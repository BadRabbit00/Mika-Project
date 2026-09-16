"""Bounded Gemma reasoning stays separate from validated application output."""

import asyncio
import json
import logging
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
import structlog
from structlog.testing import capture_logs

from src.core.context import ContextBuilder, ContextOverflow
from src.core.db import Database
from src.core.llm_local import LocalLLM
from src.core.ops_log import OpsMirror
from src.core.reasoning import after_reasoning
from src.core.settings import SettingsRegistry, SQLiteSettingsStore


class Settings:
    def __init__(self, **values):
        self.values = {
            "llm.reasoning_enabled": True,
            "llm.reasoning_budget_tokens": 64,
            "llm.reasoning_timeout_sec": 60,
            "system.llm_timeout_sec": 30,
        } | values

    def get(self, key):
        return self.values[key]


class Server:
    def __init__(self):
        self.calls = []
        self.context = 32768
        self.prompt_size = 20
        self.thought_size = 3
        self.answer_size = 5
        self.answer = '{"answer":"fixture"}'
        self.thought = "Private reasoning fixture"
        self.template = "enable_thinking <|turn>model <|channel>thought <channel|>"
        self.overrides = {}

    def handle(self, request):
        body = json.loads(request.content) if request.content else {}
        self.calls.append((request.url.path, body))
        match request.url.path:
            case "/props":
                data = {
                    "default_generation_settings": {"n_ctx": self.context},
                    "chat_template": self.template,
                }
            case "/apply-template":
                data = {"prompt": "<|turn>model\n"}
            case "/tokenize":
                tokens = (
                    [102]
                    if body["content"] == "<channel|>"
                    else [50] * self.answer_size
                    if body["content"] == self.answer
                    else [10] * self.prompt_size
                )
                data = {"tokens": tokens}
            case "/completion":
                tokens = (
                    [40] * self.thought_size + [102] + [50] * self.answer_size + [2]
                )
                data = {
                    "content": self.thought + "<channel|>" + self.answer,
                    "tokens": tokens,
                    "tokens_predicted": len(tokens),
                    "tokens_evaluated": self.prompt_size,
                    "stop_type": "eos",
                    "model": "gemma-fixture",
                } | self.overrides
            case _:
                pytest.fail(f"Unexpected endpoint: {request.url.path}")
        return httpx.Response(200, json=data)

    @property
    def completions(self):
        return [body for path, body in self.calls if path == "/completion"]


@pytest.fixture
def request_data():
    return ContextBuilder(Path("prompts")).build(
        "extract", existing_node_names=[], article_chunk="SQLite requires storage."
    )


async def test_reasoning_separated_from_answer_and_recorded(tmp_path, request_data):
    database = Database(tmp_path / "runs.sqlite3")
    database.initialize()
    server = Server()
    async with LocalLLM(
        settings=Settings(),
        database=database,
        transport=httpx.MockTransport(server.handle),
    ) as llm:
        with structlog.contextvars.bound_contextvars(trace_id="reasoning-fixture"):
            assert await llm.generate(request_data) == server.answer
    with database.connection() as c:
        row = c.execute("SELECT * FROM runs").fetchone()
        assert row["thought"] == server.thought
        assert row["output"] == server.answer
        assert row["trace_id"] == "reasoning-fixture"
        assert row["tokens_out"] == 10
        assert row["status"] == "completed"
    template = next(body for path, body in server.calls if path == "/apply-template")
    assert template["chat_template_kwargs"]["enable_thinking"] is True
    payload = server.completions[0]
    assert payload["reasoning_budget_tokens"] == 64
    assert payload["generation_prompt"] == "<|channel>thought"
    assert payload["return_tokens"] is True
    assert "stop" not in payload


async def test_reasoning_grammar_starts_after_thinking(request_data):
    server = Server()
    grammar = Path("grammars/claims.gbnf").read_text()
    async with LocalLLM(
        settings=Settings(), transport=httpx.MockTransport(server.handle)
    ) as llm:
        await llm.generate(request_data, grammar=grammar)
    payload = server.completions[0]
    assert payload["grammar_lazy"] is True
    assert payload["grammar"].startswith('root ::= "<channel|>" mika-answer\n')
    assert "mika-answer ::= claim (nl claim)* nl?" in payload["grammar"]
    assert payload["grammar_triggers"] == [{"type": 1, "value": "<channel|>"}]
    assert "<channel|>" in payload["preserved_tokens"]
    assert "tools" not in payload


async def test_reasoning_reserves_answer_space_in_actual_slot(request_data):
    server = Server()
    server.context = 100
    async with LocalLLM(
        settings=Settings(), transport=httpx.MockTransport(server.handle)
    ) as llm:
        await llm.generate(request_data, max_tokens=50)
    payload = server.completions[0]
    assert payload["reasoning_budget_tokens"] == 25
    assert payload["n_predict"] == 80
    assert len(payload["prompt"]) + payload["n_predict"] == server.context


async def test_reasoning_overflow_never_calls_model(request_data):
    server = Server()
    server.context = 70
    async with LocalLLM(
        settings=Settings(), transport=httpx.MockTransport(server.handle)
    ) as llm:
        with pytest.raises(ContextOverflow):
            await llm.generate(request_data, max_tokens=50)
    assert not server.completions


async def test_reasoning_detects_server_context_change(request_data):
    server = Server()

    def restarted(request):
        response = server.handle(request)
        if request.url.path == "/completion":
            server.context = 1024
        return response

    async with LocalLLM(
        settings=Settings(), transport=httpx.MockTransport(restarted)
    ) as llm:
        with pytest.raises(ContextOverflow, match="changed"):
            await llm.generate(request_data)


async def test_reasoning_utf8_completion_uses_only_reserved_space(request_data):
    server = Server()
    server.context = 100
    server.thought_size = 28
    async with LocalLLM(
        settings=Settings(), transport=httpx.MockTransport(server.handle)
    ) as llm:
        assert await llm.generate(request_data, max_tokens=50) == server.answer
    assert server.completions[0]["reasoning_budget_tokens"] == 25


@pytest.mark.parametrize(
    "override",
    [
        {"content": "Unclosed reasoning"},
        {"content": "Thought<channel|>Answer<|channel>thoughtSecond<channel|>"},
        {"tokens": [40, 50]},
        {"tokens_predicted": 20000},
        {"tokens_evaluated": 19},
        {"stop_type": "limit"},
        {"truncated": True},
        {"stop_type": "word"},
    ],
)
async def test_incomplete_or_inconsistent_reasoning_is_rejected(
    request_data, override, tmp_path
):
    server = Server()
    server.overrides = override
    database = Database(tmp_path / "failed.sqlite3")
    database.initialize()
    async with LocalLLM(
        settings=Settings(),
        database=database,
        transport=httpx.MockTransport(server.handle),
    ) as llm:
        with pytest.raises(ValueError):
            await llm.generate(request_data)
    with database.connection() as c:
        assert c.execute("SELECT status FROM runs").fetchone()[0] == "failed"


async def test_reasoning_settings_apply_to_next_generation(tmp_path, request_data):
    database = Database(tmp_path / "settings.sqlite3")
    database.initialize()
    settings = SettingsRegistry.from_file(
        Path("config/settings.yaml"), store=SQLiteSettingsStore(database)
    )
    server = Server()
    async with LocalLLM(
        settings=settings, transport=httpx.MockTransport(server.handle)
    ) as llm:
        await llm.generate(request_data)
        settings.set("llm.reasoning_budget_tokens", "128", trace_id="settings-test")
        await llm.generate(request_data)
        settings.set("llm.reasoning_enabled", "false", trace_id="settings-test")
        server.overrides = {"content": server.answer}
        await llm.generate(
            request_data, grammar=Path("grammars/claims.gbnf").read_text()
        )
    assert server.completions[0]["reasoning_budget_tokens"] == 8192
    assert server.completions[1]["reasoning_budget_tokens"] == 128
    assert "reasoning_budget_tokens" not in server.completions[2]
    assert "grammar_lazy" not in server.completions[2]
    templates = [body for path, body in server.calls if path == "/apply-template"]
    assert templates[-1]["chat_template_kwargs"]["enable_thinking"] is False
    for key, value in (
        ("llm.reasoning_budget_tokens", "-1"),
        ("llm.reasoning_budget_tokens", "16385"),
        ("llm.reasoning_timeout_sec", "0"),
    ):
        with pytest.raises(ValueError):
            settings.validate(key, value)


async def test_reasoning_post_tags_do_not_stop_the_thought(request_data):
    server = Server()
    server.thought = "Fixture mentions </casual> while thinking."
    async with LocalLLM(
        settings=Settings(), transport=httpx.MockTransport(server.handle)
    ) as llm:
        assert await llm.generate(replace(request_data, mode="casual")) == server.answer
    assert "stop" not in server.completions[0]


async def test_reasoning_hidden_from_machine_attachment(request_data):
    server = Server()
    server.overrides = {"completion_probabilities": [{"content": server.thought}]}
    with capture_logs() as events:
        async with LocalLLM(
            settings=Settings(), transport=httpx.MockTransport(server.handle)
        ) as llm:
            await llm.generate(request_data)
    event = next(row for row in events if row["event"] == "local_generation_finished")
    assert event["thought"] == server.thought
    settings = SettingsRegistry.from_file(Path("config/settings.yaml"))
    settings.entries["log.verbosity"]["default"] = "full"
    settings.entries["log.show_thoughts"]["default"] = False
    publisher = Mock()
    mirror = OpsMirror(publisher, Mock(), settings)
    mirror.emit(
        logging.LogRecord("blogai.llm_local", logging.INFO, "", 0, event, (), None)
    )
    await mirror.drain(force=True)
    attachment = publisher.enqueue_operations.call_args.args[0][1]["content"]
    assert server.thought not in attachment
    assert json.loads(attachment)["events"][0]["output"] == server.answer
    assert "tokens" not in json.loads(attachment)["events"][0]["response"]


def test_reasoning_grammar_preserves_literals_and_recursive_rules():
    grammar = 'root ::= "root" [root] root? other-root # root is recursive\n'
    assert after_reasoning(grammar) == (
        'root ::= "<channel|>" mika-answer\n'
        'mika-answer ::= "root" [root] mika-answer? other-root # root is recursive\n'
    )


@pytest.mark.parametrize("axis", ["thought", "answer"])
async def test_reasoning_and_final_answer_have_separate_limits(request_data, axis):
    server = Server()
    if axis == "thought":
        server.thought_size = 68
    else:
        server.answer_size = 21
    async with LocalLLM(
        settings=Settings(), transport=httpx.MockTransport(server.handle)
    ) as llm:
        with pytest.raises(ValueError, match="budget"):
            await llm.generate(request_data, max_tokens=20)


async def test_zero_reasoning_budget_still_allows_final_answer(request_data):
    server = Server()
    server.thought_size = 0
    server.thought = ""
    async with LocalLLM(
        settings=Settings(**{"llm.reasoning_budget_tokens": 0}),
        transport=httpx.MockTransport(server.handle),
    ) as llm:
        assert await llm.generate(request_data) == server.answer
    assert server.completions[0]["reasoning_budget_tokens"] == 0


async def test_reasoning_rejects_unsupported_model_before_generation(request_data):
    server = Server()
    server.template = "Unsupported template fixture"
    async with LocalLLM(
        settings=Settings(), transport=httpx.MockTransport(server.handle)
    ) as llm:
        with pytest.raises(ValueError, match="Gemma"):
            await llm.generate(request_data)
    assert not server.completions


async def test_reasoning_timeout_bounds_all_retries(request_data, tmp_path):
    server = Server()

    async def slow(request):
        if request.url.path == "/completion":
            await asyncio.sleep(10)
        return server.handle(request)

    database = Database(tmp_path / "timeout.sqlite3")
    database.initialize()
    async with LocalLLM(
        settings=Settings(**{"llm.reasoning_timeout_sec": 0.01}),
        database=database,
        transport=httpx.MockTransport(slow),
    ) as llm:
        with pytest.raises(TimeoutError):
            await llm.generate(request_data)
    with database.connection() as c:
        assert c.execute("SELECT status FROM runs").fetchone()[0] == "failed"
