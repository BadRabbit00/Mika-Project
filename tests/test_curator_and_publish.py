"""Publication contracts, declared before the delivery implementation."""

import asyncio
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import structlog
from pydantic import BaseModel, ConfigDict

from src.bot import BotIngress, post_buttons
from src.core.db import Database
from src.core.llm_vendor import ClaudeCodeBackend, VendorConfig, extract_json
from src.core.tasks import JobQueue
from src.core.telegram import TelegramLayout, TelegramTransport
from src.core.time_utils import ALMATY
from src.publish import DeliveryRejected, Destination, OutboxWorker, Publisher

AT = datetime(2026, 9, 16, 19, tzinfo=ALMATY)
DIARY = Destination("diary", "mika", -100123, 11, primary=True)
CHANNEL = Destination("channel", "mika", -100124)


@pytest.fixture
def database(tmp_path):
    database = Database(tmp_path / "interfaces.sqlite3")
    database.initialize()
    database.run_transaction(
        lambda c: c.execute(
            "INSERT INTO posts(id, kind, state, text) "
            "VALUES ('p', 'summary', 'draft', ?)",
            ("A validated draft for the publication transport contract.",),
        )
    )
    return database


def rows(database):
    with database.connection() as connection:
        return [
            dict(row) for row in connection.execute("SELECT * FROM outbox ORDER BY id")
        ]


async def test_outbox_worker_restart_does_not_duplicate_telegram(database):
    publisher = Publisher(database)
    first = publisher.enqueue_post("p", [DIARY, CHANNEL], trace_id="trace-1", at=AT)
    assert (
        publisher.enqueue_post("p", [DIARY, CHANNEL], trace_id="trace-1", at=AT)
        == first
    )
    transport = AsyncMock()
    transport.send.side_effect = [100, 101]
    worker = OutboxWorker(database, transport)
    assert await worker.run_once(at=AT) == "sent"
    assert await worker.run_once(at=AT) == "sent"
    assert await OutboxWorker(database, transport).run_once(at=AT) == "idle"
    assert transport.send.await_count == 2
    assert all(row["sent_at"].endswith("Z") for row in rows(database))
    with database.connection() as connection:
        post = connection.execute("SELECT * FROM posts WHERE id='p'").fetchone()
        assert post["state"] == "published" and post["tg_message_id"] == 100


async def test_outbox_crash_after_remote_acceptance_is_not_retried(database):
    Publisher(database).enqueue_post("p", [DIARY], trace_id="trace-1", at=AT)
    calls = []

    async def accepted_but_disconnected(payload):
        calls.append(payload)
        raise TimeoutError("Acceptance is unknown to the sender")

    transport = AsyncMock()
    transport.send.side_effect = accepted_but_disconnected
    assert await OutboxWorker(database, transport).run_once(at=AT) == "uncertain"
    restarted = OutboxWorker(database, transport)
    assert await restarted.run_once(at=AT + timedelta(days=1)) == "idle"
    assert len(calls) == 1 and len(restarted.uncertain()) == 1
    restarted.reconcile(rows(database)[0]["id"], message_id=900, at=AT)
    assert await restarted.run_once(at=AT) == "idle"
    assert rows(database)[0]["tg_message_id"] == 900


async def test_outbox_receipt_commit_failure_does_not_resend(database, monkeypatch):
    Publisher(database).enqueue_post("p", [DIARY], trace_id="trace-1", at=AT)
    transport = AsyncMock()
    transport.send.return_value = 99
    worker = OutboxWorker(database, transport)

    def crash(*args, **kwargs):
        raise RuntimeError("Process stopped before receipt commit")

    monkeypatch.setattr(worker, "reconcile", crash)
    with pytest.raises(RuntimeError):
        await worker.run_once(at=AT)
    assert await OutboxWorker(database, transport).run_once(at=AT) == "idle"
    transport.send.assert_awaited_once()


async def test_outbox_existing_receipt_is_recovered_without_network(database):
    Publisher(database).enqueue_post("p", [DIARY], trace_id="trace-1", at=AT)
    database.run_transaction(
        lambda c: c.execute("UPDATE outbox SET tg_message_id=71, attempts=1 WHERE id=1")
    )
    transport = AsyncMock()
    assert await OutboxWorker(database, transport).run_once(at=AT) == "recovered"
    transport.send.assert_not_awaited()
    assert rows(database)[0]["sent_at"].endswith("Z")


async def test_outbox_concurrent_workers_claim_once(database):
    Publisher(database).enqueue_post("p", [DIARY], trace_id="trace-1", at=AT)
    transport = AsyncMock()
    transport.send.return_value = 20
    results = await asyncio.gather(
        *(OutboxWorker(database, transport).run_once(at=AT) for _ in range(4))
    )
    assert sorted(results) == ["idle", "idle", "idle", "sent"]
    transport.send.assert_awaited_once()


async def test_publication_rechecks_blackout_after_draft_creation(database):
    Publisher(database).enqueue_post("p", [DIARY], trace_id="blackout", at=AT)
    transport = AsyncMock()
    allowed = AsyncMock(return_value=False)
    worker = OutboxWorker(database, transport, allowed=allowed)
    assert await worker.run_once(at=AT) == "deferred"
    transport.send.assert_not_awaited()
    assert not worker.uncertain()
    allowed.return_value = True
    transport.send.return_value = 74
    assert await worker.run_once(at=AT + timedelta(minutes=1)) == "sent"


async def test_outbox_retries_only_confirmed_rejection(database):
    Publisher(database).enqueue_post("p", [DIARY], trace_id="trace-1", at=AT)
    transport = AsyncMock()
    transport.send.side_effect = [DeliveryRejected("Flood control", retry_after=30), 33]
    worker = OutboxWorker(database, transport)
    assert await worker.run_once(at=AT) == "retry"
    assert await worker.run_once(at=AT + timedelta(seconds=29)) == "idle"
    assert await worker.run_once(at=AT + timedelta(seconds=30)) == "sent"
    assert rows(database)[0]["attempts"] == 2


async def test_publication_trace_survives_enqueue_and_worker_restart(database):
    Publisher(database).enqueue_post("p", [DIARY], trace_id="chain-123", at=AT)
    transport = AsyncMock()

    async def send(payload):
        assert payload["trace_id"] == "chain-123"
        assert structlog.contextvars.get_contextvars()["trace_id"] == "chain-123"
        return 34

    transport.send.side_effect = send
    await OutboxWorker(database, transport).run_once(at=AT)
    assert json.loads(rows(database)[0]["payload"])["trace_id"] == "chain-123"


def test_publication_refuses_killed_or_changed_drafts(database):
    publisher = Publisher(database)
    publisher.enqueue_post("p", [DIARY], trace_id="chain-123", at=AT)
    database.run_transaction(
        lambda c: c.execute("UPDATE posts SET text='Changed' WHERE id='p'")
    )
    with pytest.raises(ValueError, match="different payload"):
        publisher.enqueue_post("p", [DIARY], trace_id="chain-123", at=AT)
    database.run_transaction(
        lambda c: c.execute("UPDATE posts SET state='killed' WHERE id='p'")
    )
    with pytest.raises(ValueError, match="publishable"):
        publisher.enqueue_post("p", [DIARY], trace_id="chain-123", at=AT)
    assert len(rows(database)) == 1


def test_publication_rejects_naive_time_before_mutation(database):
    with pytest.raises(ValueError, match="aware"):
        Publisher(database).enqueue_post(
            "p", [DIARY], trace_id="trace", at=AT.replace(tzinfo=None)
        )
    assert rows(database) == []


class VendorFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    verdict: str


def test_claude_cli_uses_temporary_files_and_validates_json(monkeypatch):
    seen = []
    user = "Article material. " * 100_000
    system = Path("prompts/curator_system.md").read_text()

    def run(command, **kwargs):
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["check"] and kwargs["timeout"] == 300
        assert "input" not in kwargs and "shell" not in kwargs
        assert command[command.index("--model") + 1] == "sonnet"
        assert command[command.index("--effort") + 1] == "medium"
        request_file = Path(command[command.index("-p") + 1].removeprefix("@"))
        system_file = Path(command[command.index("--append-system-prompt-file") + 1])
        assert request_file.read_text() == user and system_file.read_text() == system
        assert request_file.stat().st_mode & 0o777 == 0o600
        assert max(map(len, command)) < 1000
        tools = command[command.index("--tools") + 1]
        assert all(name not in tools.split(",") for name in ("Bash", "Edit", "Write"))
        assert (
            "--strict-mcp-config" in command and "--no-session-persistence" in command
        )
        seen.extend((request_file, system_file))
        return SimpleNamespace(
            stdout=json.dumps(
                {
                    "result": 'A result follows.\n```json\n{"verdict":"pass"}\n```',
                    "total_cost_usd": 0.0123,
                }
            ),
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr(subprocess, "run", run)
    backend = ClaudeCodeBackend(
        VendorConfig.from_registry(Path("config/settings.yaml"), timeout_sec=300)
    )
    result = backend.ask(system, user, VendorFixture, trace_id="exam-chain")
    assert result.value.verdict == "pass" and result.cost_usd == 0.0123
    assert all(not path.exists() for path in seen)


@pytest.mark.parametrize(
    "raw",
    [
        '{"verdict":"pass","verdict":"fail"}',
        '{"verdict":"pass"} {"verdict":"fail"}',
        '{"verdict":12}',
        '{"verdict":"pass","extra":true}',
        '{"verdict":NaN}',
        "not JSON",
    ],
)
def test_extract_json_rejects_ambiguous_or_invalid_results(raw):
    with pytest.raises(ValueError):
        extract_json(raw, VendorFixture)


def test_claude_retries_invalid_output_once_and_accounts_for_both_calls(monkeypatch):
    requests = []

    def run(command, **kwargs):
        request = Path(command[command.index("-p") + 1][1:]).read_text()
        requests.append(request)
        return SimpleNamespace(
            stdout=json.dumps(
                {
                    "result": "invalid" if len(requests) == 1 else '{"verdict":"pass"}',
                    "total_cost_usd": 0.01,
                }
            ),
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr(subprocess, "run", run)
    backend = ClaudeCodeBackend(
        VendorConfig.from_registry(Path("config/settings.yaml"), timeout_sec=300)
    )
    result = backend.ask(
        "Fixture system", "Fixture task", VendorFixture, trace_id="retry-chain"
    )
    assert result.cost_usd == 0.02 and result.attempts == 2
    assert "validation_error" in requests[1]


def test_curator_does_not_repeat_timeout_as_json_repair(monkeypatch):
    run = __import__("unittest.mock", fromlist=["Mock"]).Mock(
        side_effect=subprocess.TimeoutExpired("claude", 300)
    )
    monkeypatch.setattr(subprocess, "run", run)
    backend = ClaudeCodeBackend(
        VendorConfig.from_registry(Path("config/settings.yaml"), timeout_sec=300)
    )
    with pytest.raises(subprocess.TimeoutExpired):
        backend.ask(
            "Fixture system", "Fixture task", VendorFixture, trace_id="timeout-chain"
        )
    assert run.call_count == 1


@pytest.mark.parametrize(
    "stderr,category",
    [
        ("usage limit reached", "limit"),
        ("rate limit", "limit"),
        ("quota exhausted", "limit"),
        ("Please login", "auth"),
        ("unauthorized", "auth"),
        ("unexpected failure", "unknown"),
    ],
)
def test_curator_classifies_cli_failure_without_schema_retry(
    monkeypatch, stderr, category
):
    from unittest.mock import Mock

    from src.core.llm_vendor import CuratorFailure

    run = Mock(side_effect=subprocess.CalledProcessError(1, "claude", stderr=stderr))
    monkeypatch.setattr(subprocess, "run", run)
    backend = ClaudeCodeBackend(
        VendorConfig.from_registry(Path("config/settings.yaml"))
    )
    with pytest.raises(CuratorFailure) as error:
        backend.ask("Fixture system", "Fixture task", VendorFixture, trace_id="failure")
    assert error.value.category == category
    assert run.call_count == 1


def test_curator_reads_settings_each_call_and_persists_receipts(database, monkeypatch):
    from src.core.settings import SettingsRegistry, SQLiteSettingsStore

    settings = SettingsRegistry.from_file(
        Path("config/settings.yaml"), store=SQLiteSettingsStore(database)
    )
    seen = []

    def run(command, **kwargs):
        seen.append(kwargs["timeout"])
        return SimpleNamespace(
            stdout=json.dumps({"result": '{"verdict":"pass"}', "total_cost_usd": 0.01}),
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr(subprocess, "run", run)
    backend = ClaudeCodeBackend(settings, database=database)
    backend.ask("Fixture system", "Fixture task", VendorFixture, trace_id="chain")
    settings.set("curator.timeout_sec", "600", trace_id="setting")
    backend.ask("Fixture system", "Fixture task", VendorFixture, trace_id="chain")
    assert seen == [300, 600]
    with database.connection() as c:
        assert (
            c.execute(
                "SELECT count(DISTINCT call_id) FROM runs WHERE trace_id='chain'"
            ).fetchone()[0]
            == 2
        )


def test_curator_repairs_malformed_cli_envelope_once(monkeypatch):
    from unittest.mock import Mock

    run = Mock(
        side_effect=[
            SimpleNamespace(stdout="{broken", stderr="", returncode=0),
            SimpleNamespace(
                stdout='{"result":"{\\"verdict\\":\\"pass\\"}","total_cost_usd":0.01}',
                stderr="",
                returncode=0,
            ),
        ]
    )
    monkeypatch.setattr(subprocess, "run", run)
    backend = ClaudeCodeBackend(
        VendorConfig.from_registry(Path("config/settings.yaml"))
    )
    assert (
        backend.ask(
            "Fixture system", "Fixture task", VendorFixture, trace_id="schema"
        ).attempts
        == 2
    )


def telegram_layout():
    return TelegramLayout(
        owner_id=123,
        group_id=-100123,
        channel_id=-100124,
        topics=dict(
            diary=11, author=12, curator=13, chat=14, library=15, machine=16, control=17
        ),
    )


async def test_diary_handler_ignores_user_messages():
    from aiogram.types import Chat, Message, User

    service = AsyncMock()
    jobs = JobQueue()
    ingress = BotIngress(
        telegram_layout(), {10: "mika", 20: "curator", 30: "ops"}, jobs, service
    )
    message = Message(
        message_id=1,
        date=AT,
        chat=Chat(id=-100123, type="supergroup"),
        message_thread_id=11,
        from_user=User(id=123, is_bot=False, first_name="Owner"),
        text="/state",
    )
    await ingress.on_message(message, SimpleNamespace(id=10))
    assert jobs.pending == 0
    service.command.assert_not_awaited()
    service.chat.assert_not_awaited()


async def test_model_calls_use_task_queue():
    from aiogram.types import Chat, Message, User

    service = AsyncMock()
    jobs = JobQueue()
    ingress = BotIngress(telegram_layout(), {30: "ops"}, jobs, service)
    message = Message(
        message_id=2,
        date=AT,
        chat=Chat(id=-100123, type="supergroup"),
        message_thread_id=17,
        from_user=User(id=123, is_bot=False, first_name="Owner"),
        text="/health",
    )
    await ingress.on_message(message, SimpleNamespace(id=30))
    service.command.assert_not_awaited()
    assert jobs.pending == 1
    await jobs.start()
    await jobs.join()
    await jobs.close()
    service.command.assert_awaited_once()


@pytest.mark.parametrize(
    "owner,topic,bot", [(456, 17, 30), (123, 12, 30), (123, 13, 20), (123, 17, 10)]
)
async def test_routing_rejects_wrong_owner_topic_or_bot(owner, topic, bot):
    from aiogram.types import Chat, Message, User

    jobs = JobQueue()
    ingress = BotIngress(
        telegram_layout(), {10: "mika", 20: "curator", 30: "ops"}, jobs, AsyncMock()
    )
    message = Message(
        message_id=3,
        date=AT,
        chat=Chat(id=-100123, type="supergroup"),
        message_thread_id=topic,
        from_user=User(id=owner, is_bot=False, first_name="User"),
        text="/state",
    )
    await ingress.on_message(message, SimpleNamespace(id=bot))
    assert jobs.pending == 0


async def test_job_trace_is_inherited_by_threaded_work():
    seen = []
    jobs = JobQueue()

    async def work():
        seen.append(await asyncio.to_thread(structlog.contextvars.get_contextvars))

    jobs.submit("trace-root", "fixture", work)
    await jobs.start()
    await jobs.join()
    await jobs.close()
    assert seen[0]["trace_id"] == "trace-root"


@pytest.mark.parametrize(
    "owner,topic,bot,accepted",
    [(123, 17, 30, True), (456, 17, 30, False), (123, 11, 10, False)],
)
async def test_settings_callback_is_owner_only_and_queued(owner, topic, bot, accepted):
    from aiogram.types import Chat, Message, User

    jobs, service = JobQueue(), AsyncMock()
    ingress = BotIngress(telegram_layout(), {10: "mika", 30: "ops"}, jobs, service)
    message = Message(
        message_id=4,
        date=AT,
        chat=Chat(id=-100123, type="supergroup"),
        message_thread_id=topic,
    )
    query = SimpleNamespace(
        id="settings-query",
        from_user=User(id=owner, is_bot=False, first_name="User"),
        message=message,
        data="settings:view:study",
        answer=AsyncMock(),
    )
    await ingress.on_callback(query, SimpleNamespace(id=bot))
    assert jobs.pending == int(accepted)
    service.settings_view.assert_not_awaited()
    if accepted:
        query.answer.assert_awaited_once()
        await jobs.start()
        await jobs.join()
        await jobs.close()
        service.settings_view.assert_awaited_once()
        assert service.settings_view.call_args.args[:2] == (message, "study")
    else:
        query.answer.assert_not_awaited()


async def test_dependent_pin_waits_for_message_receipt(database):
    publisher = Publisher(database)
    destination = Destination("control", "ops", -100123, 17)
    card = publisher.enqueue_operation(
        "card",
        destination,
        trace_id="defect",
        method="message",
        text="Defect card",
        at=AT,
    )
    publisher.enqueue_operation(
        "pin", destination, trace_id="defect", method="pin", depends_on=card, at=AT
    )
    transport = AsyncMock()
    transport.send.side_effect = [77, 77]
    worker = OutboxWorker(database, transport)
    assert await worker.run_once(at=AT) == "sent"
    assert await worker.run_once(at=AT) == "sent"
    assert transport.send.call_args.args[0]["message_id"] == 77


def test_post_buttons_expose_required_actions():
    draft = post_buttons("post-id")["inline_keyboard"][0]
    assert [button["text"] for button in draft] == ["Опубликовать", "Перегенерировать"]
    assert (
        post_buttons("post-id", published=True)["inline_keyboard"][0][0]["text"]
        == "Брак"
    )


async def test_telegram_transport_uses_the_designated_bot_and_topic():
    bots = {name: AsyncMock() for name in ("mika", "curator", "ops")}
    bots["curator"].send_message.return_value = SimpleNamespace(message_id=83)
    transport = TelegramTransport(bots)
    message_id = await transport.send(
        {
            "method": "message",
            "trace_id": "trace",
            "destination": {
                "channel": "curator",
                "bot": "curator",
                "chat_id": -100123,
                "topic_id": 13,
                "primary": False,
            },
            "text": "A recorded verdict.",
        }
    )
    assert message_id == 83
    assert bots["curator"].send_message.call_args.kwargs["message_thread_id"] == 13
    bots["mika"].send_message.assert_not_awaited()
    bots["ops"].send_message.assert_not_awaited()
