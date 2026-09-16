"""Compose the live learner and owner interfaces from approved files and providers."""

import asyncio
from dataclasses import asdict
from random import Random

import structlog

from src.application import LearningApplication
from src.bot import post_buttons
from src.catalogue import Catalogue
from src.chat import ChatService
from src.chat_gateway import ChatGateway
from src.core.chat_context import ChatContext
from src.core.chat_memory import ChatMemory
from src.core.context import ContextBuilder
from src.core.llm_vendor import ClaudeCodeBackend
from src.core.time_utils import add_elapsed, now
from src.curator import Curator
from src.extract import Extractor
from src.orchestrator import Event, State
from src.pipeline import LearningPipeline
from src.providers import RuntimeProviders
from src.publish import Destination
from src.retrieve import Retriever
from src.runner import ActionRunner, SQLiteLearningStore
from src.scheduler import ActivityScheduler, ConfiguredRhythm, SQLiteReservations
from src.selfquiz import SelfQuiz
from src.validator import OutputValidator
from src.writer import Writer

log = structlog.get_logger("blogai.live")


class LiveApplication(LearningApplication):
    def __init__(self, runner, activity, *, catalogue, providers, settings):
        super().__init__(runner, activity)
        self.catalogue, self.providers, self.settings = catalogue, providers, settings

    async def start(self, *, paused=False):
        await self.providers.context(self.clock())
        await super().start(paused=paused)

    async def tick(self):
        if self.settings.get("system.paused"):
            return False
        state = await asyncio.to_thread(self.runner.store.state)
        actions = await asyncio.to_thread(self.runner.store.actions)
        if state.phase in {"IDLE", "WAITING"} and not any(
            row["status"] in {"pending", "waiting", "running", "uncertain"}
            for row in actions
        ):
            topic = state.topic or self.catalogue.start
            candidates = state.pending_articles or tuple(
                self.catalogue.topics[topic]["articles"]
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
        return await super().tick()

    async def close(self):
        await super().close()
        await self.activity.jobs.join()
        await self.providers.close()


async def assemble_live(args, service, jobs):
    database, llm, settings = service.database, service.llm, service.registry
    catalogue = Catalogue.load(args.library)
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
            return blocks | {"topic": state.topic or catalogue.start}

        service.chat_gateway = ChatGateway(
            chat, service.publisher, service.layout, chat_context
        )
        service.extractor = extractor

        async def regenerate(post_id, *, trace_id):
            with structlog.contextvars.bound_contextvars(trace_id=trace_id):
                result = await writer.regenerate(
                    post_id, **(await providers.context(now()))
                )
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
        )
        activity = ActivityScheduler(
            rhythm,
            jobs,
            blackout=providers.blackout,
            reservations=SQLiteReservations(database),
        )
        return LiveApplication(
            runner,
            activity,
            catalogue=catalogue,
            providers=providers,
            settings=settings,
        )
    except BaseException:
        await providers.close()
        raise
