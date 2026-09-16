"""Bounded background jobs that preserve the originating trace context."""

import asyncio
from dataclasses import dataclass

import structlog

log = structlog.get_logger("blogai.tasks")


@dataclass
class Job:
    trace_id: str
    kind: str
    work: object
    status: str = "queued"


class JobQueue:
    def __init__(self, *, capacity: int = 64):
        self.queue = asyncio.Queue(maxsize=capacity)
        self.jobs = {}
        self._worker = None

    @property
    def pending(self):
        return self.queue.qsize()

    def submit(self, trace_id, kind, work):
        key = (trace_id, kind)
        if key in self.jobs:
            return self.jobs[key]
        job = Job(trace_id, kind, work)
        self.queue.put_nowait(job)
        self.jobs[key] = job
        log.info("job_queued", trace_id=trace_id, kind=kind)
        return job

    async def start(self):
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())

    async def _run(self):
        while True:
            job = await self.queue.get()
            if job is None:
                self.queue.task_done()
                return
            with structlog.contextvars.bound_contextvars(trace_id=job.trace_id):
                job.status = "running"
                log.info("job_started", kind=job.kind)
                try:
                    await job.work()
                except Exception:
                    job.status = "failed"
                    log.exception("job_failed", kind=job.kind)
                else:
                    job.status = "succeeded"
                    log.info("job_completed", kind=job.kind)
                finally:
                    self.queue.task_done()

    async def join(self):
        await self.queue.join()

    async def close(self):
        if self._worker is not None:
            await self.queue.put(None)
            await self._worker
            self._worker = None
