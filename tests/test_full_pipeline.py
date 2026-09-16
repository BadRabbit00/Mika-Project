"""Final architecture invariants and executable application composition."""

import ast
import copy
import inspect
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from src import orchestrator
from src.core.db import Database
from src.core.time_utils import ALMATY
from src.orchestrator import Event, Phase, State, transition
from src.pipeline import LearningPipeline
from src.runner import ActionRunner, SQLiteLearningStore
from src.runtime import dry_run

AT = datetime(2026, 9, 16, 19, 30, tzinfo=ALMATY)


def event(kind, identity=None, **kwargs):
    return Event(identity or kind, kind, "chain", AT, **kwargs)


def test_state_machine_is_pure(monkeypatch):
    state = State(min_articles=3, quiz_threshold=0.6)
    incoming = event("article", article_id="one", topic="security")
    before = copy.deepcopy((state, incoming))

    def forbidden(*args, **kwargs):
        pytest.fail("The transition function performed a side effect")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    first, second = transition(state, incoming), transition(state, incoming)
    assert first == second and (state, incoming) == before
    assert first[0].phase == Phase.ANNOUNCED
    assert {action.kind for action in first[1]} == {"extract", "found", "reading_timer"}
    tree = ast.parse(inspect.getsource(orchestrator))
    modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert modules <= {"dataclasses", "datetime", "enum", "src.core.time_utils"}
    assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
    assert not {"now", "open", "print", "connect", "get_logger"} & {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def test_state_machine_handles_waiting_quiz_and_exam_branches():
    state = State(min_articles=3, quiz_threshold=0.6)
    for index in range(3):
        identity = str(index)
        state, _ = transition(
            state,
            event("article", f"article-{index}", article_id=identity, topic="security"),
        )
        state, _ = transition(
            state, event("extracted", f"extract-{index}", article_id=identity)
        )
        assert state.phase == Phase.ANNOUNCED
        state, actions = transition(state, event("reading_due", f"read-{index}"))
        assert state.phase == Phase.READING
        state, _ = transition(state, event("ingested", f"ingest-{index}"))
        state, _ = transition(state, event("assess", f"assess-{index}"))
        assert state.phase == (Phase.WAITING if index < 2 else Phase.SELF_QUIZ)
    quiz = event("quiz_done", total=5, answered=3)
    ready, actions = transition(state, quiz)
    assert ready.phase == Phase.SUMMARY and actions[0].kind == "summary"
    assert transition(ready, quiz) == (ready, [])
    ready, actions = transition(
        ready, event("summary_written", post_id="post", status="draft")
    )
    assert actions[0].kind == "exam"
    exam, _ = transition(ready, event("exam_prepared"))
    assert exam.phase == Phase.EXAM
    for verdict, phase, allocation in [
        ("pass", Phase.TOPIC_DONE, (2, 4)),
        ("fail", Phase.REMEDIAL, (4, 2)),
    ]:
        result, actions = transition(exam, event("graded", status=verdict))
        assert result.phase == phase
        assert (actions[0].core_articles, actions[0].adjacent_articles) == allocation
        if verdict == "pass":
            result, actions = transition(
                result,
                event(
                    "articles_selected",
                    next_topic="networking",
                    article_ids=("next",),
                ),
            )
            assert result.phase == Phase.IDLE and result.articles == ()
            assert result.pending_articles == ("next",)
            assert actions[0].kind == "announce_topic"
        else:
            result, actions = transition(
                result,
                event(
                    "remediation_ingested",
                    article_ids=("extra",),
                ),
            )
            assert result.phase == Phase.INGESTED and "extra" in result.articles
            assert actions[0].kind == "assess"
    failed, actions = transition(
        state, event("quiz_done", "failed-quiz", total=5, answered=2)
    )
    assert failed.phase == Phase.WAITING and actions[0].kind == "struggle"


def test_state_machine_rejects_invalid_events_and_naive_time():
    state = State(min_articles=3, quiz_threshold=0.6)
    with pytest.raises(ValueError):
        transition(state, event("summary_written", post_id="out-of-order"))
    with pytest.raises(ValueError, match="aware"):
        Event("naive", "article", "chain", AT.replace(tzinfo=None))


async def test_pipeline_grading_uses_only_validated_graph_answers(learning_store):
    from src.orchestrator import Action

    db = learning_store.database
    db.run_transaction(
        lambda c: c.executemany(
            "INSERT INTO exams(id,topic,question,at) VALUES (?,'security',?,?)",
            [(1, "Known question", AT), (2, "Unknown question", AT)],
        )
    )
    curator = SimpleNamespace(
        _replay=lambda phase, trace: {"exam_ids": [1, 2]} if phase == "exam" else None,
        grade=AsyncMock(return_value={"result": {"overall": "fail"}}),
    )
    quiz = SimpleNamespace(
        answer=AsyncMock(
            side_effect=[
                SimpleNamespace(verdict="answered", answer="Supported by the graph"),
                SimpleNamespace(
                    verdict="invalid_citation", answer="Unverified content"
                ),
            ]
        )
    )
    pipeline = LearningPipeline(
        db,
        {},
        None,
        quiz,
        None,
        curator,
        context_provider=None,
        rhythm=None,
        rng=None,
        pass_rule="fixture policy",
    )
    action = Action("grade", "chain", "security", exam_trace_id="exam-one")
    result = await pipeline.handlers["grade"](action, AT)
    assert result.kind == "graded" and result.status == "fail"
    assert curator.grade.await_args.kwargs["answers"] == {
        1: "Supported by the graph",
        2: "",
    }
    await pipeline.handlers["grade"](action, AT)
    assert quiz.answer.await_count == 2
    pipeline.pass_rule = None
    curator.pass_rule = lambda: "Configured file policy"
    await pipeline.handlers["grade"](action, AT)
    assert curator.grade.await_args.kwargs["pass_rule"] == "Configured file policy"


@pytest.mark.parametrize("passed", [True, False])
async def test_pipeline_selection_binds_validated_allocation(learning_store, passed):
    from src.ingest import Source
    from src.orchestrator import Action

    sources = {
        key: Source(key, Path(f"{key}.md"), key, topic, "Fixture article")
        for key, topic in [("core", "security"), ("next", "networking")]
    }
    receipt = {
        "result": {
            "articles": [
                {"id": "core", "kind": "core"},
                {"id": "next", "kind": "adjacent"},
            ],
            "topic_switch": passed,
            "next_topic": "networking" if passed else None,
            "shortage": None,
        }
    }
    curator = SimpleNamespace(select_articles=AsyncMock(return_value=receipt))
    extractor = SimpleNamespace(extract=AsyncMock())
    pipeline = LearningPipeline(
        learning_store.database,
        sources,
        extractor,
        None,
        None,
        curator,
        context_provider=None,
        rhythm=None,
        rng=None,
        topics_map={"security": ["networking"]},
    )
    action = Action(
        "select_articles",
        "chain",
        "security",
        exam_trace_id="exam-one",
        core_articles=2 if passed else 4,
        adjacent_articles=4 if passed else 2,
    )
    result = await pipeline.handlers["select_articles"](action, AT)
    assert curator.select_articles.await_args.kwargs["exam_result"] == (
        "pass" if passed else "fail"
    )
    if passed:
        assert result.kind == "articles_selected" and result.next_topic == "networking"
        assert result.article_ids == ("core", "next")
        extractor.extract.assert_not_awaited()
    else:
        assert result.kind == "remediation_ingested" and result.article_ids == ("core",)
        assert extractor.extract.await_count == 2


@pytest.fixture
def learning_store(tmp_path):
    db = Database(tmp_path / "runner.sqlite3")
    db.initialize()
    return SQLiteLearningStore(db, State(min_articles=3, quiz_threshold=0.6))


async def test_runner_persists_atomic_intents_and_replays_without_duplicate_actions(
    learning_store,
):
    handler = AsyncMock(return_value=None)
    runner = ActionRunner(
        learning_store, {"extract": handler, "found": handler, "reading_timer": handler}
    )
    incoming = event("article", article_id="one", topic="security")
    await runner.dispatch(incoming)
    reopened = SQLiteLearningStore(
        learning_store.database, State(min_articles=3, quiz_threshold=0.6)
    )
    assert reopened.state().phase == Phase.ANNOUNCED
    await runner.dispatch(incoming)
    assert len(reopened.actions()) == 3
    await runner.run_once(at=AT)
    assert handler.await_count == 1
    again = ActionRunner(reopened, runner.handlers)
    await again.run_once(at=AT)
    assert handler.await_count == 2
    with pytest.raises(ValueError, match="identity"):
        await runner.dispatch(event("article", article_id="changed", topic="security"))


async def test_curator_failure_is_deferred_six_hours_three_times(learning_store):
    from dataclasses import asdict

    state = State(
        min_articles=3,
        quiz_threshold=0.6,
        phase=Phase.SUMMARY,
        topic="security",
        post_id="summary",
    )
    learning_store.database.run_transaction(
        lambda c: c.execute(
            "UPDATE learner_state SET state_json=?", (json.dumps(asdict(state)),)
        )
    )
    runner = ActionRunner(
        learning_store, {"exam": AsyncMock(side_effect=TimeoutError("offline"))}
    )
    await runner.dispatch(event("summary_written", status="draft", post_id="summary"))
    for attempt in range(3):
        at = AT + timedelta(hours=6 * attempt)
        assert await runner.run_once(at=at) == "retry"
        assert await runner.run_once(at=at + timedelta(hours=5)) == "idle"
    assert await runner.run_once(at=AT + timedelta(hours=18)) == "failed"
    assert learning_store.actions()[0]["status"] == "failed"


async def test_action_dependencies_and_generation_gap_survive_reopen(learning_store):
    runner = ActionRunner(
        learning_store,
        {"extract": AsyncMock(), "found": AsyncMock()},
        generation_delay=lambda at: at + timedelta(minutes=12),
    )
    runner.handlers["extract"].return_value = None
    runner.handlers["found"].return_value = None
    await runner.dispatch(event("article", article_id="one", topic="security"))
    assert learning_store.claim(AT, "article:001") is None
    assert await runner.run_once(at=AT) == "completed"
    assert await runner.run_once(at=AT) == "completed"
    with learning_store.database.connection() as c:
        from src.core.time_utils import from_utc_iso

        assert from_utc_iso(
            c.execute("SELECT generation_after FROM learner_state").fetchone()[0]
        ) == AT + timedelta(minutes=12)


async def test_application_tick_admits_real_actions_through_queue(learning_store):
    from src.application import LearningApplication
    from src.core.schedule import Blackout
    from src.core.tasks import JobQueue
    from src.scheduler import ActivityScheduler, Rhythm

    scenario = json.loads(Path("fixtures/dry_run/scenario.json").read_text())
    jobs, handler = JobQueue(), AsyncMock(return_value=None)
    runner = ActionRunner(learning_store, {"extract": handler})
    activity = ActivityScheduler(
        Rhythm.from_mapping(scenario["rhythm"]),
        jobs,
        blackout=lambda at: Blackout(False),
        clock=lambda: AT,
    )
    app = LearningApplication(runner, activity, clock=lambda: AT)
    await runner.dispatch(event("article", article_id="one", topic="security"))
    await jobs.start()
    await app.start(paused=True)
    try:
        assert await app.tick()
        await jobs.join()
        handler.assert_awaited_once()
    finally:
        await app.close()
        await jobs.close()


async def test_local_service_failure_pauses_actions_for_fifteen_minutes(learning_store):
    runner = ActionRunner(
        learning_store,
        {"extract": AsyncMock(side_effect=httpx.ConnectError("offline"))},
    )
    await runner.dispatch(event("article", article_id="one", topic="security"))
    assert await runner.run_once(at=AT) == "retry"
    assert await runner.run_once(at=AT + timedelta(minutes=14)) == "idle"
    assert learning_store.peek(AT + timedelta(minutes=15)) is not None


def test_interrupted_actions_remain_visible_after_restart(learning_store):
    learning_store.dispatch(event("article", article_id="one", topic="security"))
    claimed = learning_store.claim(AT)
    assert learning_store.recover_interrupted() == 1
    assert (
        next(row for row in learning_store.actions() if row["id"] == claimed["id"])[
            "status"
        ]
        == "uncertain"
    )
    assert learning_store.state().phase == Phase.ANNOUNCED


async def test_full_article_extraction_quiz_summary_and_curator_assignment(
    tmp_path, monkeypatch
):
    from src.core import db as db_module

    async def no_network(*args, **kwargs):
        pytest.fail("The dry run contacted an external service")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)
    database_log = Mock(wraps=db_module.log)
    monkeypatch.setattr(db_module, "log", database_log)
    report = await dry_run(tmp_path)
    assert report["phase"] == "EXAM"
    assert report["sources"] == 3 and report["claims"] == 3
    assert report["answered_questions"] >= 5
    assert report["summary_state"] == "draft" and report["exam_questions"] == 5
    assert report["killed_posts"] == 0
    assert report["external_deliveries"] == 0
    assert report["tokenize_calls"] > 0 and report["curator_calls"] == 1
    assert report["trace_id"] == "dry-run"
    assert not any(
        call.args[0] == "db_unfinished_transaction_rolled_back"
        for call in database_log.warning.call_args_list
    )


def test_single_command_dry_run(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.cli",
            "run",
            "--dry-run",
            "--workdir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["phase"] == "EXAM"


@pytest.mark.parametrize(
    "name",
    [
        "test_timezone_everywhere",
        "test_no_prompt_strings_in_code",
        "test_outbox_idempotent",
        "test_state_machine_is_pure",
        "test_context_isolation_offtop",
        "test_context_isolation_quiz",
        "test_mood_inertia",
        "test_validator_rejects_cjk",
        "test_validator_strips_fences",
        "test_token_budget_enforced",
        "test_model_output_validated_before_writes",
        "test_model_tools_cannot_write",
        "test_empty_retriever_no_llm_call",
        "test_curator_text_uses_user_role",
        "test_chunk_overlap",
        "test_triplet_deduplication",
        "test_model_calls_use_task_queue",
    ],
)
def test_all_final_architecture_invariants_have_executable_tests(name):
    declared = {
        node.name
        for path in Path("tests").glob("test_*.py")
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert name in declared


@pytest.mark.parametrize("category", ["limit", "auth", "unknown"])
async def test_curator_error_policy_persists_without_global_pause(
    learning_store, category
):
    from dataclasses import asdict

    from src.core.llm_vendor import CuratorFailure
    from src.core.time_utils import from_utc_iso

    state = State(3, 0.6, phase=Phase.SUMMARY, topic="security", post_id="summary")
    learning_store.database.run_transaction(
        lambda c: c.execute(
            "UPDATE learner_state SET state_json=?",
            (json.dumps(asdict(state)),),
        )
    )
    alert = AsyncMock()
    runner = ActionRunner(
        learning_store,
        {"exam": AsyncMock(side_effect=CuratorFailure(category, "Fixture failure"))},
        alert=alert,
    )
    await runner.dispatch(event("summary_written", status="draft", post_id="summary"))
    assert await runner.run_once(at=AT) == ("failed" if category == "auth" else "retry")
    with learning_store.database.connection() as c:
        row = c.execute("SELECT * FROM learner_state").fetchone()
        assert row["paused_until"] is None
        if category == "limit":
            due = from_utc_iso(row["curator_paused_until"])
            assert (
                due.hour == due.minute == 0
                and due.date() == (AT + timedelta(days=1)).date()
            )
        if category == "auth":
            assert row["curator_auth_failed"] == 1
    alert.assert_awaited_once()


def test_topic_catalogue_requires_real_first_topic_articles(tmp_path):
    import shutil

    from src.catalogue import Catalogue

    shutil.copy("library/topics.yaml", tmp_path / "topics.yaml")
    with pytest.raises(ValueError, match="ab-01"):
        Catalogue.load(tmp_path)


async def test_runtime_providers_use_explicit_state_and_persist_sleep_once(tmp_path):
    from src.core.db import Database
    from src.core.settings import SettingsRegistry, SQLiteSettingsStore
    from src.core.time_utils import to_utc_iso
    from src.providers import RuntimeProviders

    db = Database(tmp_path / "providers.sqlite3")
    db.initialize()
    inputs = tmp_path / "world.json"
    inputs.write_text(
        json.dumps(
            dict(
                location="дом",
                observed_at=to_utc_iso(AT),
                valid_until=to_utc_iso(AT + timedelta(hours=1)),
                road_roll=0.99,
                initial_mood=dict(P=0, A=0, D=0),
                initial_mood_at=to_utc_iso(AT),
                initial_sleep_debt=0,
                sleep=[
                    dict(
                        bedtime=to_utc_iso(AT.replace(hour=1)),
                        wake=to_utc_iso(AT.replace(hour=9)),
                        planned_bedtime=to_utc_iso(AT.replace(hour=1)),
                        reason="alarm",
                    )
                ],
            )
        )
    )
    settings = SettingsRegistry.from_file(
        Path("config/settings.yaml"), store=SQLiteSettingsStore(db)
    )
    providers = RuntimeProviders(db, Path("config"), inputs, settings)
    try:
        first = await providers.context(AT)
        second = await providers.context(AT)
        assert first == second and first["day"].location == "дом"
        assert first["day"].at.tzinfo == ALMATY
        with db.connection() as c:
            assert (
                c.execute(
                    "SELECT count(*) FROM sleep_log WHERE debt_applied=1 "
                    "AND origin='override'"
                ).fetchone()[0]
                == 1
            )
        automatic = await providers.context(AT + timedelta(hours=2))
        assert automatic["day"].at == AT + timedelta(hours=2)
    finally:
        await providers.close()


async def test_live_composition_builds_all_providers_without_external_calls(
    tmp_path, monkeypatch
):
    from src.catalogue import Catalogue
    from src.core.mood import MoodModel
    from src.core.schedule import Blackout
    from src.core.settings import SettingsRegistry, SQLiteSettingsStore
    from src.core.tasks import JobQueue
    from src.core.telegram import TelegramLayout
    from src.live import assemble_live
    from src.providers import RuntimeProviders
    from src.publish import Publisher

    db = Database(tmp_path / "live.sqlite3")
    db.initialize()
    settings = SettingsRegistry.from_file(
        Path("config/settings.yaml"), store=SQLiteSettingsStore(db)
    )
    catalogue = Catalogue(
        "security",
        {
            "security": {
                "name": "security",
                "status": "active",
                "adjacent": [],
                "articles": [],
            }
        },
        {},
    )
    providers = SimpleNamespace(
        model=MoodModel.from_config(Path("config")),
        context=AsyncMock(return_value={}),
        blackout=AsyncMock(return_value=Blackout(False)),
        mood=SimpleNamespace(current=AsyncMock(), settings_changed=AsyncMock()),
        close=AsyncMock(),
    )
    monkeypatch.setattr(Catalogue, "load", lambda _: catalogue)
    monkeypatch.setattr(
        RuntimeProviders,
        "__init__",
        lambda self, *args: self.__dict__.update(providers.__dict__),
    )
    service = SimpleNamespace(
        database=db,
        llm=AsyncMock(),
        publisher=Publisher(db),
        layout=TelegramLayout(
            owner_id=123,
            group_id=-100123,
            topics=dict(
                diary=11,
                author=12,
                curator=13,
                chat=14,
                library=15,
                machine=16,
                control=17,
            ),
        ),
        registry=settings,
    )
    args = SimpleNamespace(
        library=tmp_path,
        config_dir=Path("config"),
        prompt_dir=Path("prompts"),
        grammar_dir=Path("grammars"),
        world_state=tmp_path / "world.json",
    )
    app = await assemble_live(args, service, JobQueue())
    assert service.chat_gateway.service is not None
    assert service.regenerator is not None
    assert set(app.runner.handlers) >= {
        "extract",
        "quiz",
        "summary",
        "exam",
        "grade",
        "select_articles",
    }
    service.llm.generate.assert_not_awaited()
    await app.close()


async def test_live_shutdown_drains_jobs_before_closing_providers():
    from src.core.tasks import JobQueue
    from src.live import LiveApplication

    jobs = JobQueue()
    order = []

    async def work():
        order.append("job")

    async def close_providers():
        order.append("providers")

    activity = SimpleNamespace(jobs=jobs, close=AsyncMock())
    app = LiveApplication(
        None,
        activity,
        catalogue=None,
        providers=SimpleNamespace(close=close_providers),
        settings=None,
    )
    await jobs.start()
    jobs.submit("shutdown", "fixture", work)
    try:
        await app.close()
    finally:
        await jobs.close()
    assert order == ["job", "providers"]
