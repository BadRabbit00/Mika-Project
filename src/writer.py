"""Generate stateless post drafts, validate them, and persist attempt outcomes."""

import asyncio
import json
import time
from dataclasses import dataclass
from uuid import uuid4

import httpx
import structlog

from src.core.context import OFFTOP_KINDS, WRITE_INPUTS
from src.core.time_utils import now, to_utc_iso
from src.validator import PastPost, ValidationContext, ValidationResult

log = structlog.get_logger("blogai.writer")


@dataclass(frozen=True)
class WriteResult:
    id: str
    status: str
    text: str | None
    attempts: int
    reasons: tuple[str, ...] = ()


class Writer:
    def __init__(self, database, llm, context, validator, *, max_output_tokens=2048):
        if type(max_output_tokens) is not int or max_output_tokens <= 0:
            raise ValueError("A positive output token limit is required")
        self.database, self.llm = database, llm
        self.context, self.validator = context, validator
        self.max_output_tokens = max_output_tokens

    def _history(self, at):
        with self.database.connection() as connection:
            connection.execute("BEGIN")
            posts = [
                PastPost(**dict(row))
                for row in connection.execute(
                    "SELECT p.id, p.text FROM posts p WHERE p.published_at<=? "
                    "AND p.text IS NOT NULL "
                    "AND NOT EXISTS (SELECT 1 FROM invalidated i WHERE i.post_id=p.id) "
                    "ORDER BY p.published_at DESC, p.id DESC LIMIT 30",
                    (to_utc_iso(at),),
                )
            ]
            terms = tuple(
                row[0]
                for row in connection.execute("SELECT name FROM nodes ORDER BY id")
            )
            connection.execute("COMMIT")
        return posts, terms

    def _save_attempt(
        self,
        *,
        run_id,
        post_id,
        kind,
        request,
        raw,
        validation,
        attempt,
        started_at,
        duration_ms,
        tokens_out,
        trace_id,
    ):
        final = validation.accepted or attempt == 3
        status = (
            "validated" if validation.accepted else "killed" if final else "rejected"
        )

        def save(connection):
            connection.execute(
                "INSERT INTO runs(trace_id, at, actor, profile, params_json, "
                "system, user, "
                "output, tokens_in, tokens_out, duration_ms, status, error) "
                "VALUES (?, ?, 'writer', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    started_at,
                    request.profile,
                    json.dumps(
                        {
                            "post_id": post_id,
                            "trace_id": trace_id,
                            "action_id": structlog.contextvars.get_contextvars().get(
                                "action_id"
                            ),
                            "attempt": attempt,
                            "temperature": request.temperature,
                            "max_tokens": self.max_output_tokens,
                        }
                    ),
                    request.system,
                    request.user,
                    raw,
                    request.tokens_in,
                    tokens_out,
                    duration_ms,
                    status,
                    json.dumps(validation.reasons),
                ),
            )
            if final:
                connection.execute(
                    "INSERT INTO posts(id, kind, state, text) VALUES (?, ?, ?, ?)",
                    (
                        post_id,
                        kind,
                        "draft" if validation.accepted else "killed",
                        validation.text if validation.accepted else None,
                    ),
                )

        self.database.run_transaction(save)

    async def generate(self, kind: str, **blocks) -> WriteResult:
        post_id = uuid4().hex
        trace_id = (
            structlog.contextvars.get_contextvars().get("trace_id") or uuid4().hex
        )
        if kind not in WRITE_INPUTS:
            raise ValueError(f"No isolated writing template for {kind}")
        day = blocks["day"]
        if day.blackout.blocked:
            log.info(
                "writing_blocked", reason=day.blackout.reason, at=to_utc_iso(day.at)
            )
            return WriteResult(post_id, "blocked", None, 0, (day.blackout.reason,))
        offtop = kind in OFFTOP_KINDS
        profile = "write_offtop" if offtop else "write_tech"
        posts, terms = await asyncio.to_thread(self._history, day.at)
        feedback = {}
        for attempt in range(1, 4):
            request = await self.context.build_checked(
                profile, llm=self.llm, kind=kind, **blocks, **feedback
            )
            run_id, started_at, started = uuid4().hex, now(), time.monotonic()
            raw, tokens_out = "", 0
            with structlog.contextvars.bound_contextvars(
                trace_id=trace_id, call_id=run_id, post_id=post_id
            ):
                try:
                    raw = await self.llm.generate(
                        request, max_tokens=self.max_output_tokens
                    )
                    validation = await self.validator.validate(
                        raw,
                        ValidationContext(
                            at=day.at,
                            daypart=day.daypart,
                            sleep_debt=day.sleep_debt,
                            offtop=offtop,
                            prompt=request.system + "\n" + request.user,
                            graph_terms=terms,
                            mode=request.mode,
                            min_chars=request.min_chars,
                            max_chars=request.max_chars,
                        ),
                        recent_posts=posts,
                    )
                    tokens = await self.llm.tokenize(raw)
                    if not isinstance(tokens, list) or any(
                        type(token) is not int or token < 0 for token in tokens
                    ):
                        raise ValueError("Exact output token IDs are required")
                    tokens_out = len(tokens)
                except httpx.HTTPError:
                    log.exception("writing_transport_failed", attempt=attempt)
                    raise
                except ValueError:
                    log.exception("writing_attempt_failed", attempt=attempt)
                    validation = ValidationResult("", ("generation_error",), ())
                await asyncio.to_thread(
                    self._save_attempt,
                    run_id=run_id,
                    post_id=post_id,
                    kind=kind,
                    request=request,
                    raw=raw,
                    validation=validation,
                    attempt=attempt,
                    started_at=started_at,
                    duration_ms=round((time.monotonic() - started) * 1000),
                    tokens_out=tokens_out,
                    trace_id=trace_id,
                )
                log.info(
                    "writing_attempt_completed",
                    attempt=attempt,
                    accepted=validation.accepted,
                    reasons=validation.reasons,
                )
            if validation.accepted:
                return WriteResult(post_id, "draft", validation.text, attempt)
            if validation.duplicate_of is not None:
                similar = next(
                    post for post in posts if post.id == validation.duplicate_of
                )
                feedback = {
                    "validation_feedback": {
                        "duplicate_of": similar.id,
                        "similar_text": similar.text,
                    }
                }
        return WriteResult(post_id, "killed", None, 3, validation.reasons)
