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
from unittest.mock import AsyncMock

import httpx
import pytest

from src import orchestrator
from src.core.db import Database
from src.core.time_utils import ALMATY
from src.orchestrator import Event, Phase, State, transition
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
    modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert modules <= {"dataclasses", "datetime", "enum", "src.core.time_utils"}
    assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
    assert not {"now", "open", "print", "connect", "get_logger"} & {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def test_state_machine_handles_waiting_quiz_and_exam_branches():
    state = State(min_articles=3, quiz_threshold=0.6)
    for index in range(3):
        identity = str(index)
        state, _ = transition(state, event("article", f"article-{index}",
                                          article_id=identity, topic="security"))
        state, _ = transition(state, event("extracted", f"extract-{index}", article_id=identity))
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
    ready, actions = transition(ready, event("summary_written", post_id="post", status="draft"))
    assert actions[0].kind == "exam"
    exam, _ = transition(ready, event("exam_prepared"))
    assert exam.phase == Phase.EXAM
    for verdict, phase, allocation in [("pass", Phase.TOPIC_DONE, (2, 4)),
                                       ("fail", Phase.REMEDIAL, (4, 2))]:
        result, actions = transition(exam, event("graded", status=verdict))
        assert result.phase == phase
        assert (actions[0].core_articles, actions[0].adjacent_articles) == allocation
    failed, actions = transition(state, event("quiz_done", "failed-quiz", total=5, answered=2))
    assert failed.phase == Phase.WAITING and actions[0].kind == "struggle"


def test_state_machine_rejects_invalid_events_and_naive_time():
    state = State(min_articles=3, quiz_threshold=0.6)
    with pytest.raises(ValueError):
        transition(state, event("summary_written", post_id="out-of-order"))
    with pytest.raises(ValueError, match="aware"):
        Event("naive", "article", "chain", AT.replace(tzinfo=None))


@pytest.fixture
def learning_store(tmp_path):
    db = Database(tmp_path / "runner.sqlite3")
    db.initialize()
    sql = Path("docs/learning-storage.sql").read_text()
    for statement in sql.split(";"):
        if statement.strip():
            db.run_transaction(lambda c, statement=statement: c.execute(statement))
    return SQLiteLearningStore(db, State(min_articles=3, quiz_threshold=0.6))


async def test_runner_persists_atomic_intents_and_replays_without_duplicate_actions(learning_store):
    handler = AsyncMock(return_value=None)
    runner = ActionRunner(learning_store, {"extract": handler, "found": handler, "reading_timer": handler})
    incoming = event("article", article_id="one", topic="security")
    await runner.dispatch(incoming)
    reopened = SQLiteLearningStore(learning_store.database, State(min_articles=3, quiz_threshold=0.6))
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


async def test_full_article_extraction_quiz_summary_and_curator_assignment(tmp_path, monkeypatch):
    async def no_network(*args, **kwargs):
        pytest.fail("The dry run contacted an external service")
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)
    report = await dry_run(tmp_path)
    assert report["phase"] == "EXAM"
    assert report["sources"] == 3 and report["claims"] == 3
    assert report["answered_questions"] >= 5
    assert report["summary_state"] == "draft" and report["exam_questions"] == 5
    assert report["external_deliveries"] == 0
    assert report["tokenize_calls"] > 0 and report["curator_calls"] == 1
    assert report["trace_id"] == "dry-run"


def test_single_command_dry_run(tmp_path):
    result = subprocess.run([sys.executable, "-m", "src.cli", "run", "--dry-run",
                             "--workdir", str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["phase"] == "EXAM"


@pytest.mark.parametrize("name", [
    "test_timezone_everywhere", "test_no_prompt_strings_in_code", "test_outbox_idempotent",
    "test_state_machine_is_pure", "test_context_isolation_offtop", "test_context_isolation_quiz",
    "test_mood_inertia", "test_validator_rejects_cjk", "test_validator_strips_fences",
    "test_token_budget_enforced", "test_model_output_validated_before_writes",
    "test_model_tools_cannot_write", "test_empty_retriever_no_llm_call",
    "test_curator_text_uses_user_role", "test_chunk_overlap", "test_triplet_deduplication",
    "test_model_calls_use_task_queue",
])
def test_all_final_architecture_invariants_have_executable_tests(name):
    declared = {node.name for path in Path("tests").glob("test_*.py")
                for node in ast.walk(ast.parse(path.read_text()))
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert name in declared
