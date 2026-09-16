"""Almaty activity windows, lognormal pauses, blackout gates, and thread order."""

import asyncio
from datetime import datetime, timedelta
from random import Random
from unittest.mock import AsyncMock

import pytest

from src.core.schedule import Blackout
from src.core.tasks import JobQueue
from src.core.time_utils import ALMATY, now
from src.scheduler import ActivityScheduler, Rhythm, Thread, next_post_kind

AT = datetime(2026, 9, 16, 19, 30, tzinfo=ALMATY)
RHYTHM = {
    "sessions": {
        "weekday": [
            {"start": "10:00", "len_min": 90},
            {"start": "19:30", "len_min": 120},
        ],
        "weekend": [{"start": "13:00", "len_min": 150}],
    },
    "quiet_hours": ["23:30", "09:00"],
    "gap": {"dist": "lognormal", "mu": 2.6, "sigma": 0.7, "min_min": 4, "max_min": 40},
    "max_events_per_session": 6,
}


def test_rhythm_uses_literal_lognormal_gaps_and_aware_windows():
    rhythm = Rhythm.from_mapping(RHYTHM)
    left, right = Random(7), Random(7)
    for _ in range(50):
        assert rhythm.gap_minutes(left) == min(
            40, max(4, right.lognormvariate(2.6, 0.7))
        )
    assert rhythm.window(AT) is not None
    assert rhythm.window(AT.replace(hour=23, minute=45)) is None
    assert rhythm.window(AT.replace(hour=9)) is None
    assert rhythm.next_window(AT.replace(hour=23)).start.tzinfo == ALMATY
    with pytest.raises(ValueError, match="aware"):
        rhythm.window(AT.replace(tzinfo=None))


async def test_blackout_is_checked_before_queue_and_again_before_execution():
    jobs, work = JobQueue(), AsyncMock()
    blocked = False

    def blackout(at):
        return Blackout(blocked, "sleep" if blocked else None)

    scheduler = ActivityScheduler(
        Rhythm.from_mapping(RHYTHM), jobs, blackout=blackout, clock=lambda: AT
    )
    blocked = True
    assert not await scheduler.enqueue("job", work, trace_id="trace")
    assert jobs.pending == 0
    blocked = False
    assert await scheduler.enqueue("job", work, trace_id="trace")
    blocked = True
    await jobs.start()
    await jobs.join()
    await jobs.close()
    work.assert_not_awaited()


async def test_session_event_limit_and_apscheduler_timezone():
    jobs = JobQueue()
    scheduler = ActivityScheduler(
        Rhythm.from_mapping(RHYTHM),
        jobs,
        blackout=lambda at: Blackout(False),
        clock=lambda: AT,
    )
    for index in range(6):
        assert await scheduler.enqueue(str(index), AsyncMock(), trace_id=str(index))
    assert not await scheduler.enqueue("seventh", AsyncMock(), trace_id="seventh")
    scheduler.start(paused=True)
    assert scheduler.scheduler.timezone == ALMATY
    await scheduler.close()


async def test_concurrent_admission_and_capacity_rechecked_across_windows():
    jobs, work, current = JobQueue(), AsyncMock(), AT
    scheduler = ActivityScheduler(
        Rhythm.from_mapping(RHYTHM),
        jobs,
        blackout=lambda at: Blackout(False),
        clock=lambda: current,
    )
    results = await asyncio.gather(
        *(scheduler.enqueue("same", work, trace_id="same") for _ in range(3))
    )
    assert sum(results) == 1 and jobs.pending == 1
    current = AT + timedelta(days=1)
    window = scheduler.rhythm.window(current)
    for index in range(6):
        scheduler.reservations.reserve(str(index), window, current, 6)
    await jobs.start()
    await jobs.join()
    await jobs.close()
    work.assert_not_awaited()


async def test_apscheduler_dispatches_a_date_job_through_the_background_queue():
    jobs, completed = JobQueue(), asyncio.Event()
    scheduler = ActivityScheduler(
        Rhythm.from_mapping(RHYTHM),
        jobs,
        blackout=lambda at: Blackout(False),
        clock=lambda: AT,
    )

    async def work():
        completed.set()

    await jobs.start()
    scheduler.start()
    try:
        scheduler.schedule("native-date", work, at=now(), trace_id="native")
        await asyncio.wait_for(completed.wait(), timeout=3)
        await jobs.join()
        assert any(job.status == "succeeded" for job in jobs.jobs.values())
    finally:
        await scheduler.close()
        await jobs.close()


def test_thread_selection_follows_architecture_order_and_expires_old_threads():
    correction = Thread(1, "correction", AT - timedelta(hours=2))
    confusion = Thread(2, "confusion", AT - timedelta(hours=21))
    question = Thread(3, "question", AT - timedelta(hours=1))
    args = dict(at=AT, state="WAITING", offtop_due=True, inbox_new=True)
    assert next_post_kind([correction, confusion, question], **args) == "correction"
    assert next_post_kind([confusion, question], **args) == "struggle"
    assert next_post_kind([Thread(4, "confusion", AT)], **args) is None
    args["state"] = "SUMMARY_READY"
    assert next_post_kind([question], **args) == "summary"
    args["state"] = "IDLE"
    assert next_post_kind([question], **args) == "offtop"
    args["offtop_due"] = False
    assert next_post_kind([question], **args) == "answer"
    assert (
        next_post_kind([Thread(5, "correction", AT - timedelta(days=6))], **args)
        == "found"
    )
