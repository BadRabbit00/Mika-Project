"""File-backed curator workflows; only validated code writes educational state."""

import asyncio
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from src.core.settings import SettingsRegistry
from src.core.time_utils import now
from src.trust import Trust

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
    def __init__(
        self, database, backend, prompt_dir: Path, *, settings=None, trust=None
    ):
        self.database, self.backend, self.prompt_dir = (
            database,
            backend,
            Path(prompt_dir),
        )
        self.settings = settings or SettingsRegistry.from_file(
            Path("config/settings.yaml")
        )
        self.trust = trust or Trust.from_config(database)

    def pass_rule(self):
        return (
            (self.prompt_dir / "curator_pass_rule.md")
            .read_text()
            .replace("{threshold}", str(self.settings.get("study.quiz_threshold")))
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
                    "SELECT * FROM sources WHERE topic=? ORDER BY id",
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
            connection.execute("COMMIT")
        for source in sources:
            with self.database.connection() as c:
                hashes = [
                    row[0]
                    for row in c.execute(
                        "SELECT DISTINCT norm_hash FROM claims WHERE source_id=?",
                        (source["id"],),
                    )
                ]
            source["reliability"] = self.trust.reliability(source, at=now().date())
            source["claims_trust"] = {
                digest: asdict(
                    self.trust.claim(digest, source_id=source["id"], at=now().date())
                )
                for digest in hashes
            }
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
            ids, exam_run_id = [], None
            if exams:
                instant = now()
                prefix = f"exam-{instant:%Y%m%d}-"
                number = (
                    max(
                        (
                            int(row[0][len(prefix) :])
                            for row in connection.execute(
                                "SELECT id FROM exam_runs WHERE id LIKE ?",
                                (prefix + "%",),
                            )
                        ),
                        default=0,
                    )
                    + 1
                )
                exam_run_id = prefix + f"{number:02d}"
                connection.execute(
                    "INSERT INTO exam_runs(id,topic,at,trace_id) VALUES (?,?,?,?)",
                    (exam_run_id, topic, instant, trace_id),
                )
                for question in exams:
                    ids.append(
                        connection.execute(
                            "INSERT INTO exams(topic, question, expected, "
                            "key_facts, at, exam_run_id) VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                topic,
                                question.q,
                                question.expected,
                                json.dumps(question.key_facts),
                                now(),
                                exam_run_id,
                            ),
                        ).lastrowid
                    )
                for correction in result.value.graph_corrections:
                    matches = connection.execute(
                        "SELECT DISTINCT n.id FROM nodes n JOIN edges e ON "
                        "n.id=e.src OR n.id=e.dst "
                        "JOIN sources s ON s.id=e.source_id WHERE s.topic=? AND "
                        "(n.id=? OR n.name=?)",
                        (topic, correction.node, correction.node),
                    ).fetchall()
                    if len(matches) != 1:
                        raise ValueError(
                            "Correction must identify one current topic node"
                        )
                    key = matches[0][0]
                    connection.execute(
                        "UPDATE nodes SET summary=?,corrected_by=?,suspect=0 WHERE "
                        "id=?",
                        (correction.correct, "exam:" + exam_run_id, key),
                    )
                    connection.execute(
                        "UPDATE curator_review SET resolved_at=?,verdict=?,exam_id=? "
                        "WHERE subject=? AND kind='suspect_node' AND resolved_at IS "
                        "NULL",
                        (instant, correction.correct, exam_run_id, key),
                    )
                    connection.execute(
                        "INSERT INTO "
                        "curator_log(at,kind,subject,claim,reasoning,exam_id) VALUES "
                        "(?,'correction',?,?,?,?)",
                        (
                            instant,
                            key,
                            correction.correct,
                            json.dumps(
                                dict(
                                    topic=topic,
                                    trace_id=trace_id,
                                    issue=correction.issue,
                                )
                            ),
                            ids[0],
                        ),
                    )
            if grades:
                runs = {
                    connection.execute(
                        "SELECT exam_run_id FROM exams WHERE id=?", (item[0],)
                    ).fetchone()[0]
                    for item in grades
                }
                if len(runs) != 1 or None in runs:
                    raise ValueError("Grades must belong to one identified exam")
                exam_run_id = runs.pop()
                for exam_id, answer, verdict in grades:
                    connection.execute(
                        "UPDATE exams SET answer=?, verdict=?, comment=? "
                        "WHERE id=? AND topic=?",
                        (answer, verdict.verdict, verdict.note, exam_id, topic),
                    )
                connection.execute(
                    "UPDATE exam_runs SET verdict=? WHERE id=?",
                    (result.value.overall, exam_run_id),
                )
            receipt = {
                "trace_id": trace_id,
                "exam_ids": ids,
                "exam_run_id": exam_run_id,
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

    async def grade(self, topic, *, answers: dict[int, str], pass_rule=None, trace_id):
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
            runs = {row["exam_run_id"] for row in rows}
            if len(runs) != 1 or None in runs:
                raise ValueError("Answers must belong to one identified exam")
            with self.database.connection() as c:
                expected = {
                    row[0]
                    for row in c.execute(
                        "SELECT id FROM exams WHERE exam_run_id=?", (next(iter(runs)),)
                    )
                }
            if set(answers) != expected:
                raise ValueError("Each exam question requires an answer")
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
                "pass_rule": self.pass_rule() if pass_rule is None else pass_rule,
            },
            GradeResult,
            trace_id,
        )
        verdicts = result.value.verdicts
        if len(verdicts) != len(rows) or {v.q_index for v in verdicts} != set(
            range(len(rows))
        ):
            raise ValueError("Grade must cover each supplied answer exactly once")
        fraction = sum(
            {"pass": 1, "partial": 0.5, "fail": 0}[v.verdict] for v in verdicts
        ) / len(verdicts)
        passed = (
            fraction >= self.settings.get("study.quiz_threshold")
            and sum(v.verdict == "fail" for v in verdicts) <= 1
        )
        if result.value.overall != ("pass" if passed else "fail"):
            raise ValueError("Aggregate grade contradicts the configured pass rule")
        log.info(
            "exam_scored",
            trace_id=trace_id,
            fraction=fraction,
            unknown_answers=sum(not text.strip() for text in answers.values()),
        )
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
