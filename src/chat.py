"""Persistent, isolated conversation sessions; models return validated data only."""

import asyncio
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import httpx
import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from ruamel.yaml import YAML

from src.core.chat_metrics import encode_mood, mood_metrics
from src.core.chat_router import ChatRouter
from src.core.chat_store import SessionStore as SessionStore
from src.core.chat_store import summary_state
from src.core.content_rules import normalized_text
from src.core.context import ContextBuilder, ContextOverflow
from src.core.pad import Mood
from src.core.time_utils import elapsed_hours, from_utc_iso, require_aware
from src.core.vectors import cosine
from src.selfquiz import Answer, validate_citations
from src.validator import OutputValidator, ValidationContext

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
_NAME_QUESTION = re.compile(
    r"как (?:тебя|вас) зовут|(?:тво[её]|ваше) имя|давай(?:те)? знакомиться|"
    r"как к (?:тебе|вам) обращаться|как (?:мне )?(?:тебя|вас) называть|"
    r"what (?:should|can) I call you|"
    r"(?:what(?:'s| is)|tell me|remind me of) your name",
    re.I,
)
_BARE_NAME = re.compile(
    r"[a-zа-яё][a-zа-яё'’-]{0,39}(?: [a-zа-яё][a-zа-яё'’-]{0,39}){0,2}[.!]?",
    re.I,
)


def _name_answer(fact, turns):
    """Accept a short direct answer only to a visible name question."""
    for index, turn in enumerate(turns):
        if turn["id"] != fact.source or turn["role"] != "user":
            continue
        text = normalized_text(turn["text"])
        previous = turns[index - 1] if index else None
        return bool(
            fact.kind == "name"
            and previous
            and previous["role"] == "mika"
            and _NAME_QUESTION.search(previous["text"])
            and _BARE_NAME.fullmatch(text)
            and text.split()[0] not in {"не", "нет", "no", "not", "maybe", "может"}
            and normalized_text(fact.fact).rstrip(".!") == text.rstrip(".!")
        )
    return False


def _declared_name(fact, source):
    if fact.kind != "name" or not _BARE_NAME.fullmatch(fact.fact):
        return False
    pattern = re.compile(
        r"\b(?:меня зовут|мо[её] имя|my name is)\s+"
        + re.escape(normalized_text(fact.fact).rstrip(".!"))
        + r"(?=$|[\s,.!])"
    )
    return any(
        "?" not in sentence and pattern.search(sentence)
        for sentence in re.split(r"(?<=[.!?])\s*", source)
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
            or not text
            or "?" in text
            or not (
                _DIRECT.search(text)
                or _name_answer(fact, turns)
                or _declared_name(fact, source)
            )
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
        mood_provider=None,
    ):
        if not person_id:
            raise ValueError("An explicit conversation owner is required")
        self.database, self.llm, self.retriever = database, llm, retriever
        self.context, self._settings, self.person_id = context, settings, str(person_id)
        self.mood_provider = mood_provider
        self.summarizer, self.facts_extractor = summarizer, facts_extractor
        self.store = SessionStore(database)
        self.grammar = (Path(grammar_dir) / "answer.gbnf").read_text()
        self.router = ChatRouter(llm, context.prompt_dir, grammar_dir=grammar_dir)
        self.validator = validator or OutputValidator(llm)
        self._locks = {channel: asyncio.Lock() for channel in ("topic", "dm")}

    @property
    def settings(self):
        if isinstance(self._settings, ChatSettings):
            return self._settings
        return ChatSettings(
            self._settings.get("chat.context_tokens"),
            self._settings.get("chat.keep_last_turns"),
            self._settings.get("chat.session_ttl_hours"),
        )

    def _lock(self, channel):
        if channel not in self._locks:
            raise ValueError("Unknown session channel")
        return self._locks[channel]

    async def set_enabled(self, channel, enabled, *, at):
        if channel not in {"dm", "topic"} or type(enabled) is not bool:
            raise ValueError("A dialogue channel and boolean availability are required")
        at = require_aware(at)
        await asyncio.to_thread(
            self.database.run_transaction,
            lambda c: c.execute(
                "INSERT INTO life_state VALUES (?, ?, ?) ON CONFLICT(key) DO "
                "UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                ("chat.enabled:" + channel, json.dumps(enabled), at),
            ),
        )

    async def open(self, channel, *, at, mood=None):
        at = require_aware(at)
        if mood is None and self.mood_provider is not None:
            mood = await self.mood_provider(at)
        async with self._lock(channel):
            await self.set_enabled(channel, True, at=at)
            current = await asyncio.to_thread(self.store.active, channel)
            if current and await self._expired(current, at):
                await self._close(current, at=at, mood=mood)
            return await asyncio.to_thread(
                self.store.open,
                channel,
                at=at,
                mood=encode_mood(mood, self.context.mood_model),
            )

    async def _expired(self, session, at):
        def unread():
            with self.database.connection(readonly=True) as c:
                return (
                    c.execute(
                        "SELECT 1 FROM chat_inbox WHERE session_id=? AND status IN "
                        "('pending','generating','ready') LIMIT 1",
                        (session["id"],),
                    ).fetchone()
                    is not None
                )

        if await asyncio.to_thread(unread):
            return False
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
            existing = (
                {row[0] for row in c.execute("SELECT id FROM nodes WHERE suspect=0")}
                if mode == "topical"
                else set()
            )
        return facts, narrative, existing

    async def _request(self, mode, *, history, state, session, trace_id, **kwargs):
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
            request = self.context.build(mode, history=tail, summary=summary, **kwargs)
            compressed_tokens = await self.llm.prompt_tokens(request)
            ContextBuilder._enforce(request, compressed_tokens)
            state.update(text=summary, through_idx=head[-1]["idx"])
            state.setdefault("tokens_before_first_compression", len(tokens))
            await asyncio.to_thread(self.store.checkpoint, session["id"], state)
            tokens, history = compressed_tokens, tail
        ContextBuilder._enforce(request, tokens)
        return request, tokens, history

    async def reply(
        self,
        channel,
        question,
        *,
        trace_id,
        day,
        mood,
        wake_reason,
        topic,
        incoming_trace_id=None,
        delivery_context=None,
        life_state=None,
    ):
        async with self._lock(channel):
            session = await asyncio.to_thread(self.store.active, channel)
            if session is None:
                return None
            if await self._expired(session, day.at):
                await self._close(session, at=day.at, mood=mood)
                return None
            with structlog.contextvars.bound_contextvars(
                trace_id=trace_id, session_id=session["id"], chat_channel=channel
            ):
                user = await asyncio.to_thread(
                    self.store.add_user,
                    session["id"],
                    question,
                    trace_id=incoming_trace_id or trace_id,
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
                staged = await asyncio.to_thread(self.store.staged_reply, trace_id)
                if staged:
                    if staged["session_id"] != session["id"]:
                        raise ValueError("A reply trace belongs to another session")
                    return self._reply(staged)
                mode, nodes = "personal", []
                facts, narrative, existing = await asyncio.to_thread(self._memory, mode)
                state = await asyncio.to_thread(self.store.confirmed_summary, session)
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
                if life_state is not None or delivery_context is not None:
                    kwargs.update(
                        life_state=life_state, delivery_context=delivery_context
                    )
                request, tokens, history = await self._request(
                    mode,
                    history=history,
                    state=state,
                    session=session,
                    trace_id=trace_id,
                    **kwargs,
                )
                query = await self.router.query(request)
                if query is not None:
                    knowledge_status = "empty"
                    try:
                        nodes = await self.retriever.search(query, topic=topic)
                    except (httpx.HTTPError, TimeoutError):
                        knowledge_status = "unavailable"
                        log.warning("chat_knowledge_unavailable")
                    if len(nodes) > 12 or len({node.id for node in nodes}) != len(
                        nodes
                    ):
                        raise ValueError(
                            "Chat retrieval requires at most twelve distinct nodes"
                        )
                    mode = "topical" if nodes else "unknown"
                    _, _, existing = await asyncio.to_thread(self._memory, mode)
                    kwargs.update(
                        nodes=[asdict(node) for node in nodes],
                        narrative=(),
                        knowledge_status="available" if nodes else knowledge_status,
                    )
                    request, tokens, history = await self._request(
                        mode,
                        history=history,
                        state=state,
                        session=session,
                        trace_id=trace_id,
                        **kwargs,
                    )
                log.info(
                    "chat_routed", mode=mode, knowledge_requested=query is not None
                )
                raw = await self.llm.generate(request, grammar=self.grammar)
                answer = Answer.model_validate_json(raw)
                if mode == "topical" and (answer.cited or answer.confident):
                    if (
                        validate_citations(
                            answer, {node.id for node in nodes}, existing
                        )
                        != "answered"
                    ):
                        raise ValueError("Invalid topical citation")
                elif mode != "topical" and answer.cited:
                    raise ValueError(
                        "A non-topical answer cannot contain graph citations"
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
                        dialogue=True,
                    ),
                )
                if not validation.accepted:
                    raise ValueError(f"Chat output rejected: {validation.reasons}")
                output_tokens = await self.llm.tokenize(raw)
                row = await asyncio.to_thread(
                    self.store.stage_reply,
                    session["id"],
                    user_id=user["id"],
                    text=validation.text,
                    mode=mode,
                    cited=answer.cited,
                    at=day.at,
                    trace_id=trace_id,
                    tokens=len(tokens) + len(output_tokens),
                    topic=topic,
                    mood=encode_mood(mood, self.context.mood_model),
                )
                log.info(
                    "chat_reply_staged",
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

    async def _close(self, session, *, at, mood=None):
        trace_id = "session-close:" + session["id"]
        with structlog.contextvars.bound_contextvars(
            trace_id=trace_id, chat_channel=session["channel"]
        ):
            if summary_state(session).get("finalized"):
                return
            if (
                not session["closed_at"]
                and mood is None
                and self.mood_provider is not None
            ):
                mood = await self.mood_provider(at)
            await asyncio.to_thread(
                self.store.begin_close,
                session["id"],
                at,
                mood=encode_mood(mood, self.context.mood_model),
            )
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
                mood=None,
                trace_id=trace_id,
            )
            log.info("chat_session_closed", session_id=session["id"], facts=len(facts))

    async def close(self, session_id, *, at, mood=None):
        session = await asyncio.to_thread(self.store.get, session_id)
        async with self._lock(session["channel"]):
            await self.set_enabled(session["channel"], False, at=at)
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
        turns = await asyncio.to_thread(self.store.turns, session_id, include_mood=True)
        events = [
            {
                "at": row["at"],
                "mood": encode_mood(
                    Mood(row["p"], row["a"], row["d"]), self.context.mood_model
                ),
            }
            for row in await asyncio.to_thread(self.store.mood_events, session, turns)
        ]
        topical = [
            turn
            for turn in turns
            if turn["role"] == "mika"
            and turn["mode"] == "topical"
            and json.loads(turn["cited"] or "[]")
        ]
        replies = [turn for turn in turns if turn["role"] == "mika"]
        voice, voice_status = None, "insufficient_turns"
        if len(replies) >= 2:
            with structlog.contextvars.bound_contextvars(
                trace_id="session-export:" + session_id,
                chat_channel=session["channel"],
            ):
                try:
                    first = await self.llm.embed(replies[0]["text"])
                    last = await self.llm.embed(replies[-1]["text"])
                    voice, voice_status = cosine(first, last), "measured"
                except (httpx.HTTPError, ValueError):
                    voice_status = "embedding_unavailable"
                    log.exception("session_export_embedding_unavailable")
        unknown = sum(turn["mode"] == "unknown" for turn in replies)
        header = json.dumps(
            {
                "session": session,
                "mood_events": events,
                "metrics": {
                    "valid_citation_fraction": 1.0 if topical else None,
                    "tokens_before_first_compression": summary_state(session).get(
                        "tokens_before_first_compression"
                    ),
                    "voice_drift": voice,
                    "voice_drift_status": voice_status,
                    "voice_drift_metric": "first_last_reply_cosine",
                    "unknown_reply_count": unknown,
                    "unknown_reply_fraction": unknown / len(replies)
                    if replies
                    else None,
                    **mood_metrics(session, turns, events),
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
