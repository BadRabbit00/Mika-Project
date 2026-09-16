"""Persistent, isolated conversation sessions; models return validated data only."""

import asyncio
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from ruamel.yaml import YAML

from src.core.chat_store import SessionStore as SessionStore
from src.core.chat_store import summary_state
from src.core.content_rules import normalized_text, technical_match
from src.core.context import ContextBuilder, ContextOverflow
from src.core.time_utils import elapsed_hours, from_utc_iso, require_aware
from src.selfquiz import Answer, validate_citations
from src.validator import OutputValidator, ValidationContext, lexical_echo_similarity

log = structlog.get_logger("blogai.chat")


@dataclass(frozen=True)
class ChatSettings:
    budget: int
    keep_last_turns: int
    ttl_hours: int

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in asdict(self).values()):
            raise ValueError("Positive chat settings are required")

    @classmethod
    def from_registry(cls, path):
        data = YAML(typ="safe").load(Path(path).read_text())
        values = {row["key"]: row.get("default") for row in data["settings"]}
        return cls(
            values["chat.context_tokens"],
            values["chat.keep_last_turns"],
            values["chat.session_ttl_hours"],
        )


@dataclass(frozen=True)
class ChatReply:
    session_id: str
    text: str
    mode: str
    cited: tuple[str, ...]
    trace_id: str


class Fact(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    kind: Literal["name", "prefs", "context", "project"]
    fact: str = Field(min_length=1)
    source: int = Field(gt=0)


# Conservative lexical filters supplement verbatim evidence, not inferred traits.
_SENSITIVE = re.compile(
    r"salary|income|money|health|diagnos|politic|religio|relationship|girlfriend|"
    r"boyfriend|wife|husband|зарплат|доход|деньг|здоров|диагноз|боле[зт]|"
    r"полит|религи|отношени|муж\b|жена\b|беремен|секс",
    re.I,
)
_DIRECT = re.compile(r"\b(?:I|my|I'm|меня|я|мне|мой|моя|моё|мои)\b", re.I)
_UNKNOWN = re.compile(
    r"не знаю|не изучала|не разбиралась|не читала|"
    r"do not know|don't know|haven't studied|have not studied",
    re.I,
)


def validate_facts(candidates, turns):
    if not isinstance(candidates, list):
        raise ValueError("Expected a fact list")
    evidence = {turn["id"]: turn["text"] for turn in turns if turn["role"] == "user"}
    result, seen = [], set()
    for item in candidates:
        try:
            fact = Fact.model_validate(item)
        except ValidationError:
            continue
        text = normalized_text(fact.fact)
        source = normalized_text(evidence.get(fact.source, ""))
        if (
            text not in source
            or not _DIRECT.search(text)
            or _SENSITIVE.search(text)
            or (fact.kind, text) in seen
        ):
            continue
        result.append(fact.model_dump())
        seen.add((fact.kind, text))
    return result


class ChatService:
    def __init__(
        self,
        database,
        llm,
        retriever,
        context,
        settings,
        *,
        person_id,
        summarizer,
        facts_extractor,
        grammar_dir=Path("grammars"),
        validator=None,
    ):
        if not person_id:
            raise ValueError("An explicit conversation owner is required")
        self.database, self.llm, self.retriever = database, llm, retriever
        self.context, self.settings, self.person_id = context, settings, str(person_id)
        self.summarizer, self.facts_extractor = summarizer, facts_extractor
        self.store = SessionStore(database)
        self.grammar = (Path(grammar_dir) / "answer.gbnf").read_text()
        self.validator = validator or OutputValidator(
            llm, echo_similarity=lexical_echo_similarity
        )
        self._locks = {channel: asyncio.Lock() for channel in ("topic", "dm")}

    def _lock(self, channel):
        if channel not in self._locks:
            raise ValueError("Unknown session channel")
        return self._locks[channel]

    async def open(self, channel, *, at, mood=""):
        at = require_aware(at)
        async with self._lock(channel):
            current = await asyncio.to_thread(self.store.active, channel)
            if current and await self._expired(current, at):
                await self._close(current, at=at, mood=mood)
            return await asyncio.to_thread(self.store.open, channel, at=at, mood=mood)

    async def _expired(self, session, at):
        turns = await asyncio.to_thread(self.store.turns, session["id"])
        last = turns[-1]["at"] if turns else session["opened_at"]
        return elapsed_hours(from_utc_iso(last), at) >= self.settings.ttl_hours

    def _memory(self, mode):
        with self.database.connection() as c:
            facts = [
                dict(row)
                for row in c.execute(
                    "SELECT fact,kind FROM people_facts WHERE person_id=? ORDER BY id",
                    (self.person_id,),
                )
            ]
            terms = tuple(row[0] for row in c.execute("SELECT name FROM nodes"))
            narrative = (
                [
                    dict(row)
                    for row in c.execute(
                        "SELECT gist,at FROM narrative WHERE excluded=0 "
                        "AND kind IN ('offtop','daily','situation') "
                        "ORDER BY at DESC,id DESC LIMIT 6"
                    )
                ]
                if mode == "personal"
                else []
            )
            existing = {
                row[0] for row in c.execute("SELECT id FROM nodes WHERE suspect=0")
            }
        return facts, terms, narrative, existing

    async def reply(
        self, channel, question, *, trace_id, day, mood, wake_reason, topic
    ):
        async with self._lock(channel):
            session = await asyncio.to_thread(self.store.active, channel)
            if session is None:
                return None
            if await self._expired(session, day.at):
                await self._close(session, at=day.at)
                return None
            with structlog.contextvars.bound_contextvars(
                trace_id=trace_id, session_id=session["id"], chat_channel=channel
            ):
                user = await asyncio.to_thread(
                    self.store.add_user,
                    session["id"],
                    question,
                    trace_id=trace_id,
                    at=day.at,
                )
                turns = await asyncio.to_thread(self.store.turns, session["id"])
                previous = next(
                    (
                        turn
                        for turn in turns
                        if turn["trace_id"] == trace_id and turn["role"] == "mika"
                    ),
                    None,
                )
                if previous:
                    return self._reply(previous)
                _, terms, _, _ = await asyncio.to_thread(self._memory, None)
                nodes = await self.retriever.search(question, topic=topic)
                mode = (
                    "topical"
                    if nodes
                    else "unknown"
                    if technical_match(question, terms)
                    else "personal"
                )
                if len(nodes) > 6 or len({node.id for node in nodes}) != len(nodes):
                    raise ValueError(
                        "Chat retrieval requires at most six distinct nodes"
                    )
                facts, _, narrative, existing = await asyncio.to_thread(
                    self._memory, mode
                )
                state = summary_state(session)
                history = [turn for turn in turns if turn["idx"] > state["through_idx"]]
                kwargs = dict(
                    question=question,
                    nodes=[asdict(node) for node in nodes],
                    people_facts=facts,
                    day=day,
                    mood=mood,
                    wake_reason=wake_reason,
                    budget=self.settings.budget,
                    narrative=narrative,
                )
                request = self.context.build(
                    mode, history=history, summary=state["text"], **kwargs
                )
                tokens = await self.llm.prompt_tokens(request)
                if len(tokens) > self.settings.budget:
                    head, tail = (
                        history[: -self.settings.keep_last_turns],
                        history[-self.settings.keep_last_turns :],
                    )
                    if not head:
                        raise ContextOverflow(
                            "The protected chat tail exceeds its token budget"
                        )
                    summary = await self.summarizer(
                        turns=head, previous=state["text"], trace_id=trace_id
                    )
                    if not isinstance(summary, str) or not summary.strip():
                        raise ValueError("Dialogue compression returned no summary")
                    request = self.context.build(
                        mode, history=tail, summary=summary, **kwargs
                    )
                    compressed_tokens = await self.llm.prompt_tokens(request)
                    ContextBuilder._enforce(request, compressed_tokens)
                    state.update(text=summary, through_idx=head[-1]["idx"])
                    state.setdefault("tokens_before_first_compression", len(tokens))
                    await asyncio.to_thread(self.store.checkpoint, session["id"], state)
                    tokens = compressed_tokens
                ContextBuilder._enforce(request, tokens)
                raw = await self.llm.generate(request, grammar=self.grammar)
                answer = Answer.model_validate_json(raw)
                if mode == "topical":
                    if (
                        validate_citations(
                            answer, {node.id for node in nodes}, existing
                        )
                        != "answered"
                    ):
                        raise ValueError("Invalid topical citation")
                elif answer.cited:
                    raise ValueError(
                        "A non-topical answer cannot contain graph citations"
                    )
                if mode == "unknown" and not _UNKNOWN.search(answer.answer):
                    raise ValueError(
                        "An unknown answer must acknowledge missing knowledge"
                    )
                validation = await self.validator.validate(
                    answer.answer,
                    ValidationContext(
                        at=day.at,
                        daypart=day.daypart,
                        sleep_debt=day.sleep_debt,
                        offtop=False,
                        prompt=request.system + "\n" + request.user,
                        min_chars=request.min_chars,
                        max_chars=request.max_chars,
                    ),
                )
                if not validation.accepted:
                    raise ValueError(f"Chat output rejected: {validation.reasons}")
                output_tokens = await self.llm.tokenize(raw)
                row = await asyncio.to_thread(
                    self.store.save_reply,
                    session["id"],
                    user_id=user["id"],
                    text=validation.text,
                    mode=mode,
                    cited=answer.cited,
                    at=day.at,
                    trace_id=trace_id,
                    tokens=len(tokens) + len(output_tokens),
                    topic=topic,
                )
                log.info(
                    "chat_reply_saved",
                    mode=mode,
                    tokens_in=len(tokens),
                    tokens_out=len(output_tokens),
                )
                return self._reply(row)

    @staticmethod
    def _reply(row):
        return ChatReply(
            row["session_id"],
            row["text"],
            row["mode"],
            tuple(json.loads(row["cited"] or "[]")),
            row["trace_id"],
        )

    async def _close(self, session, *, at, mood=""):
        trace_id = "session-close:" + session["id"]
        with structlog.contextvars.bound_contextvars(
            trace_id=trace_id, chat_channel=session["channel"]
        ):
            if summary_state(session).get("finalized"):
                return
            await asyncio.to_thread(self.store.begin_close, session["id"], at)
            turns = await asyncio.to_thread(self.store.turns, session["id"])
            summary, facts = "", []
            if turns:
                summary = await self.summarizer(
                    turns=turns, previous="", trace_id=trace_id
                )
                candidates = await self.facts_extractor(turns=turns, trace_id=trace_id)
                facts = validate_facts(candidates, turns)
            await asyncio.to_thread(
                self.store.finish_close,
                session["id"],
                summary=summary,
                facts=facts,
                person_id=self.person_id,
                at=at,
                mood=mood,
                trace_id=trace_id,
            )
            log.info("chat_session_closed", session_id=session["id"], facts=len(facts))

    async def close(self, session_id, *, at, mood=""):
        session = await asyncio.to_thread(self.store.get, session_id)
        async with self._lock(session["channel"]):
            await self._close(
                await asyncio.to_thread(self.store.get, session_id),
                at=require_aware(at),
                mood=mood,
            )

    async def expire(self, *, at):
        at = require_aware(at)
        closed = []
        for channel in ("topic", "dm"):
            async with self._lock(channel):
                session = await asyncio.to_thread(self.store.active, channel)
                if session and await self._expired(session, at):
                    await self._close(session, at=at)
                    closed.append(session["id"])

        def pending():
            with self.database.connection() as c:
                return [
                    dict(row)
                    for row in c.execute(
                        "SELECT * FROM sessions WHERE closed_at IS NOT NULL"
                    )
                    if not summary_state(dict(row)).get("finalized")
                ]

        for session in await asyncio.to_thread(pending):
            await self.close(session["id"], at=at)
            closed.append(session["id"])
        return closed

    async def export(self, session_id):
        session = await asyncio.to_thread(self.store.get, session_id)
        turns = await asyncio.to_thread(self.store.turns, session_id)
        topical = [
            turn
            for turn in turns
            if turn["role"] == "mika" and turn["mode"] == "topical"
        ]
        header = json.dumps(
            {
                "session": session,
                "metrics": {
                    "valid_citation_fraction": 1.0 if topical else None,
                    "tokens_before_first_compression": summary_state(session).get(
                        "tokens_before_first_compression"
                    ),
                    "voice_drift": None,
                    "voice_drift_status": "TODO(CHAT-VOICE-METRICS)",
                },
            },
            ensure_ascii=False,
        )
        return (
            "\n".join(
                [
                    header,
                    *[json.dumps({"turn": turn}, ensure_ascii=False) for turn in turns],
                ]
            )
            + "\n"
        )
