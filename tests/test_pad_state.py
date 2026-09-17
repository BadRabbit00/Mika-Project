"""PAD geometry and durable replacement of the operator's single state message."""

import io
import json
import threading
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mpl_toolkits.mplot3d import proj3d
from PIL import Image
from ruamel.yaml import YAML

from src.core.db import Database, enqueue_outbox
from src.core.pad_plot import PadPlot, make_figure, render_png
from src.core.telegram import TelegramTransport
from src.core.time_utils import ALMATY
from src.core.world_journal import WorldJournal
from src.publish import Destination, OutboxWorker

AT = datetime(2026, 9, 17, 9, tzinfo=ALMATY)
TARGET = Destination("state", "ops", -100, 1266)


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "state.sqlite3")
    db.initialize()
    return db


def spec(**mood):
    return PadPlot.from_config().snapshot(mood or {"P": 0.45, "A": -0.3, "D": 0.65})


def test_pad_geometry_axis_mapping_octants_and_fixed_camera():
    snapshot = spec()
    figure, axes = make_figure(snapshot)
    assert axes.get_xlabel().startswith("A")
    assert axes.get_ylabel().startswith("D")
    assert axes.get_zlabel().startswith("P")
    assert (axes.elev, axes.azim, axes.roll) == (22, 30, 0)
    assert axes.get_zlim() == (-1.2, 1.2)
    assert snapshot["vector"] == [-0.3, 0.65, 0.45]
    names = YAML(typ="safe").load(Path("config/mood.yaml"))["octants"]
    assert {key: item["name"] for key, item in snapshot["octants"].items()} == {
        key: item["name"] for key, item in names.items()
    }
    assert len({item["color"] for item in snapshot["octants"].values()}) == 8
    assert snapshot["octants"]["-++"]["color"] == "#e53935"
    assert snapshot["octants"]["--+"]["color"] == "#ed8b23"
    assert all(line.get_linestyle() != "--" for line in axes.lines)
    origin = proj3d.proj_transform(0, 0, 0, axes.get_proj())
    upper = proj3d.proj_transform(0, 0, 1, axes.get_proj())
    assert upper[0] == pytest.approx(origin[0])
    assert upper[1] > origin[1]
    assert "P = +0.4500" in " ".join(text.get_text() for text in figure.texts)
    figure.clear()
    other, other_axes = make_figure(spec(P=-1, A=1, D=-1))
    assert (other_axes.elev, other_axes.azim, other_axes.roll) == (22, 30, 0)
    other.clear()


@pytest.mark.parametrize("values", [(0, 0, 0), (-1, 1, -1), (0.0001, 0.9999, -0.42)])
def test_pad_png_is_reproducible_and_within_telegram_limits(values):
    from src.core.pad_plot import _cached_png

    snapshot = spec(**dict(zip(("P", "A", "D"), values, strict=True)))
    saved = json.dumps(snapshot)
    first = render_png(json.loads(saved))
    _cached_png.cache_clear()
    assert first == render_png(json.loads(saved))
    assert len(first) < 10_000_000
    with Image.open(io.BytesIO(first)) as image:
        assert image.format == "PNG"
        assert sum(image.size) <= 10000
        assert image.convert("RGB").getpixel((0, 0)) == (255, 255, 255)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 1.001, -1.001])
def test_invalid_mood_cannot_be_plotted(value):
    with pytest.raises(ValueError):
        spec(P=value, A=0, D=0)


async def test_state_document_upgrade_reuses_receipt_even_without_world_change(
    database,
):
    snapshot = {"cash": 20000, "mood": {"P": 0.2, "A": -0.1, "D": 0.3}}
    journal = WorldJournal(database, state_destination=TARGET)
    journal.observe(snapshot, AT, cause="start")

    def legacy(c):
        row = enqueue_outbox(
            c,
            post_id="world-state:-100:1266:1",
            channel="state",
            next_try_at=AT,
            payload={
                "method": "document",
                "destination": asdict(TARGET),
                "world_state": True,
                "world_sequence": 1,
            },
        )
        c.execute("UPDATE outbox SET sent_at=?,tg_message_id=321 WHERE id=?", (AT, row))

    database.run_transaction(legacy)
    journal.flush(AT)
    with database.connection(readonly=True) as c:
        rows = [json.loads(row[0]) for row in c.execute("SELECT payload FROM outbox")]
    assert [row["method"] for row in rows] == ["document", "pin", "edit_photo"]
    assert rows[-1]["message_id"] == 321
    assert rows[-1]["pad_plot"]["vector"] == [-0.1, 0.3, 0.2]
    send = AsyncMock(return_value=321)
    worker = OutboxWorker(database, SimpleNamespace(send=send), clock=lambda: AT)
    assert await worker.run_once(at=AT) == "sent"
    assert await worker.run_once(at=AT + timedelta(seconds=5)) == "sent"
    WorldJournal(database, state_destination=TARGET).flush(AT + timedelta(seconds=10))
    assert await worker.run_once(at=AT + timedelta(seconds=10)) == "idle"


async def test_inflight_photo_coalesces_to_latest_committed_state(database):
    journal = WorldJournal(database, state_destination=TARGET)
    initial = {"mood": {"P": 0.2, "A": -0.1, "D": 0.3}}
    journal.observe(initial, AT, cause="start")
    journal.flush(AT)
    changed = {"mood": {"P": 0.2001, "A": -0.1, "D": 0.3}}
    journal.observe(changed, AT + timedelta(seconds=1), cause="event")
    journal.flush(AT + timedelta(seconds=1))
    with database.connection(readonly=True) as c:
        assert c.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1
    send = AsyncMock(return_value=321)
    worker = OutboxWorker(database, SimpleNamespace(send=send), clock=lambda: AT)
    assert await worker.run_once(at=AT) == "sent"
    restarted = WorldJournal(database, state_destination=TARGET)
    restarted.flush(AT + timedelta(seconds=5))
    with database.connection(readonly=True) as c:
        edit = json.loads(
            c.execute("SELECT payload FROM outbox ORDER BY id DESC").fetchone()[0]
        )
    assert edit["method"] == "edit_photo" and edit["message_id"] == 321
    assert edit["pad_plot"]["mood"]["P"] == 0.2001
    assert json.loads(edit["content"])["mood"] == changed["mood"]


@pytest.mark.parametrize("method", ["photo", "edit_photo"])
async def test_photo_rendering_is_off_event_loop_and_uses_saved_message(
    monkeypatch, method
):
    event_loop_thread = threading.get_ident()
    seen_threads = []

    def render(snapshot):
        seen_threads.append(threading.get_ident())
        return b"PNG fixture"

    monkeypatch.setattr("src.core.telegram.render_png", render)
    bot = SimpleNamespace(
        send_photo=AsyncMock(return_value=SimpleNamespace(message_id=321)),
        edit_message_media=AsyncMock(return_value=SimpleNamespace(message_id=321)),
    )
    payload = {
        "method": method,
        "destination": asdict(TARGET),
        "pad_plot": spec(),
        "caption": "Mika state",
        "message_id": 321,
    }
    assert await TelegramTransport({"ops": bot}).send(payload) == 321
    assert seen_threads and seen_threads[0] != event_loop_thread
    if method == "photo":
        args = bot.send_photo.await_args.kwargs
        assert args["message_thread_id"] == 1266
        assert args["photo"].data == b"PNG fixture"
    else:
        args = bot.edit_message_media.await_args.kwargs
        assert args["message_id"] == 321 and args["chat_id"] == -100
        assert args["media"].type == "photo"
        assert args["media"].media.data == b"PNG fixture"
        assert args["media"].caption == "Mika state"


async def test_committed_mood_refreshes_state_within_same_minute(database, monkeypatch):
    from src.life import LifeRuntime
    from src.providers import RuntimeProviders

    at = AT.replace(hour=18, minute=40)
    providers = RuntimeProviders(database, Path("config"), clock=lambda: at)
    runtime = LifeRuntime(providers, None, None, [], state_destination=TARGET)
    monkeypatch.setattr(runtime, "_next", lambda at: None)
    try:
        await runtime.tick(at)
        with database.connection(readonly=True) as c:
            previous = c.execute("SELECT max(sequence) FROM world_changes").fetchone()[
                0
            ]
        changed = at + timedelta(seconds=2)
        providers.mood.service.record_event(
            "understood_something", at=changed, context=providers.mood.history(changed)
        )
        await runtime.tick(changed)
        with database.connection(readonly=True) as c:
            row = c.execute(
                "SELECT * FROM world_changes ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
        assert row["sequence"] > previous
        mood = await providers.mood.current(changed)
        assert json.loads(row["snapshot"])["mood"] == {
            axis: round(getattr(mood, axis), 4) for axis in ("P", "A", "D")
        }
    finally:
        await runtime.close()
        await providers.close()


async def test_identical_photo_acknowledgement_does_not_block_future_updates(
    monkeypatch,
):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import EditMessageMedia
    from aiogram.types import InputMediaPhoto

    method = EditMessageMedia(
        chat_id=-100, message_id=321, media=InputMediaPhoto(media="existing-photo")
    )
    bot = SimpleNamespace(
        edit_message_media=AsyncMock(
            side_effect=TelegramBadRequest(
                method=method, message="Bad Request: message is not modified"
            )
        )
    )
    monkeypatch.setattr("src.core.telegram.render_png", lambda snapshot: b"PNG fixture")
    assert (
        await TelegramTransport({"ops": bot}).send(
            {
                "destination": asdict(TARGET),
                "method": "edit_photo",
                "message_id": 321,
                "pad_plot": spec(),
                "caption": "Mika state",
            }
        )
        == 321
    )


def test_full_world_remains_available_from_state_command(database):
    from src.commands import CommandService

    snapshot = {"cash": 20000, "mood": {"P": 0.2, "A": -0.1, "D": 0.3}}
    WorldJournal(database).observe(snapshot, AT, cause="start")
    service = CommandService(database, None, None, None, log_path="unused.jsonl")
    result = service._state()
    assert result["world"]["snapshot"] == snapshot
    assert result["world"]["observed_at"].endswith("Z")
