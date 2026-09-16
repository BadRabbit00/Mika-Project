"""Autonomous life ticks and independent, durable everyday publication work."""

import asyncio
import hashlib
import json
from datetime import timedelta
from random import Random

import structlog

from src.core.time_utils import from_utc_iso, now, require_aware, to_utc_iso

log = structlog.get_logger("blogai.life_runtime")


class LifeRuntime:
    def __init__(
        self,
        providers,
        generator,
        publisher,
        destinations,
        *,
        clock=now,
        weather_enabled=False,
    ):
        self.providers, self.generator, self.publisher = providers, generator, publisher
        self.destinations, self.clock = destinations, clock
        self.engine, self.database = providers.life, providers.life.database
        self._last_minute, self._generation = None, None
        self.weather_enabled, self._weather_job, self._weather_due = (
            weather_enabled,
            None,
            None,
        )

    async def _observe_weather(self, at):
        try:
            weather = await self.providers.weather.fetch(at, rng=Random(str(at.date())))
            await asyncio.to_thread(self.engine.observe_weather, weather, self.clock())
        except Exception:
            log.exception("life_weather_deferred")

    def recover(self):
        def save(c):
            for row in c.execute(
                "SELECT * FROM life_events WHERE publication_status='generating'"
            ).fetchall():
                post = c.execute(
                    "SELECT p.id FROM runs r JOIN posts p ON "
                    "p.id=json_extract(r.params_json,'$.post_id') "
                    "WHERE r.trace_id=? AND p.state IN ('draft','queued','published') "
                    "ORDER BY r.at DESC LIMIT 1",
                    (row["id"],),
                ).fetchone()
                c.execute(
                    "UPDATE life_events SET publication_status=?,post_id=? WHERE id=?",
                    (
                        "draft" if post else "pending",
                        post[0] if post else None,
                        row["id"],
                    ),
                )

        self.database.run_transaction(save)

    def _advance(self, at):
        activity = self.providers.itinerary.current(at)
        if not activity.can_publish:
            self.engine.income(at)
            return
        with self.database.connection(readonly=True) as c:
            completed = c.execute(
                "SELECT a.* FROM life_activities a JOIN life_tasks t ON t.id=a.task_id "
                "WHERE a.state='active' AND a.ends_at<=? AND t.status='pending' ORDER "
                "BY a.ends_at",
                (at,),
            ).fetchall()
        from src.core.itinerary import Activity

        for row in completed:
            finished = Activity.from_row(row)
            self.engine.income(finished.ends_at)
            self.engine.execute(
                finished.task_id, at=finished.ends_at, activity=finished
            )
        self.engine.income(at)
        sleep, reason, _ = self.providers.sleep.current(at)
        if timedelta(0) <= at - sleep.wake < timedelta(hours=1):

            def wake(c):
                identity = "wake:" + str(sleep.wake.date())
                if (
                    c.execute(
                        "SELECT 1 FROM life_events WHERE id=?", (identity,)
                    ).fetchone()
                    is None
                ):
                    self.engine._event(
                        c,
                        self.engine._state(c),
                        identity,
                        at,
                        "daily",
                        self.engine.rules["facts"]["wake"],
                        activity=activity,
                        changes={"wake_reason": reason},
                    )

            self.database.run_transaction(wake)
        self.engine.activity(activity, at)
        self.engine.notice(at)
        self.engine.seed_stories(at, activity)
        for task in self.engine.tasks():
            if self.engine.ready(
                task, at, activity
            ) and self.providers.itinerary.reserve_task(at, task):
                break
        needs = self.engine.needs(at)
        if needs["ill"]:
            self.providers.sleep.disturb_future_night(
                at,
                minutes=self.providers.life_config["health"]["restless_wake_minutes"],
                cause_id=self.engine.state()["ill_until"],
            )
        fingerprint = hashlib.sha256(
            json.dumps(needs, sort_keys=True).encode()
        ).hexdigest()[:16]
        self.providers.itinerary.adapt(
            at, needs=needs, cause_id=f"needs:{at.date()}:{fingerprint}"
        )
        self._retrospective_event(at, activity)

    def _retrospective_event(self, at, activity):
        if activity.kind != "rest" or at.hour < 22:
            return
        cfg = self.providers.life_config["publishing"]
        if Random(f"summary:{at.date()}").random() >= cfg["evening_summary_chance"]:
            return

        def save(c):
            identity = "evening:" + str(at.date())
            if not c.execute(
                "SELECT 1 FROM life_events WHERE id=?", (identity,)
            ).fetchone():
                state = self.engine._state(c)
                self.engine._event(
                    c, state, identity, at, "daily", "", activity=activity
                )

        self.database.run_transaction(save)

    def retrospective(self, at):
        start = at.replace(hour=0, minute=0, second=0, microsecond=0)
        with self.database.connection(readonly=True) as c:
            rows = c.execute(
                "SELECT v.at,v.payload,e.payload AS mood FROM life_events v "
                "LEFT JOIN life_effects e ON e.event_id=v.id AND e.kind='mood' AND "
                "e.applied_at IS NOT NULL "
                "WHERE v.at>=? AND v.at<=? AND v.kind!='daily' ORDER BY v.at",
                (start, at),
            ).fetchall()
        result = []
        for row in rows:
            value = {"at": row["at"], "facts": json.loads(row["payload"])["facts"]}
            if row["mood"]:
                mood = json.loads(row["mood"])
                value.update(
                    mood_before=mood["before_label"], mood_after=mood["after_label"]
                )
            result.append(value)
        return result[-16:]

    def _next(self, at):
        with self.database.connection(readonly=True) as c:
            cfg = self.providers.life_config["publishing"]
            cutoff = at - timedelta(minutes=cfg["burst_rest_minutes"])
            if (
                c.execute(
                    "SELECT count(*) FROM posts WHERE published_at>?", (cutoff,)
                ).fetchone()[0]
                >= cfg["max_burst"]
            ):
                return None
            if cfg["daily_target"] is not None:
                start = at.replace(hour=0, minute=0, second=0, microsecond=0)
                if (
                    c.execute(
                        "SELECT count(*) FROM posts WHERE published_at>=?", (start,)
                    ).fetchone()[0]
                    >= cfg["daily_target"][1]
                ):
                    return None
            cadence = c.execute(
                "SELECT value FROM life_state WHERE key='life.next_post'"
            ).fetchone()
            if cadence and at < from_utc_iso(json.loads(cadence[0])):
                return None
            row = c.execute(
                "SELECT * FROM life_events WHERE publication_status IN "
                "('pending','draft') "
                "AND at<=? AND (retry_at IS NULL OR retry_at<=?) AND attempts<3 "
                "ORDER BY (kind='daily') DESC,(task_id IS NOT NULL) DESC,at DESC "
                "LIMIT 1",
                (at, at),
            ).fetchone()
        return dict(row) if row else None

    async def tick(self, at=None):
        at = require_aware(at or self.clock())
        if self.weather_enabled and (
            self._weather_due is None or at >= self._weather_due
        ):
            if self._weather_job is None or self._weather_job.done():
                self._weather_due = at + timedelta(minutes=30)
                self._weather_job = asyncio.create_task(self._observe_weather(at))
        blocks = await self.providers.context(at)
        minute = at.replace(second=0, microsecond=0)
        if minute != self._last_minute:
            await asyncio.to_thread(self._advance, at)
            self._last_minute = minute
        if blocks["day"].blackout.blocked:
            return "sleep"
        await self.providers.mood.apply_life_effect(at)
        if self._generation is not None and not self._generation.done():
            return "generating"
        event = await asyncio.to_thread(self._next, at)
        if event is None:
            return "cadence_or_no_event"
        self._generation = asyncio.create_task(self._generate(event))
        return "queued"

    async def _generate(self, event):
        with structlog.contextvars.bound_contextvars(trace_id=event["id"]):
            try:
                blocks = await self.providers.context(self.clock())
                if blocks["day"].blackout.blocked:
                    return
                if event["post_id"] is None:
                    await asyncio.to_thread(
                        self.database.run_transaction,
                        lambda c: c.execute(
                            "UPDATE life_events SET "
                            "publication_status='generating',attempts=attempts+1 "
                            "WHERE id=?",
                            (event["id"],),
                        ),
                    )
                    result = await self.generator.recorded(
                        event,
                        **blocks,
                        retrospective=self.retrospective(blocks["day"].at)
                        if event["id"].startswith("evening:")
                        else None,
                    )
                    status = "draft" if result.status == "draft" else "killed"
                    await asyncio.to_thread(
                        self.database.run_transaction,
                        lambda c: c.execute(
                            "UPDATE life_events SET post_id=?,publication_status=? "
                            "WHERE id=?",
                            (
                                result.id
                                if result.status in {"draft", "killed"}
                                else None,
                                status,
                                event["id"],
                            ),
                        ),
                    )
                    if status != "draft":
                        return
                    event["post_id"] = result.id
                fresh = (await self.providers.context(self.clock()))["day"]
                if (
                    fresh.activity_id != blocks["day"].activity_id
                    or fresh.blackout.blocked
                ):
                    await asyncio.to_thread(self.expire, event["post_id"], self.clock())
                    return
                await asyncio.to_thread(
                    self.publisher.enqueue_post,
                    event["post_id"],
                    self.destinations,
                    trace_id=event["id"],
                    at=self.clock(),
                )
                gap = self.providers.life_config["publishing"][
                    "busy_gap_minutes" if fresh.busy else "rest_gap_minutes"
                ]
                due = self.clock() + timedelta(
                    minutes=Random(event["id"]).uniform(*gap)
                )

                def save(c):
                    c.execute(
                        "UPDATE life_events SET publication_status='queued' WHERE id=?",
                        (event["id"],),
                    )
                    c.execute(
                        "INSERT INTO life_state VALUES ('life.next_post',?,?) ON "
                        "CONFLICT(key) DO UPDATE SET "
                        "value=excluded.value,updated_at=excluded.updated_at",
                        (json.dumps(to_utc_iso(due)), self.clock()),
                    )

                await asyncio.to_thread(self.database.run_transaction, save)
            except Exception as error:
                error_type = type(error).__name__
                log.exception(
                    "life_generation_deferred", error_type=type(error).__name__
                )
                await asyncio.to_thread(
                    self.database.run_transaction,
                    lambda c: c.execute(
                        "UPDATE life_events SET "
                        "publication_status=CASE WHEN attempts>=3 THEN 'killed' "
                        "ELSE 'pending' END,retry_at=?,error=? WHERE id=? "
                        "AND publication_status='generating'",
                        (
                            self.clock() + timedelta(minutes=15),
                            error_type,
                            event["id"],
                        ),
                    ),
                )

    def expire(self, post_id, at):
        def save(c):
            c.execute(
                "UPDATE life_events SET "
                "publication_status='expired',error='activity_changed' WHERE post_id=? "
                "AND publication_status!='published'",
                (post_id,),
            )
            c.execute(
                "UPDATE posts SET state='killed' WHERE id=? AND published_at IS NULL",
                (post_id,),
            )

        self.database.run_transaction(save)

    async def close(self):
        if self._generation is not None:
            await self._generation
        if self._weather_job is not None:
            await self._weather_job
