"""Names-only questions, graph retrieval, grounded answers, and citation checks."""

import asyncio
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, ValidationError
from ruamel.yaml import YAML

from src.core.context import ContextBuilder
from src.core.db import Database
from src.core.time_utils import now
from src.extract import normalize

log = structlog.get_logger("blogai.selfquiz")


@dataclass(frozen=True)
class QuizSettings:
    questions_per_round: int
    threshold: float

    def __post_init__(self):
        if type(self.questions_per_round) is not int or self.questions_per_round <= 0:
            raise ValueError("A positive question count is required")
        if type(self.threshold) not in (int, float) or not 0 <= self.threshold <= 1:
            raise ValueError("Quiz threshold must be between zero and one")

    @classmethod
    def from_registry(cls, path: Path):
        registry = YAML(typ="safe").load(Path(path).read_text(encoding="utf-8"))
        required = {"study.questions_per_round", "study.quiz_threshold"}
        values = {
            item["key"]: item["default"]
            for item in registry["settings"]
            if item["key"] in required
        }
        return cls(values["study.questions_per_round"], values["study.quiz_threshold"])


@dataclass(frozen=True)
class QuizQuestion:
    id: int
    topic: str
    text: str


@dataclass(frozen=True)
class QuizResult:
    question_id: int
    question: str
    verdict: str
    answer: str | None
    cited: tuple[str, ...]
    retrieved: tuple[str, ...]


@dataclass(frozen=True)
class QuizRound:
    results: tuple[QuizResult, ...]
    answered: int
    passed: bool
    trace_id: str


class Answer(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    answer: str
    cited: list[str]
    confident: bool


def validate_citations(answer: Answer, retrieved: set[str], existing: set[str]) -> str:
    if not answer.cited:
        return "no_knowledge"
    if not set(answer.cited) <= retrieved & existing:
        return "invalid_citation"
    # Confidence is logged; only the supplied evidence determines the verdict.
    return "answered" if answer.answer.strip() else "no_knowledge"


class SelfQuiz:
    def __init__(
        self,
        database: Database,
        llm,
        retriever,
        context: ContextBuilder,
        settings: QuizSettings,
        *,
        grammar_dir: Path = Path("grammars"),
        max_output_tokens: int = 2048,
    ):
        if type(max_output_tokens) is not int or max_output_tokens <= 0:
            raise ValueError("A positive generation limit is required")
        self.database, self.llm, self.retriever = database, llm, retriever
        self.context, self._settings = context, settings
        self.grammar = (Path(grammar_dir) / "answer.gbnf").read_text(encoding="utf-8")
        self.max_output_tokens = max_output_tokens

    @property
    def settings(self):
        if isinstance(self._settings, QuizSettings):
            return self._settings
        return QuizSettings(
            self._settings.get("study.questions_per_round"),
            self._settings.get("study.quiz_threshold"),
        )

    def _question_context(self, topic):
        with self.database.connection() as connection:
            connection.execute("BEGIN")
            names = [
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT n.name FROM nodes n WHERE n.suspect=0 AND EXISTS ("
                    "SELECT 1 FROM edges e JOIN sources s ON s.id=e.source_id "
                    "WHERE s.topic=? AND (e.src=n.id OR e.dst=n.id)) ORDER BY n.name",
                    (topic,),
                )
            ]
            previous = [
                row[0]
                for row in connection.execute(
                    "SELECT text FROM questions WHERE topic=? ORDER BY id", (topic,)
                )
                if row[0]
            ]
            connection.execute("COMMIT")
            return names, previous

    async def ask(self, topic: str) -> list[QuizQuestion]:
        if not topic.strip():
            raise ValueError("A nonempty topic is required")
        names, previous = await asyncio.to_thread(self._question_context, topic)
        if not names:
            log.info("quiz_has_no_nodes", topic=topic)
            return []
        request = await self.context.build_checked(
            "selfquiz_ask",
            llm=self.llm,
            topic_node_names=names,
            asked_questions=previous,
            n=str(self.settings.questions_per_round),
        )
        output = await self.llm.generate(request, max_tokens=self.max_output_tokens)
        texts = [line.strip() for line in output.splitlines() if line.strip()]
        if (
            not texts
            or len(texts) > self.settings.questions_per_round
            or any(re.match(r"^(?:\d+[.)]|[-*#`])\s*", text) for text in texts)
        ):
            raise ValueError(
                "Expected an unnumbered list of questions within the requested count"
            )

        def save(connection):
            seen = {
                normalize(row[0])
                for row in connection.execute(
                    "SELECT text FROM questions WHERE topic=?", (topic,)
                )
                if row[0]
            }
            result = []
            for text in texts:
                if normalize(text) in seen:
                    continue
                seen.add(normalize(text))
                row_id = connection.execute(
                    "INSERT INTO questions(topic, text, asked_at) VALUES (?, ?, ?)",
                    (topic, text, now()),
                ).lastrowid
                result.append(QuizQuestion(row_id, topic, text))
            return result

        questions = await asyncio.to_thread(self.database.run_transaction, save)
        log.info("quiz_questions_created", topic=topic, count=len(questions))
        return questions

    @staticmethod
    def _result(row):
        return QuizResult(
            row["id"],
            row["text"],
            row["verdict"],
            row["answer"],
            tuple(json.loads(row["cited"] or "[]")),
            tuple(json.loads(row["retrieved"] or "[]")),
        )

    def _existing(self, question_id, topic, question):
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM questions WHERE id=?", (question_id,)
            ).fetchone()
            if row is None or row["topic"] != topic or row["text"] != question:
                raise ValueError("Question identity does not match its stored context")
            return self._result(row) if row["verdict"] else None

    async def answer(
        self, question: str, *, topic: str, question_id: int | None = None
    ) -> QuizResult:
        if not question.strip() or not topic.strip():
            raise ValueError("A nonempty question and topic are required")
        if question_id is not None:
            previous = await asyncio.to_thread(
                self._existing, question_id, topic, question
            )
            if previous:
                return previous
        asked_at = now()
        nodes = await self.retriever.search(question, topic=topic)
        retrieved = tuple(node.id for node in nodes)
        candidate, verdict = None, "no_knowledge"
        if nodes:
            if len(nodes) > 6 or len(set(retrieved)) != len(retrieved):
                raise ValueError("Retriever must return at most six distinct nodes")
            request = await self.context.build_checked(
                "selfquiz_answer",
                llm=self.llm,
                question=question,
                retrieved_nodes=[asdict(node) for node in nodes],
            )
            output = await self.llm.generate(
                request, grammar=self.grammar, max_tokens=self.max_output_tokens
            )
            try:
                candidate = Answer.model_validate_json(output)
            except ValidationError:
                verdict = "invalid_citation"
                log.warning("quiz_answer_rejected", reason="schema", output=output)

        def save(connection):
            current_id = question_id
            if current_id is not None:
                row = connection.execute(
                    "SELECT * FROM questions WHERE id=?", (current_id,)
                ).fetchone()
                if row is None or row["topic"] != topic or row["text"] != question:
                    raise ValueError("Question changed during generation")
                if row["verdict"]:
                    return self._result(row)
            outcome = verdict
            if candidate is not None:
                existing = {
                    row[0]
                    for row in connection.execute(
                        "SELECT id FROM nodes WHERE suspect=0"
                    )
                }
                outcome = validate_citations(candidate, set(retrieved), existing)
            if current_id is None:
                current_id = connection.execute(
                    "INSERT INTO questions(topic, text, asked_at) VALUES (?, ?, ?)",
                    (topic, question, asked_at),
                ).lastrowid
            connection.execute(
                "UPDATE questions SET retrieved=?, answer=?, cited=?, verdict=? "
                "WHERE id=?",
                (
                    json.dumps(retrieved),
                    candidate.answer if candidate else None,
                    json.dumps(candidate.cited if candidate else []),
                    outcome,
                    current_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM questions WHERE id=?", (current_id,)
            ).fetchone()
            return self._result(row)

        result = await asyncio.to_thread(self.database.run_transaction, save)
        log.info(
            "quiz_answer_validated",
            question_id=result.question_id,
            verdict=result.verdict,
            retrieved=result.retrieved,
            cited=result.cited,
        )
        return result

    async def run(self, topic: str) -> QuizRound:
        trace_id = (
            structlog.contextvars.get_contextvars().get("trace_id") or uuid4().hex
        )
        with structlog.contextvars.bound_contextvars(trace_id=trace_id):
            questions = await self.ask(topic)
            results = tuple(
                [
                    await self.answer(item.text, topic=item.topic, question_id=item.id)
                    for item in questions
                ]
            )
            answered = sum(result.verdict == "answered" for result in results)
            passed = (
                len(results) >= 5 and answered / len(results) >= self.settings.threshold
            )
            log.info(
                "quiz_round_finished",
                topic=topic,
                count=len(results),
                answered=answered,
                passed=passed,
            )
            return QuizRound(results, answered, passed, trace_id)
