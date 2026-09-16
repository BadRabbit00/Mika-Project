"""Offline application exercise with real components and explicit service fixtures."""

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from random import Random

import httpx

from src.core.context import ContextBuilder
from src.core.db import Database
from src.core.llm_local import LocalLLM
from src.core.llm_vendor import VendorResult
from src.core.mood import MoodModel
from src.core.pad import Mood
from src.core.schedule import SleepWindow
from src.core.tasks import JobQueue
from src.core.time_utils import add_elapsed, from_utc_iso, require_aware
from src.core.world import World
from src.curator import Curator
from src.extract import Extractor
from src.ingest import read_source
from src.orchestrator import Event, State
from src.pipeline import LearningPipeline
from src.retrieve import RetrievalPolicy, Retriever
from src.runner import ActionRunner, SQLiteLearningStore
from src.scheduler import ActivityScheduler, Rhythm, SQLiteReservations
from src.selfquiz import QuizSettings, SelfQuiz
from src.validator import OutputValidator
from src.writer import Writer


class FixtureServer:
    """A reversible artificial tokenizer and deterministic HTTP service fixture."""

    def __init__(self, scenario):
        self.scenario = scenario
        self.tokens, self.pieces, self.tokenize_calls = {}, {}, 0
        self.output = ""
        self.names = sorted(
            {
                article["claim"][field]
                for article in scenario["articles"]
                for field in ("src", "dst")
            }
        )

    def encode(self, text):
        result = []
        for piece in re.findall(r"\s+|\w+|[^\w\s]", text):
            if piece not in self.tokens:
                token = len(self.tokens) + 1
                self.tokens[piece], self.pieces[token] = token, piece
            result.append(self.tokens[piece])
        return result

    def handle(self, request):
        body = json.loads(request.content) if request.content else {}
        match request.url.path:
            case "/tokenize":
                self.tokenize_calls += 1
                data = {"tokens": self.encode(body["content"])}
            case "/detokenize":
                data = {
                    "content": "".join(self.pieces[token] for token in body["tokens"])
                }
            case "/apply-template":
                data = {"prompt": json.dumps(body["messages"], ensure_ascii=False)}
            case "/completion":
                data = {"content": self.output, "stopped_limit": False}
            case "/v1/models":
                data = {"data": [{"id": "dry-run-fixture"}]}
            case "/v1/embeddings":
                text = body["input"].casefold()
                index = next(
                    (index for index, name in enumerate(self.names) if name in text),
                    len(self.names),
                )
                vector = [
                    float(index == position) for position in range(len(self.names) + 1)
                ]
                data = {"data": [{"embedding": vector}]}
            case _:
                raise AssertionError(f"Unexpected fixture endpoint: {request.url.path}")
        return httpx.Response(200, json=data)


class FixtureLLM(LocalLLM):
    def __init__(self, server):
        super().__init__(transport=httpx.MockTransport(server.handle))
        self.server = server

    async def generate(self, request, **kwargs):
        scenario = self.server.scenario
        match request.profile:
            case "extract":
                article = next(
                    article
                    for article in scenario["articles"]
                    if article["text"] in request.user
                )
                output = json.dumps(article["claim"]) + "\n"
            case "selfquiz_ask":
                output = "\n".join(scenario["questions"])
            case "selfquiz_answer":
                ids = re.findall(r'"id":\s*"([^\"]+)"', request.user)
                output = json.dumps(
                    {"answer": scenario["answer"], "cited": ids[:1], "confident": True}
                )
            case "write_tech":
                output = (
                    f"<{request.mode}>"
                    + scenario["bodies"][request.mode]
                    + f"</{request.mode}>"
                )
            case _:
                raise AssertionError(f"Unexpected fixture profile: {request.profile}")
        self.server.output = output
        return await super().generate(request, **kwargs)


class FixtureCurator:
    def __init__(self, scenario):
        self.scenario, self.calls = scenario, 0

    def ask(self, system, user, schema, *, trace_id):
        self.calls += 1
        value = schema.model_validate(
            {
                "questions": [
                    {
                        "q": question,
                        "expected": self.scenario["answer"],
                        "key_facts": [self.scenario["answer"]],
                    }
                    for question in self.scenario["questions"]
                ],
                "graph_corrections": [],
                "trust_notes": [],
                "public_comment": self.scenario["curator_comment"],
            }
        )
        return VendorResult(value, 0.0, 1, ("dry-run-curator",))


@dataclass
class VirtualClock:
    at: datetime

    def __post_init__(self):
        self.at = require_aware(self.at)

    def __call__(self):
        return self.at


async def dry_run(workdir):
    """Use a new isolated database, virtual time, and mocked external boundaries."""
    root, workdir = Path.cwd(), Path(workdir)
    scenario = json.loads((root / "fixtures/dry_run/scenario.json").read_text())
    database_path = workdir / "dry-run.sqlite3"
    if database_path.exists():
        raise ValueError("Dry runs require a fresh work directory")
    workdir.mkdir(parents=True, exist_ok=True)
    database = Database(database_path)
    await asyncio.to_thread(database.initialize)
    sources = {}
    for article in scenario["articles"]:
        path = workdir / (article["id"] + ".md")
        metadata = {
            "id": article["id"],
            "title": article["title"],
            "topic": "security",
            "origin_key": "fixture:" + article["id"],
            "given_by": "dry-run",
        }
        path.write_text("---\n" + json.dumps(metadata) + "\n---\n" + article["text"])
        sources[article["id"]] = read_source(path)
    clock = VirtualClock(from_utc_iso(scenario["at"]))
    world = World.from_config(root / "config")
    mood_model = MoodModel.from_config(root / "config", epoch=clock.at)

    def day(at):
        return world.day_context(
            at,
            sleep=SleepWindow(
                at.replace(hour=1, minute=0), at.replace(hour=9, minute=0)
            ),
            sleep_debt=0,
            location="дом",
            road_roll=0.99,
        )

    async def context_provider(at):
        return {"day": day(at), "mood": Mood(0, 0, 0), "wake_reason": "fixture"}

    settings = QuizSettings.from_registry(root / "config/settings.yaml")
    store = SQLiteLearningStore(
        database, State(min_articles=3, quiz_threshold=settings.threshold)
    )
    rhythm, rng, jobs = Rhythm.from_mapping(scenario["rhythm"]), Random(7), JobQueue()
    scheduler = ActivityScheduler(
        rhythm,
        jobs,
        blackout=lambda at: day(at).blackout,
        clock=clock,
        reservations=SQLiteReservations(database),
    )
    server, backend = FixtureServer(scenario), FixtureCurator(scenario)
    async with FixtureLLM(server) as llm:
        context = ContextBuilder(
            root / "prompts",
            database=database,
            mood_model=mood_model,
            config_dir=root / "config",
        )
        extractor = Extractor(database, llm, context, grammar_dir=root / "grammars")
        quiz = SelfQuiz(
            database,
            llm,
            Retriever(database, llm, RetrievalPolicy(0.55, 60)),
            context,
            settings,
            grammar_dir=root / "grammars",
        )
        writer = Writer(
            database,
            llm,
            context,
            OutputValidator(llm),
        )
        pipeline = LearningPipeline(
            database,
            sources,
            extractor,
            quiz,
            writer,
            Curator(database, backend, root / "prompts"),
            context_provider=context_provider,
            rhythm=rhythm,
            rng=rng,
        )
        runner = ActionRunner(store, pipeline.handlers)
        await jobs.start()
        scheduler.start(paused=True)
        try:
            for source in sources.values():
                await runner.dispatch(
                    Event(
                        "article:" + source.id,
                        "article",
                        "dry-run",
                        clock.at,
                        article_id=source.id,
                        topic=source.topic,
                    )
                )
                for _ in range(300):
                    if store.state().phase == "EXAM":
                        break
                    pending = [
                        row
                        for row in store.actions()
                        if row["status"] in {"pending", "waiting"}
                    ]
                    if not pending:
                        break
                    clock.at = max(clock.at, from_utc_iso(pending[0]["due_at"]))
                    row = store.peek(clock.at)
                    if row is None:
                        raise RuntimeError("Dry run stalled on a paused action")
                    action = json.loads(row["action_json"])
                    if action["kind"] in {"found", "impression", "struggle", "summary"}:
                        results = []

                        async def execute(identity=row["id"], outcomes=results):
                            outcomes.append(
                                await runner.run_once(at=clock.at, identity=identity)
                            )

                        admitted = await scheduler.enqueue(
                            row["id"], execute, trace_id="dry-run"
                        )
                        await jobs.join()
                        if not admitted or not results:
                            window = rhythm.window(clock.at)
                            clock.at = rhythm.next_window(
                                window.end if window else clock.at
                            ).start
                            continue
                        clock.at = add_elapsed(
                            clock.at, minutes=rhythm.gap_minutes(rng)
                        )
                    else:
                        await runner.run_once(at=clock.at)
                    if any(row["status"] == "failed" for row in store.actions()):
                        raise RuntimeError("A dry-run action failed")
                else:
                    raise RuntimeError("Dry-run iteration bound exceeded")
        finally:
            await scheduler.close()
            await jobs.close()
    with database.connection() as c:

        def count(table):
            return c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

        report = {
            "phase": store.state().phase,
            "trace_id": "dry-run",
            "sources": count("sources"),
            "claims": count("claims"),
            "killed_posts": c.execute(
                "SELECT count(*) FROM posts WHERE state='killed'"
            ).fetchone()[0],
            "exam_questions": count("exams"),
            "answered_questions": c.execute(
                "SELECT count(*) FROM questions WHERE verdict='answered'"
            ).fetchone()[0],
            "summary_state": c.execute(
                "SELECT state FROM posts WHERE kind='summary'"
            ).fetchone()[0],
            "external_deliveries": 0,
            "tokenize_calls": server.tokenize_calls,
            "curator_calls": backend.calls,
            "database": str(database_path),
        }
    return report
