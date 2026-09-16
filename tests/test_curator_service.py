"""File-backed curator context and validated persistence contracts."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from src.core.db import Database
from src.core.llm_vendor import VendorResult
from src.curator import Curator, ExamResult, GradeResult, SelectionResult


@pytest.fixture
def service(tmp_path):
    db = Database(tmp_path / "curator.sqlite3")
    db.initialize()
    db.run_transaction(
        lambda c: c.execute(
            "INSERT INTO sources(id, path, title, topic, trust_prior) "
            "VALUES ('article', 'article.md', 'Article', 'topic', 0.7)"
        )
    )
    return Curator(db, Mock(), Path("prompts"))


def exam(**changes):
    return ExamResult.model_validate(
        {
            "questions": [
                {"q": f"Question {i}", "expected": "Expected", "key_facts": ["Fact"]}
                for i in range(5)
            ],
            "graph_corrections": [],
            "trust_notes": [{"source": "article", "comment": "Supplied trust."}],
            "public_comment": "A detailed assessment of the current understanding. "
            * 8,
        }
        | changes
    )


async def test_curator_validates_references_before_writing(service):
    service.backend.ask.return_value = VendorResult(
        exam(trust_notes=[{"source": "invented", "comment": "Unknown"}]),
        0.1,
        1,
        ("call",),
    )
    with pytest.raises(ValueError, match="source"):
        await service.prepare_exam(
            "topic", summary_post="Summary", given=1, read=1, n=5, trace_id="trace"
        )
    with service.database.connection() as c:
        assert c.execute("SELECT count(*) FROM exams").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM curator_log").fetchone()[0] == 0


async def test_curator_exam_is_replayable_and_logged_before_publication(service):
    service.backend.ask.return_value = VendorResult(exam(), 0.1, 1, ("call",))
    first = await service.prepare_exam(
        "topic", summary_post="Summary", given=1, read=1, n=5, trace_id="trace"
    )
    second = await service.prepare_exam(
        "topic", summary_post="Summary", given=1, read=1, n=5, trace_id="trace"
    )
    assert first == second and len(first["exam_ids"]) == 5
    service.backend.ask.assert_called_once()
    system, user, schema = service.backend.ask.call_args.args
    assert "Summary" in user and "0.7" in user and "Summary" not in system
    with service.database.connection() as c:
        assert c.execute("SELECT count(*) FROM curator_log").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0


async def test_curator_selection_cannot_invent_library_articles(service):
    result = SelectionResult.model_validate(
        {
            "articles": [{"id": "unknown", "why": "Reason", "kind": "core"}],
            "next_topic": None,
            "topic_switch": False,
            "shortage": "Missing material",
            "public_comment": "The library needs more material.",
        }
    )
    service.backend.ask.return_value = VendorResult(result, 0.1, 1, ("call",))
    with pytest.raises(ValueError, match="library"):
        await service.select_articles(
            "topic",
            status="active",
            topics_map={},
            library_index=[{"id": "article", "topic": "topic"}],
            given_articles=[],
            exam_result="fail",
            trace_id="trace",
        )


async def test_curator_grade_requires_a_verdict_for_every_answer(service):
    service.backend.ask.return_value = VendorResult(exam(), 0.1, 1, ("call",))
    prepared = await service.prepare_exam(
        "topic", summary_post="Summary", given=1, read=1, n=5, trace_id="exam"
    )
    answers = {key: "A supplied answer" for key in prepared["exam_ids"]}
    service.backend.ask.return_value = VendorResult(
        GradeResult.model_validate(
            {
                "verdicts": [
                    {"q_index": 0, "verdict": "pass", "missed": [], "note": "Complete"}
                ],
                "overall": "pass",
                "public_comment": exam().public_comment,
            }
        ),
        0.1,
        1,
        ("grade",),
    )
    with pytest.raises(ValueError, match="each supplied answer"):
        await service.grade(
            "topic",
            answers=answers,
            pass_rule="Supplied grading policy",
            trace_id="grade",
        )
    with service.database.connection() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM exams WHERE verdict IS NOT NULL"
            ).fetchone()[0]
            == 0
        )
