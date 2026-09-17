"""Autonomous life ticks and independent, durable everyday publication work."""

import asyncio
import hashlib
import json
from datetime import timedelta
from random import Random

import structlog

from src.core.breaks import BreakPlanner
from src.core.detailed_world import DetailedWorld
from src.core.food_plan import prioritize_meal_route, revise_for_food
from src.core.nutrition import Nutrition
from src.core.time_utils import from_utc_iso, now, require_aware, to_utc_iso
from src.core.transitions import TransitionJournal
from src.core.world_journal import WorldJournal
from src.core.world_plan import save_plan

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
        log_destination=None,
        state_destination=None,
    ):
        self.providers, self.generator, self.publisher = providers, generator, publisher
        self.destinations, self.clock = destinations, clock
        self.engine, self.database = providers.life, providers.life.database
        self.transitions, self.breaks = (
            TransitionJournal(providers),
            BreakPlanner(providers),
        )
        self.details = DetailedWorld(self.engine, itinerary=providers.itinerary)
        self.journal = WorldJournal(
            self.database,
            log_destination=log_destination,
            state_destination=state_destination,
        )
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

    def _advance(self, at, *, mood=None, sleep_debt=0):
        self.details.health(at)
        self.breaks.ensure_wind_down(at)
        reason = None
        needs = self.engine.needs(at)
        if needs["ill"]:
            self.providers.itinerary.adapt(
                at,
                needs=needs,
                cause_id="illness-admission:" + self.engine.state()["ill_until"],
            )
            reason = "health"
        if mood is not None:
            reason = (
                self.breaks.stop_if_exhausted(at, mood=mood, sleep_debt=sleep_debt)
                or reason
            )
        activity = self.providers.itinerary.current(at)
        self.transitions.observe(at, reason=reason)
        if not activity.can_publish:
            self.engine.income(at)
            return
        self.details.household.expire(at)
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
        self.details.advance(activity, at, seed=False)
        nutrition = Nutrition(self.engine)
        nutrition.observe(activity, at)
        if prioritize_meal_route(self.engine, self.providers.itinerary, activity, at):
            activity = self.providers.itinerary.current(at)
        if revise_for_food(self.engine, self.providers.itinerary, activity, at):
            activity = self.providers.itinerary.current(at)
            self.transitions.observe(at, reason="meal")
        nutrition.seed(self.details, activity, at)
        self.details.complete_appointments(at)
        self.engine.notice(at)
        self.engine.seed_stories(at, activity)
        with self.database.connection(readonly=True) as c:
            occupied = self.details.occupied(c, at)
        for task in [] if occupied else self.engine.tasks():
            if self.engine.ready(
                task, at, activity
            ) and self.providers.itinerary.reserve_task(at, task):
                break
        self.details.seed(
            self.providers.itinerary.current(at),
            at,
            productivity=needs.get("productivity", 0.5),
        )
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
        if mood is not None:
            self.breaks.consider(at, mood=mood, sleep_debt=sleep_debt)
        activity = self.providers.itinerary.current(at)
        self.engine.activity(activity, at)
        self.transitions.observe(at, reason="health" if needs["ill"] else None)
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
        if mandatory := self.transitions.next(at):
            return mandatory
        # A present-tense activity observation loses relevance at its boundary.
        # Its history remains available to the retrospective, without a new post.
        self.database.run_transaction(
            lambda c: c.execute(
                "UPDATE life_events SET publication_status='expired' WHERE "
                "id LIKE 'activity:%' AND publication_status IN ('pending','draft') "
                "AND valid_until<=?",
                (at,),
            )
        )
        with self.database.connection(readonly=True) as c:
            cfg = self.providers.life_config["publishing"]
            cutoff = at - timedelta(minutes=cfg["burst_rest_minutes"])
            if cfg["max_burst"] is not None and (
                c.execute(
                    "SELECT count(*) FROM posts p WHERE published_at>? AND NOT EXISTS "
                    "(SELECT 1 FROM activity_transitions t WHERE t.post_id=p.id)",
                    (cutoff,),
                ).fetchone()[0]
                >= cfg["max_burst"]
            ):
                return None
            if cfg["daily_target"] is not None:
                start = at.replace(hour=0, minute=0, second=0, microsecond=0)
                if (
                    c.execute(
                        "SELECT count(*) FROM posts p WHERE published_at>=? AND NOT "
                        "EXISTS "
                        "(SELECT 1 FROM activity_transitions t WHERE t.post_id=p.id)",
                        (start,),
                    ).fetchone()[0]
                    >= cfg["daily_target"][1]
                ):
                    return None
            cadence = c.execute(
                "SELECT value FROM life_state WHERE key='life.next_post'"
            ).fetchone()
            if cadence and at < from_utc_iso(json.loads(cadence[0])):
                return None
            if c.execute(
                "SELECT 1 FROM life_events WHERE publication_status='queued' "
                "AND kind!='transition'"
            ).fetchone():
                return None
            row = c.execute(
                "SELECT * FROM life_events WHERE publication_status IN "
                "('pending','draft') AND kind!='transition' "
                "AND at<=? AND (retry_at IS NULL OR retry_at<=?) AND attempts<3 "
                "AND NOT EXISTS (SELECT 1 FROM life_events intent WHERE "
                "json_extract(intent.payload,'$.related_event_id')=life_events.id "
                "AND (intent.publication_status IN "
                "('pending','generating','draft','queued') OR EXISTS (SELECT 1 "
                "FROM activity_transitions notice WHERE notice.intent_id=intent.id "
                "AND notice.delivered_at IS NULL))) "
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
        changed_minute = minute != self._last_minute
        if changed_minute:
            await asyncio.to_thread(
                self._advance,
                at,
                mood=blocks["mood"],
                sleep_debt=blocks["day"].sleep_debt,
            )
            self._last_minute = minute
        if not blocks["day"].blackout.blocked:
            await self.providers.mood.apply_life_effect(at)
        if changed_minute:
            await asyncio.to_thread(self._observe_state, at)
            await asyncio.to_thread(self.journal.flush, at)
        if blocks["day"].blackout.blocked:
            return "sleep"
        if self._generation is not None and not self._generation.done():
            return "generating"
        event = await asyncio.to_thread(self._next, at)
        if event is None:
            return "cadence_or_no_event"
        self._generation = asyncio.create_task(self._generate(event))
        return "queued"

    def _observe_state(self, at):
        state = self.engine.state()
        activity = self.providers.itinerary.current(at)
        needs = self.engine.needs(at)
        mood = self.providers.mood._current(at)
        sleep, reason, debt = self.providers.sleep.current(at)
        with self.database.connection(readonly=True) as c:
            runs = [
                dict(row)
                for row in c.execute(
                    "SELECT id,scenario,node,due_at FROM world_runs "
                    "WHERE status='running' ORDER BY id"
                )
            ]
            appointments = [
                dict(row)
                for row in c.execute(
                    "SELECT person,starts_at,ends_at,location,status "
                    "FROM world_appointments WHERE status='reserved' ORDER BY starts_at"
                )
            ]
            latest = c.execute(
                "SELECT id,payload FROM life_events WHERE "
                "json_extract(payload,'$.facts')!='' "
                "ORDER BY at DESC,rowid DESC LIMIT 1"
            ).fetchone()
            tasks = [
                dict(row)
                for row in c.execute(
                    "SELECT id,kind,reason,priority,earliest_at,deadline,dependencies "
                    "FROM life_tasks WHERE status='pending' ORDER BY priority DESC,id"
                )
            ]
            calendars = [
                json.loads(row[0])
                for row in c.execute(
                    "SELECT payload FROM world_calendars WHERE day=? ORDER BY person",
                    (str(at.date()),),
                )
            ]
        omitted = {
            "last_income_at",
            "imported_arcs",
            "imported_npc",
            "last_activity",
            "last_activity_signature",
        }
        snapshot = {key: value for key, value in state.items() if key not in omitted}
        # Account cursors describe polling, not a changed balance or appointment.
        snapshot["npc_accounts"] = {
            key: {"balance": value["balance"]}
            for key, value in state["npc_accounts"].items()
        }
        snapshot.update(
            current={
                "location": activity.location,
                "activity": activity.label,
                "until": to_utc_iso(activity.ends_at),
                "subject": activity.subject,
                "health": needs.get("health_stage"),
                "productivity": needs.get("productivity"),
                "mood": self.providers.model.mood_block(mood),
                "ongoing": self.engine.public_state(at)["ongoing_activity"],
                "food": self.engine.food_view(at),
            },
            mood={axis: round(getattr(mood, axis), 2) for axis in ("P", "A", "D")},
            sleep={
                "bedtime": to_utc_iso(sleep.bedtime),
                "wake": to_utc_iso(sleep.wake),
                "debt_hours": round(debt, 4),
                "reason": reason,
            },
            active_scenarios=runs,
            appointments=appointments,
            tasks=tasks,
            npc_calendars=calendars,
            plan=save_plan(self.database, self.providers.itinerary.day(at), at),
            last_event={
                key: value
                for key, value in json.loads(latest["payload"]).items()
                if key in {"facts", "at", "outcome", "changes", "world"}
            }
            if latest
            else None,
        )
        self.journal.observe(snapshot, at, cause=latest[0] if latest else activity.id)

    async def _generate(self, event):
        with structlog.contextvars.bound_contextvars(trace_id=event["id"]):
            try:
                blocks = await self.providers.context(self.clock())
                if blocks["day"].blackout.blocked:
                    return
                mandatory = json.loads(event["payload"]).get("mandatory", False)
                if (
                    blocks["day"].location
                    != self.providers.itinerary.current(blocks["day"].at).location
                ):
                    return
                if mandatory and event["activity_id"] != blocks["day"].activity_id:
                    await asyncio.to_thread(
                        self.database.run_transaction,
                        lambda c: c.execute(
                            "UPDATE life_events SET publication_status='expired' "
                            "WHERE id=?",
                            (event["id"],),
                        ),
                    )
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
                        if mandatory:
                            await asyncio.to_thread(
                                self.database.run_transaction,
                                lambda c: c.execute(
                                    "UPDATE life_events SET retry_at=? WHERE id=?",
                                    (
                                        self.clock()
                                        + timedelta(
                                            minutes=self.transitions.config[
                                                "retry_minutes"
                                            ]
                                        ),
                                        event["id"],
                                    ),
                                ),
                            )
                        return
                    event["post_id"] = result.id
                if mandatory:
                    await asyncio.to_thread(
                        self.transitions.bind, event["id"], event["post_id"]
                    )
                fresh = (await self.providers.context(self.clock()))["day"]
                if (
                    fresh.activity_id != blocks["day"].activity_id
                    or fresh.world_action_id != blocks["day"].world_action_id
                    or fresh.blackout.blocked
                ):
                    await asyncio.to_thread(self.expire, event["post_id"], self.clock())
                    return
                gap = self.providers.life_config["publishing"][
                    "busy_gap_minutes" if fresh.busy else "rest_gap_minutes"
                ]
                pause = round(Random(event["id"]).uniform(*gap), 4)

                def save(c):
                    if not mandatory or json.loads(event["payload"]).get(
                        "related_event_id"
                    ):
                        c.execute(
                            "UPDATE life_events SET payload="
                            "json_set(payload,'$.post_gap_minutes',?) WHERE id=?",
                            (pause, event["id"]),
                        )

                await asyncio.to_thread(self.database.run_transaction, save)
                await asyncio.to_thread(
                    self.publisher.enqueue_post,
                    event["post_id"],
                    self.destinations,
                    trace_id=event["id"],
                    at=self.clock(),
                )
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
