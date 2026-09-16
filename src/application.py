"""APScheduler lifecycle for durable actions and the existing background queue."""

import asyncio
import json

from src.core.time_utils import now, require_aware
from src.runner import GENERATION_ACTIONS


class LearningApplication:
    def __init__(self, runner, activity, *, clock=now, poll_seconds=1):
        if poll_seconds <= 0:
            raise ValueError("A positive polling interval is required")
        self.runner, self.activity, self.clock = runner, activity, clock
        self.poll_seconds, self._queued = poll_seconds, set()

    async def start(self, *, paused=False):
        """Start the exclusive owner of this database's learning actions."""
        await asyncio.to_thread(self.runner.store.recover_interrupted)
        self.activity.scheduler.add_job(
            self.tick,
            "interval",
            seconds=self.poll_seconds,
            id="learning-actions",
            replace_existing=True,
        )
        self.activity.start(paused=paused)

    async def tick(self):
        at = require_aware(self.clock())
        row = await asyncio.to_thread(self.runner.store.peek, at)
        if row is None or row["id"] in self._queued:
            return False
        action = json.loads(row["action_json"])

        async def execute():
            try:
                await self.runner.run_once(at=self.clock(), identity=row["id"])
            finally:
                self._queued.discard(row["id"])

        if action["kind"] in GENERATION_ACTIONS:
            return await self.activity.enqueue(
                row["id"], execute, trace_id=action["trace_id"]
            )
        self._queued.add(row["id"])
        try:
            self.activity.jobs.submit(
                action["trace_id"], f"action:{row['id']}:{row['attempts']}", execute
            )
        except asyncio.QueueFull:
            self._queued.discard(row["id"])
            return False
        return True

    async def close(self):
        await self.activity.close()
