"""Compose the live learner and owner interfaces from approved files and providers."""

import asyncio
import json
from dataclasses import asdict
from random import Random

import structlog

from src.application import LearningApplication
from src.bot import post_buttons
from src.catalogue import Catalogue
from src.chat import ChatService
from src.chat_gateway import ChatGateway
from src.core.admission import StudyGate
from src.core.chat_context import ChatContext
from src.core.chat_inbox import ChatInbox
from src.core.chat_memory import ChatMemory
from src.core.context import ContextBuilder
from src.core.llm_vendor import ClaudeCodeBackend
from src.core.tasks import JobQueue
from src.core.time_utils import add_elapsed, from_utc_iso, now
from src.core.writing_snapshot import WritingSnapshot
from src.curator import Curator
from src.extract import Extractor
from src.life import LifeRuntime
from src.offtop import OfftopGenerator, OfftopPlanner
from src.orchestrator import Event, State
from src.pipeline import LearningPipeline
from src.providers import RuntimeProviders
from src.publish import DeliveryExpired, Destination
from src.retrieve import Retriever
from src.runner import GENERATION_ACTIONS, ActionRunner, SQLiteLearningStore
from src.scheduler import ActivityScheduler, ConfiguredRhythm, SQLiteReservations
from src.selfquiz import SelfQuiz
from src.validator import OutputValidator
from src.writer import Writer

log = structlog.get_logger("blogai.live")


class LiveApplication(LearningApplication):
    def __init__(
        self,
        runner,
        activity,
        *,
        catalogue,
        providers,
        settings,
        life=None,
        owns_jobs=False,
        inbox=None,
    ):
        super().__init__(runner, activity)
        self.catalogue, self.providers, self.settings = catalogue, providers, settings
        self.life, self.owns_jobs = life, owns_jobs
        self.inbox = inbox

    async def start(self, *, paused=False):
        await self.providers.context(self.clock())
        if self.owns_jobs:
            await self.activity.jobs.start()
        if self.life:
            await asyncio.to_thread(self.life.recover)
        if self.inbox:
            await asyncio.to_thread(self.inbox.recover)
        await super().start(paused=paused)

    async def tick(self):
        if self.settings.get("system.paused"):
            return False
        if self.inbox:
            await self.inbox.tick()
        if self.life:
            await self.life.tick()
            blocks = await self.providers.context(self.clock())
            if not blocks["day"].study_allowed:
                return False
        state = await asyncio.to_thread(self.runner.store.state)
        actions = await asyncio.to_thread(self.runner.store.actions)
        if state.phase in {"IDLE", "WAITING"} and not any(
            row["status"] in {"pending", "waiting", "running", "uncertain"}
            for row in actions
        ):
            topic = (
                state.topic
                or self.catalogue.start
                or next(iter(self.catalogue.topics), "")
            )
            candidates = state.pending_articles or tuple(
                self.catalogue.topics.get(topic, {}).get("articles", [])
            )
            source = next(
                (
                    self.catalogue.sources[key]
                    for key in candidates
                    if key in self.catalogue.sources
                    and key not in state.articles
                    and self.catalogue.sources[key].topic == topic
                ),
                None,
            )
            if source is not None:
                await self.runner.dispatch(
                    Event(
                        "article:" + source.id,
                        "article",
                        "article:" + source.id,
                        self.clock(),
                        article_id=source.id,
                        topic=source.topic,
                    )
                )
        if self.life:
            row = await asyncio.to_thread(self.runner.store.peek, self.clock())
            if row and json.loads(row["action_json"])["kind"] in GENERATION_ACTIONS:
                if not await asyncio.to_thread(self._study_post_share, self.clock()):
                    return False
        return await super().tick()

    def _study_post_share(self, at):
        start = at.replace(hour=0, minute=0, second=0, microsecond=0)
        with self.providers.life.database.connection(readonly=True) as c:
            counts = c.execute(
                "SELECT kind,count(*) AS n FROM posts WHERE published_at>=? GROUP BY "
                "kind",
                (start,),
            ).fetchall()
        everyday = sum(
            row["n"]
            for row in counts
            if row["kind"] in {"offtop", "daily", "situation"}
        )
        total = sum(row["n"] for row in counts)
        share = self.providers.life_config["publishing"]["minimum_life_share"]
        return (
            everyday / (total + 1) >= share
            if share is not None
            else everyday > total + 1 - everyday
        )

    async def close(self):
        await super().close()
        await self.activity.jobs.join()
        if self.life:
            await self.life.close()
        if self.inbox:
            await self.inbox.close()
        if self.owns_jobs:
            await self.activity.jobs.close()
        await self.providers.close()

    async def allowed(self, payload, at):
        day = (await self.providers.context(at))["day"]
        database = self.providers.life.database
        with database.connection(readonly=True) as c:
            if payload.get("chat_reply"):
                row = c.execute(
                    "SELECT activity_id,valid_until FROM chat_replies WHERE trace_id=?",
                    (payload["chat_reply"],),
                ).fetchone()
                if (
                    row
                    and row["activity_id"]
                    and (
                        row["activity_id"] != day.activity_id
                        or row["valid_until"]
                        and at >= from_utc_iso(row["valid_until"])
                    )
                ):
                    raise DeliveryExpired()
                return day.chat_allowed
            row = c.execute(
                "SELECT kind,text,context_snapshot FROM posts WHERE id=?",
                (payload["post_id"],),
            ).fetchone()
        if row is None:
            raise DeliveryExpired()
        if row["kind"] not in {"offtop", "daily", "situation"}:
            return day.study_allowed
        if row["context_snapshot"]:
            snapshot = WritingSnapshot.decode(row["context_snapshot"])
            if snapshot.day.activity_id and (
                snapshot.day.activity_id != day.activity_id
                or snapshot.day.world_action_id != day.world_action_id
                or snapshot.day.location != day.location
                or snapshot.day.activity_until
                and at >= snapshot.day.activity_until
            ):
                await asyncio.to_thread(self.life.expire, payload["post_id"], at)
                raise DeliveryExpired()
            if evidence := snapshot.payload.get("recorded_event"):
                from src.core.activity_claims import activity_conflicts, plan_evidence

                evidence = evidence | plan_evidence(database, at)
                if activity_conflicts(
                    row["text"],
                    evidence,
                    self.providers.config_dir / "activity_transitions.yaml",
                ):
                    await asyncio.to_thread(self.life.expire, payload["post_id"], at)
                    raise DeliveryExpired()
        return not day.blackout.blocked


async def assemble_live(args, service, jobs):
    database, llm, settings = service.database, service.llm, service.registry
    catalogue = Catalogue.load(args.library, allow_empty=True)
    await asyncio.to_thread(catalogue.install, database)
    providers = RuntimeProviders(database, args.config_dir, args.world_state, settings)
    try:
        context = ContextBuilder(
            args.prompt_dir,
            database=database,
            mood_model=providers.model,
            config_dir=args.config_dir,
            settings=settings,
        )
        validator = OutputValidator(llm, settings=settings)
        writer = Writer(database, llm, context, validator)
        retriever = Retriever(database, llm, settings=settings)
        extractor = Extractor(database, llm, context, grammar_dir=args.grammar_dir)
        quiz = SelfQuiz(
            database, llm, retriever, context, settings, grammar_dir=args.grammar_dir
        )
        curator = Curator(
            database,
            ClaudeCodeBackend(settings, database=database),
            args.prompt_dir,
            settings=settings,
        )
        memory = ChatMemory(
            llm, args.prompt_dir, grammar_dir=args.grammar_dir, settings=settings
        )
        chat = ChatService(
            database,
            llm,
            retriever,
            ChatContext(
                args.prompt_dir,
                providers.model,
                config_dir=args.config_dir,
                settings=settings,
            ),
            settings,
            person_id=str(service.layout.owner_id),
            summarizer=memory.summarize,
            facts_extractor=memory.facts,
            grammar_dir=args.grammar_dir,
            validator=validator,
            mood_provider=providers.mood.current,
        )
        store = SQLiteLearningStore(
            database,
            State(
                settings.get("study.min_articles"), settings.get("study.quiz_threshold")
            ),
            settings=settings,
        )

        async def chat_context():
            blocks = await providers.context(now())
            state = await asyncio.to_thread(store.state)
            return blocks | {
                "topic": state.topic or catalogue.start,
                "life_state": await asyncio.to_thread(
                    providers.life.public_state, blocks["day"].at
                ),
            }

        service.chat_gateway = ChatGateway(
            chat, service.publisher, service.layout, chat_context
        )
        inbox = ChatInbox(service.chat_gateway, providers)
        service.chat_gateway.inbox = inbox
        service.extractor = extractor

        def source_received(source):
            catalogue.sources[source.id] = source
            topic = catalogue.topics.setdefault(
                source.topic,
                dict(name=source.topic, status="pending", adjacent=[], articles=[]),
            )
            if source.id not in topic["articles"]:
                topic["articles"].append(source.id)

        service.source_received = source_received

        async def regenerate(post_id, *, trace_id):
            with structlog.contextvars.bound_contextvars(trace_id=trace_id):
                snapshot = await asyncio.to_thread(writer.snapshot, post_id)
                blocks = await providers.context(now())
                if snapshot.kind in {"offtop", "daily", "situation"}:
                    result = await writer.regenerate(post_id, **blocks)
                elif blocks["day"].study_allowed:
                    with StudyGate(
                        database, location_provider=providers.world._location
                    ).session():
                        result = await writer.regenerate(post_id, **blocks)
                else:
                    return {"status": "deferred", "reason": "home_study_required"}
                return asdict(result)

        async def settings_changed(key, *, trace_id):
            with structlog.contextvars.bound_contextvars(trace_id=trace_id):
                await providers.mood.settings_changed(key, at=now())

        async def alert(trace_id, reason):
            await asyncio.to_thread(
                service.publisher.enqueue_operation,
                f"{trace_id}:alert:{reason}",
                Destination("owner", "ops", service.layout.owner_id),
                trace_id=trace_id,
                method="message",
                text=reason,
            )

        async def on_draft(result, *, trace_id):
            await asyncio.to_thread(
                service.publisher.enqueue_post,
                result.id,
                service.layout.publication_destinations(),
                trace_id=trace_id,
                markup=post_buttons(result.id, published=True),
            )

        async def on_curator(phase, receipt, *, trace_id):
            await asyncio.to_thread(
                service.publisher.enqueue_operation,
                f"{receipt['trace_id']}:{phase}:curator",
                service.layout.destination("curator"),
                trace_id=trace_id,
                method="message",
                text=receipt["result"]["public_comment"],
            )

        service.regenerator, service.settings_changed = regenerate, settings_changed
        rhythm = ConfiguredRhythm(args.config_dir / "rhythm.yaml", settings)
        rng = Random()
        pipeline = LearningPipeline(
            database,
            catalogue.sources,
            extractor,
            quiz,
            writer,
            curator,
            context_provider=providers.context,
            rhythm=rhythm,
            rng=rng,
            on_draft=on_draft,
            alert=alert,
            topics_map=catalogue.topics,
            on_curator=on_curator,
        )
        runner = ActionRunner(
            store,
            pipeline.handlers,
            alert=alert,
            generation_delay=lambda at: add_elapsed(
                at, minutes=rhythm.gap_minutes(rng)
            ),
            study_gate=StudyGate(database, location_provider=providers.world._location),
        )
        activity = ActivityScheduler(
            rhythm,
            JobQueue(),
            blackout=providers.blackout,
            reservations=SQLiteReservations(database),
        )
        return LiveApplication(
            runner,
            activity,
            catalogue=catalogue,
            providers=providers,
            settings=settings,
            owns_jobs=True,
            inbox=inbox,
            life=LifeRuntime(
                providers,
                OfftopGenerator(
                    OfftopPlanner.from_config(
                        database, args.config_dir, settings=settings
                    ),
                    providers.world.world,
                    writer,
                    providers.weather,
                ),
                service.publisher,
                service.layout.publication_destinations(),
                weather_enabled=True,
                log_destination=service.layout.destination("machine"),
                state_destination=service.layout.destination("state")
                if "state" in service.layout.topics
                else None,
            ),
        )
    except BaseException:
        await providers.close()
        raise
