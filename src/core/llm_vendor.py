"""Read-only headless Claude transport with bounded JSON repair and cost logs."""

import json
import math
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import structlog
from pydantic import BaseModel
from ruamel.yaml import YAML

log = structlog.get_logger("blogai.llm_vendor")


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _constant(value):
    raise ValueError(f"Non-finite JSON constant: {value}")


def extract_json[T: BaseModel](raw: str, schema: type[T]) -> T:
    """Accept one JSON object inside optional prose/fences; reject ambiguity."""
    if not isinstance(raw, str) or not issubclass(schema, BaseModel):
        raise TypeError("Text and a Pydantic model type are required")
    decoder = json.JSONDecoder(object_pairs_hook=_pairs, parse_constant=_constant)
    candidates, index = [], 0
    while index < len(raw):
        if raw[index] not in "{[":
            index += 1
            continue
        try:
            value, end = decoder.raw_decode(raw, index)
        except json.JSONDecodeError:
            index += 1
            continue
        candidates.append(value)
        index = end
    if len(candidates) != 1 or not isinstance(candidates[0], dict):
        raise ValueError("Expected exactly one unambiguous JSON object")
    return schema.model_validate(candidates[0], strict=True)


@dataclass(frozen=True)
class VendorConfig:
    model: str
    effort: str
    timeout_sec: int
    executable: str = "claude"

    def __post_init__(self):
        if not self.model or not self.effort or not self.executable:
            raise ValueError("Explicit curator configuration is required")
        if type(self.timeout_sec) is not int or self.timeout_sec <= 0:
            raise ValueError("A positive curator timeout is required")

    @classmethod
    def from_registry(cls, path: Path, *, timeout_sec: int, executable="claude"):
        # TODO(MODEL-CONFIG): models.yaml is absent; use the supplied registry.
        registry = YAML(typ="safe").load(Path(path).read_text(encoding="utf-8"))
        entries = {row["key"]: row for row in registry["settings"]}
        return cls(
            entries["curator.model"]["default"],
            entries["curator.effort"]["default"],
            timeout_sec,
            executable,
        )


@dataclass(frozen=True)
class VendorResult:
    value: BaseModel
    cost_usd: float | None
    attempts: int
    call_ids: tuple[str, ...]


class CuratorBackend(Protocol):
    def ask(
        self, system: str, user: str, schema: type[BaseModel], *, trace_id: str
    ) -> VendorResult: ...


class ClaudeCodeBackend:
    def __init__(self, config: VendorConfig):
        self.config = config

    def ask(
        self, system: str, user: str, schema: type[BaseModel], *, trace_id: str
    ) -> VendorResult:
        if not trace_id or not system.strip() or not user.strip():
            raise ValueError("A trace, system, and user context are required")
        cost, known_cost, call_ids, feedback = 0.0, True, [], None
        with structlog.contextvars.bound_contextvars(trace_id=trace_id):
            for attempt in range(1, 3):
                request = (
                    user
                    if feedback is None
                    else user
                    + "\n\n"
                    + json.dumps({"validation_error": feedback}, ensure_ascii=False)
                )
                call_id = uuid4().hex
                call_ids.append(call_id)
                started = time.monotonic()
                try:
                    payload = self._invoke(system, request)
                except (subprocess.SubprocessError, OSError, ValueError):
                    log.exception(
                        "curator_call_failed",
                        call_id=call_id,
                        model=self.config.model,
                        attempt=attempt,
                        system=system,
                        user=request,
                    )
                    raise
                value = payload.get("total_cost_usd")
                if value is None:
                    known_cost = False
                elif (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or value < 0
                ):
                    raise ValueError("Invalid CLI cost metadata")
                else:
                    cost += value
                log.info(
                    "curator_call_completed",
                    call_id=call_id,
                    actor="curator",
                    profile="curator",
                    model=self.config.model,
                    attempt=attempt,
                    params={
                        "effort": self.config.effort,
                        "timeout_sec": self.config.timeout_sec,
                    },
                    system=system,
                    user=request,
                    output=payload.get("result"),
                    thought=None,
                    usage=payload.get("usage"),
                    cost_usd=value,
                    model_usage=payload.get("modelUsage"),
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
                if payload.get("is_error"):
                    raise RuntimeError("Claude reported an unsuccessful invocation")
                try:
                    result = extract_json(payload.get("result"), schema)
                except (ValueError, TypeError) as error:
                    log.warning(
                        "curator_json_rejected", call_id=call_id, error=str(error)
                    )
                    if attempt == 2:
                        raise ValueError(
                            "Curator JSON validation failed twice"
                        ) from error
                    feedback = str(error)
                    continue
                return VendorResult(
                    result, cost if known_cost else None, attempt, tuple(call_ids)
                )
        raise AssertionError("Unreachable curator retry state")

    def _invoke(self, system, user):
        with tempfile.TemporaryDirectory(prefix="blogai-curator-") as directory:
            with (
                tempfile.NamedTemporaryFile(
                    "w+", suffix=".md", dir=directory, encoding="utf-8"
                ) as request,
                tempfile.NamedTemporaryFile(
                    "w+", suffix=".md", dir=directory, encoding="utf-8"
                ) as persona,
                tempfile.NamedTemporaryFile(
                    "w+", suffix=".json", dir=directory, encoding="utf-8"
                ) as mcp,
            ):
                for handle, content in (
                    (request, user),
                    (persona, system),
                    (mcp, json.dumps({"mcpServers": {}})),
                ):
                    handle.write(content)
                    handle.flush()
                tools = "Read,Glob,Grep,WebSearch,WebFetch"
                command = [
                    self.config.executable,
                    "-p",
                    "@" + request.name,
                    "--model",
                    self.config.model,
                    "--effort",
                    self.config.effort,
                    "--append-system-prompt-file",
                    persona.name,
                    "--output-format",
                    "json",
                    "--tools",
                    tools,
                    "--allowedTools",
                    tools,
                    "--disallowedTools",
                    "mcp__*",
                    "--strict-mcp-config",
                    "--mcp-config",
                    mcp.name,
                    "--setting-sources",
                    "",
                    "--disable-slash-commands",
                    "--permission-mode",
                    "dontAsk",
                    "--no-session-persistence",
                ]
                output = subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=self.config.timeout_sec,
                    check=True,
                    cwd=directory,
                )
        payload = json.loads(
            output.stdout, object_pairs_hook=_pairs, parse_constant=_constant
        )
        if not isinstance(payload, dict):
            raise ValueError("Claude CLI envelope must be a JSON object")
        return payload
