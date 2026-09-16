"""File-backed curator workflows; only validated code writes educational state."""

import asyncio
import json
import re
from pathlib import Path
from typing import Annotated, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from src.core.time_utils import now

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_FIELD = re.compile(r"\{([a-z_]+)\}")
_COMMENTS = re.compile(r"<!--.*?-->", re.S)
log = structlog.get_logger("blogai.curator")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Question(StrictModel):
    q: Text
    expected: Text
    key_facts: list[Text] = Field(min_length=1)


class Correction(StrictModel):
    node: Text
    issue: Text
    correct: Text
    severity: Literal["high", "medium", "low"]


class TrustNote(StrictModel):
    source: Text
    comment: Text


class ExamResult(StrictModel):
    questions: list[Question] = Field(min_length=1)
    graph_corrections: list[Correction]
    trust_notes: list[TrustNote]
    public_comment: str = Field(min_length=400, max_length=700)


class Verdict(StrictModel):
    q_index: int = Field(ge=0)
    verdict: Literal["pass", "partial", "fail"]
    missed: list[Text]
    note: str


class GradeResult(StrictModel):
    verdicts: list[Verdict] = Field(min_length=1)
    overall: Literal["pass", "fail"]
    public_comment: str = Field(min_length=400, max_length=700)


class ArticleChoice(StrictModel):
    id: Text
    why: Text
    kind: Literal["core", "adjacent"]


class SelectionResult(StrictModel):
    articles: list[ArticleChoice]
    next_topic: str | None
    topic_switch: bool
    shortage: str | None
    public_comment: Text


class Curator:
    def __init__(self, database, backend, prompt_dir: Path):
        self.database, self.backend, self.prompt_dir = (
            database,
            backend,
            Path(prompt_dir),
        )

    def _snapshot(self, topic):
        with self.database.connection() as connection:
            connection.execute("BEGIN")
            history = [
                dict(row)
                for row in connection.execute(
                    "SELECT kind, claim, reasoning FROM curator_log "
                    "WHERE subject=? ORDER BY at DESC, id DESC LIMIT 20",
                    (topic,),
                )
            ]
            sources = [
                dict(row)
                for row in connection.execute(
                    "SELECT id, title, origin_key, publisher, kind, trust_prior "
                    "FROM sources WHERE topic=? ORDER BY id",
                    (topic,),
                )
            ]
            nodes = [
                dict(row)
                for row in connection.execute(
                    "SELECT DISTINCT n.id, n.name, n.summary, n.confidence, n.suspect "
                    "FROM nodes n JOIN edges e ON n.id=e.src OR n.id=e.dst "
                    "JOIN sources s ON s.id=e.source_id WHERE s.topic=? ORDER BY n.id",
                    (topic,),
                )
            ]
            edges = [
                dict(row)
                for row in connection.execute(
                    "SELECT e.src, e.rel, e.dst, e.source_id FROM edges e "
                    "JOIN sources s ON s.id=e.source_id WHERE s.topic=? ORDER BY e.id",
                    (topic,),
                )
            ]
            connection.commit()
        return {
            "curator_log": history,
            "sources_with_trust": sources,
            "subgraph": {"nodes": nodes, "edges": edges},
        }

    def _replay(self, phase, trace_id):
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT reasoning FROM curator_log WHERE json_valid(reasoning) "
                "AND json_extract(reasoning, '$.trace_id')=? "
                "AND json_extract(reasoning, '$.phase')=? ORDER BY id LIMIT 1",
                (trace_id, phase),
            ).fetchone()
        return json.loads(row[0])["receipt"] if row else None

    async def _ask(self, phase, blocks, schema, trace_id):
        template = _COMMENTS.sub(
            "", (self.prompt_dir / f"curator_{phase}.md").read_text(encoding="utf-8")
        )
        if set(_FIELD.findall(template)) != blocks.keys():
            raise ValueError("Curator context does not match the supplied template")
        user = _FIELD.sub(
            lambda match: json.dumps(blocks[match[1]], ensure_ascii=False), template
        )
        system = (self.prompt_dir / "curator_system.md").read_text(encoding="utf-8")
        with structlog.contextvars.bound_contextvars(trace_id=trace_id):
            return await asyncio.to_thread(
                self.backend.ask, system, user, schema, trace_id=trace_id
            )

    def _record(self, phase, topic, trace_id, result, *, exams=None, grades=None):
        def save(connection):
            existing = connection.execute(
                "SELECT reasoning FROM curator_log WHERE json_valid(reasoning) "
                "AND json_extract(reasoning, '$.trace_id')=? "
                "AND json_extract(reasoning, '$.phase')=?",
                (trace_id, phase),
            ).fetchone()
            if existing:
                return json.loads(existing[0])["receipt"]
            ids = []
            if exams:
                for question in exams:
                    ids.append(
                        connection.execute(
                            "INSERT INTO exams(topic, question, expected, "
                            "key_facts, at) VALUES (?, ?, ?, ?, ?)",
                            (
                                topic,
                                question.q,
                                question.expected,
                                json.dumps(question.key_facts),
                                now(),
                            ),
                        ).lastrowid
                    )
            if grades:
                for exam_id, answer, verdict in grades:
                    connection.execute(
                        "UPDATE exams SET answer=?, verdict=?, comment=? "
                        "WHERE id=? AND topic=?",
                        (answer, verdict.verdict, verdict.note, exam_id, topic),
                    )
            receipt = {
                "trace_id": trace_id,
                "exam_ids": ids,
                "result": result.value.model_dump(),
                "cost_usd": result.cost_usd,
            }
            connection.execute(
                "INSERT INTO curator_log(at, kind, subject, claim, reasoning, exam_id) "
                "VALUES (?, 'verdict', ?, ?, ?, ?)",
                (
                    now(),
                    topic,
                    result.value.public_comment,
                    json.dumps(
                        {"phase": phase, "trace_id": trace_id, "receipt": receipt},
                        ensure_ascii=False,
                    ),
                    ids[0] if ids else None,
                ),
            )
            return receipt

        with structlog.contextvars.bound_contextvars(trace_id=trace_id):
            return self.database.run_transaction(save)

    async def prepare_exam(self, topic, *, summary_post, given, read, n, trace_id):
        if type(n) is not int or n <= 0 or not 0 <= read <= given:
            raise ValueError("Invalid exam counts")
        if prior := await asyncio.to_thread(self._replay, "exam", trace_id):
            return prior
        snapshot = await asyncio.to_thread(self._snapshot, topic)
        result = await self._ask(
            "exam",
            snapshot
            | dict(topic=topic, summary_post=summary_post, given=given, read=read, n=n),
            ExamResult,
            trace_id,
        )
        exam = result.value
        if len(exam.questions) != n or len({q.q for q in exam.questions}) != n:
            raise ValueError("Curator must return the requested distinct questions")
        names = {
            value
            for node in snapshot["subgraph"]["nodes"]
            for value in (node["id"], node["name"])
        }
        if any(c.node not in names for c in exam.graph_corrections):
            raise ValueError("Correction refers to an unknown graph node")
        sources = {source["id"] for source in snapshot["sources_with_trust"]}
        if any(note.source not in sources for note in exam.trust_notes):
            raise ValueError("Trust note refers to an unknown source")
        return await asyncio.to_thread(
            self._record, "exam", topic, trace_id, result, exams=exam.questions
        )

    async def grade(self, topic, *, answers: dict[int, str], pass_rule: str, trace_id):
        if prior := await asyncio.to_thread(self._replay, "grade", trace_id):
            return prior

        def question_rows():
            with self.database.connection() as connection:
                rows = [
                    dict(
                        connection.execute(
                            "SELECT * FROM exams WHERE id=? AND topic=?", (key, topic)
                        ).fetchone()
                        or {}
                    )
                    for key in answers
                ]
            if not rows or any(not row for row in rows):
                raise ValueError("Unknown exam question")
            return rows

        rows = await asyncio.to_thread(question_rows)
        qa_pairs = [
            dict(
                question=row["question"],
                expected=row["expected"],
                key_facts=json.loads(row["key_facts"]),
                answer=answers[row["id"]],
            )
            for row in rows
        ]
        snapshot = await asyncio.to_thread(self._snapshot, topic)
        result = await self._ask(
            "grade",
            {
                "curator_log": snapshot["curator_log"],
                "subgraph": snapshot["subgraph"],
                "qa_pairs": qa_pairs,
                "pass_rule": pass_rule,
            },
            GradeResult,
            trace_id,
        )
        verdicts = result.value.verdicts
        if len(verdicts) != len(rows) or {v.q_index for v in verdicts} != set(
            range(len(rows))
        ):
            raise ValueError("Grade must cover each supplied answer exactly once")
        grades = [
            (rows[v.q_index]["id"], answers[rows[v.q_index]["id"]], v) for v in verdicts
        ]
        return await asyncio.to_thread(
            self._record, "grade", topic, trace_id, result, grades=grades
        )

    async def select_articles(
        self,
        topic,
        *,
        status,
        topics_map,
        library_index,
        given_articles,
        exam_result,
        trace_id,
    ):
        if prior := await asyncio.to_thread(self._replay, "select", trace_id):
            return prior
        if exam_result not in {"pass", "fail"}:
            raise ValueError("Article selection requires a completed exam")
        blocks = dict(
            topic=topic,
            status=status,
            topics_map=topics_map,
            library_index=library_index,
            given_articles=given_articles,
            exam_result=exam_result,
        )
        result = await self._ask("select", blocks, SelectionResult, trace_id)
        selection = result.value
        library = {item["id"]: item for item in library_index}
        ids = [item.id for item in selection.articles]
        if (
            len(ids) != len(set(ids))
            or set(ids) - library.keys()
            or set(ids) & set(given_articles)
        ):
            raise ValueError(
                "Selection contains unavailable or repeated library articles"
            )
        counts = {
            kind: sum(item.kind == kind for item in selection.articles)
            for kind in ("core", "adjacent")
        }
        expected = (
            {"core": 2, "adjacent": 4}
            if exam_result == "pass"
            else {"core": 4, "adjacent": 2}
        )
        if any(counts[k] > expected[k] for k in counts) or (
            not selection.shortage and counts != expected
        ):
            raise ValueError("Selection violates the literal article allocation")
        for article in selection.articles:
            if (library[article.id]["topic"] == topic) != (article.kind == "core"):
                raise ValueError(
                    "Article classification does not match its library topic"
                )
        if selection.topic_switch and (
            exam_result != "pass" or not selection.next_topic
        ):
            raise ValueError("Invalid topic switch")
        return await asyncio.to_thread(self._record, "select", topic, trace_id, result)
