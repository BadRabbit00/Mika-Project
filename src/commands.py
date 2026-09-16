"""Owner operations executed by the background queue, never by model handlers."""

import asyncio
import io
import json
import shutil
import time
from datetime import timedelta
from pathlib import Path

import httpx
import structlog

from src.bot import post_buttons, settings_buttons
from src.core.settings import MissingSettingsStorage
from src.core.time_utils import now
from src.defects import CATEGORIES, Defects
from src.publish import Destination, Publisher

log = structlog.get_logger("blogai.commands")


class CommandService:
    def __init__(
        self,
        database,
        layout,
        registry,
        library,
        *,
        log_path: Path,
        regenerator=None,
        extractor=None,
        health_urls=None,
        lineage=None,
        chat_gateway=None,
    ):
        self.database, self.layout, self.registry, self.library = (
            database,
            layout,
            registry,
            library,
        )
        self.publisher, self.defects = (
            Publisher(database),
            Defects(database, lineage=lineage),
        )
        self.log_path, self.regenerator, self.extractor = (
            Path(log_path),
            regenerator,
            extractor,
        )
        self.health_urls = health_urls or {}
        self.chat_gateway = chat_gateway

    async def chat(self, message, bot, trace_id, *, channel):
        if self.chat_gateway is not None:
            await self.chat_gateway.handle(message, channel=channel, trace_id=trace_id)
        elif message.text.startswith("/chat"):
            await self.reply(
                message,
                {"error": "TODO(RUNTIME-CONTEXT): chat has no context provider"},
                trace_id,
            )

    def _reply_destination(self, message):
        if message.chat.type == "private":
            return Destination("owner", "ops", self.layout.owner_id)
        return self.layout.destination("control")

    async def reply(self, message, value, trace_id, *, suffix="reply", markup=None):
        text = (
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, indent=2)
        )
        destination = self._reply_destination(message)
        if len(text) > 4096:
            await asyncio.to_thread(
                self.publisher.enqueue_operation,
                f"{trace_id}:{suffix}",
                destination,
                trace_id=trace_id,
                method="document",
                content=text,
                filename=f"{trace_id}.json",
                caption=f"Operation result: {trace_id}",
                reply_markup=markup,
            )
        else:
            await asyncio.to_thread(
                self.publisher.enqueue_operation,
                f"{trace_id}:{suffix}",
                destination,
                trace_id=trace_id,
                method="message",
                text=text,
                reply_markup=markup,
            )

    async def settings_view(self, message, selector, trace_id):
        log.info("owner_settings_view", selector=selector, trace_id=trace_id)
        try:
            markup = None
            if not selector:
                result = "Settings groups"
                markup = settings_buttons(
                    (key, group["title"]) for key, group in self.registry.groups.items()
                )
            elif selector in self.registry.groups:
                result = await asyncio.to_thread(self.registry.describe, selector)
                markup = settings_buttons(
                    (row["key"], f"{row['title']}: {row['current']}") for row in result
                )
            elif selector in self.registry.entries:
                result = await asyncio.to_thread(self.registry.describe, selector)
            else:
                raise ValueError("Unknown settings group or key")
        except ValueError as error:
            result, markup = {"error": str(error)}, None
        await self.reply(message, result, trace_id, markup=markup)

    def _state(self):
        with self.database.connection() as connection:
            return {
                "learning_state": "TODO(LEARNING-STATE)",
                "topics": [
                    dict(row)
                    for row in connection.execute("SELECT * FROM topics ORDER BY name")
                ],
                "counts": {
                    table: connection.execute(
                        f"SELECT count(*) FROM {table}"
                    ).fetchone()[0]
                    for table in ("sources", "nodes", "edges", "exams", "posts")
                },
                "outbox_pending": connection.execute(
                    "SELECT count(*) FROM outbox WHERE sent_at IS NULL "
                    "AND next_try_at IS NOT NULL"
                ).fetchone()[0],
                "outbox_uncertain": connection.execute(
                    "SELECT count(*) FROM outbox WHERE sent_at IS NULL "
                    "AND next_try_at IS NULL AND attempts>0"
                ).fetchone()[0],
            }

    def _graph(self, argument):
        with self.database.connection() as connection:
            if not argument or argument == "stats":
                return {
                    table: connection.execute(
                        f"SELECT count(*) FROM {table}"
                    ).fetchone()[0]
                    for table in ("nodes", "edges", "claims", "sources")
                }
            if argument.startswith("search "):
                query = '"' + argument[7:].replace('"', '""') + '"'
                return [
                    dict(row)
                    for row in connection.execute(
                        "SELECT n.* FROM nodes_fts f JOIN nodes n ON n.id=f.id "
                        "WHERE nodes_fts MATCH ? ORDER BY rank LIMIT 20",
                        (query,),
                    )
                ]
            node = connection.execute(
                "SELECT * FROM nodes WHERE id=? OR name=?", (argument, argument)
            ).fetchone()
            if node is None:
                return {"nodes": []}
            edges = [
                dict(row)
                for row in connection.execute(
                    "SELECT e.*, s.title, s.url FROM edges e "
                    "LEFT JOIN sources s ON s.id=e.source_id "
                    "WHERE e.src=? OR e.dst=? ORDER BY e.id",
                    (node["id"], node["id"]),
                )
            ]
            return {"node": dict(node), "edges": edges}

    def trace(self, trace_id):
        records = []
        if self.log_path.exists():
            with self.log_path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if record.get("trace_id") == trace_id:
                        records.append(record)
        return records

    async def health(self, bot):
        state = await asyncio.to_thread(self._state)
        state["curator_cli"] = (
            await asyncio.to_thread(shutil.which, "claude") is not None
        )
        state["telegram"] = "unknown"
        try:
            await bot.get_me()
            state["telegram"] = "ok"
        except Exception:
            state["telegram"] = "unavailable"
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            for name, url in self.health_urls.items():
                started = time.monotonic()
                try:
                    response = await client.get(url.rstrip("/") + "/health")
                    response.raise_for_status()
                    state[name] = {
                        "status": "ok",
                        "duration_ms": round((time.monotonic() - started) * 1000),
                    }
                except httpx.HTTPError:
                    state[name] = {"status": "unavailable"}
        return state

    async def command(self, message, bot, trace_id):
        command, _, argument = message.text.partition(" ")
        command, argument = command.split("@", 1)[0].lower(), argument.strip()
        log.info("owner_command", command=command, trace_id=trace_id)
        try:
            match command:
                case "/state":
                    result = await asyncio.to_thread(self._state)
                case "/graph":
                    result = await asyncio.to_thread(self._graph, argument)
                case "/set" | "/get" | "/help" | "/config":
                    key, _, value = argument.partition(" ")
                    if command == "/set" and value:
                        result = await asyncio.to_thread(
                            self.registry.set, key, value, trace_id=trace_id
                        )
                    elif command == "/set":
                        await self.settings_view(message, key, trace_id)
                        return
                    else:
                        result = await asyncio.to_thread(
                            self.registry.describe, key or None
                        )
                case "/reset":
                    result = await asyncio.to_thread(
                        self.registry.reset, argument, trace_id=trace_id
                    )
                case "/diff":
                    result = [
                        row
                        for row in self.registry.describe()
                        if row["current"] != row.get("default")
                    ]
                case "/health":
                    result = await self.health(bot)
                case "/defects":
                    if argument not in {"", "week"}:
                        raise ValueError("Use /defects or /defects week")
                    result = await asyncio.to_thread(
                        self.defects.list,
                        since=now() - timedelta(days=7) if argument else None,
                    )
                case "/trace":
                    result = (
                        await asyncio.to_thread(self.trace, argument)
                        if argument
                        else {"usage": "/trace <trace_id>"}
                    )
                case "/preview":
                    post = await asyncio.to_thread(self._post, argument)
                    await self.reply(
                        message, post["text"], trace_id, markup=post_buttons(argument)
                    )
                    return
                case "/publish" | "/regen":
                    if not argument:
                        result = {"usage": f"{command} <post_id>"}
                    else:
                        await self.action(command[1:], argument, message, trace_id)
                        return
                case "/invalid":
                    parts = argument.split(" ", 2)
                    if len(parts) != 3:
                        result = {
                            "usage": "/invalid <post_id> <category> <reason>",
                            "categories": sorted(CATEGORIES),
                        }
                    else:
                        await self.defect(
                            parts[0], parts[1] + " " + parts[2], message, trace_id
                        )
                        return
                case _:
                    result = {
                        "commands": [
                            "/state",
                            "/graph",
                            "/set",
                            "/health",
                            "/defects",
                            "/trace",
                            "/preview",
                            "/publish",
                            "/regen",
                            "/invalid",
                        ]
                    }
        except (ValueError, KeyError, MissingSettingsStorage) as error:
            log.warning("owner_command_rejected", command=command, error=str(error))
            result = {"error": str(error)}
        await self.reply(message, result, trace_id)

    def _post(self, post_id):
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM posts WHERE id=?", (post_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Unknown post")
            return dict(row)

    def _post_trace(self, post_id, fallback):
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT json_extract(payload, '$.trace_id') FROM outbox "
                "WHERE json_extract(payload, '$.post_id')=? ORDER BY id LIMIT 1",
                (post_id,),
            ).fetchone()
            if row:
                return row[0]
            row = connection.execute(
                "SELECT COALESCE(json_extract(params_json, '$.trace_id'), trace_id) "
                "FROM runs WHERE json_extract(params_json, '$.post_id')=? "
                "ORDER BY at LIMIT 1",
                (post_id,),
            ).fetchone()
            return row[0] if row else fallback

    async def action(self, action, post_id, message, trace_id):
        try:
            post = await asyncio.to_thread(self._post, post_id)
            if action == "defect":
                await self.reply(
                    message,
                    "Send one line within one minute: <category> <reason>. Categories: "
                    + ", ".join(sorted(CATEGORIES)),
                    trace_id,
                )
                return
            if action == "publish":
                origin = await asyncio.to_thread(self._post_trace, post_id, trace_id)
                ids = await asyncio.to_thread(
                    self.publisher.enqueue_post,
                    post_id,
                    self.layout.publication_destinations(),
                    trace_id=origin,
                    markup=post_buttons(post_id, published=True),
                )
                await self.reply(message, {"queued": ids, "trace_id": origin}, trace_id)
            elif action == "regen":
                if self.regenerator is None:
                    raise ValueError(
                        "TODO(POST-REGENERATION): current world and mood "
                        "context provider is required"
                    )
                if post["state"] == "published":
                    raise ValueError(
                        "A published post requires the correction workflow"
                    )
                result = await self.regenerator(post_id, trace_id=trace_id)
                await self.reply(message, result, trace_id)
        except ValueError as error:
            await self.reply(message, {"error": str(error)}, trace_id)

    async def defect(self, post_id, text, message, trace_id):
        category, _, reason = text.partition(" ")
        try:
            record = await asyncio.to_thread(
                self.defects.invalidate,
                post_id,
                category=category,
                reason=reason,
                trace_id=trace_id,
            )
            post = await asyncio.to_thread(self._post, post_id)
            destination = self.layout.destination("control")
            card = await asyncio.to_thread(
                self.publisher.enqueue_operation,
                f"defect:{post_id}",
                destination,
                trace_id=record["trace_id"],
                method="message",
                text=(
                    f"Defect: {post_id}\nCategory: {category}\nReason: {reason}"
                    f"\nTrace: {record['trace_id']}\n\n{post['text']}"
                ),
            )
            await asyncio.to_thread(
                self.publisher.enqueue_operation,
                f"defect-pin:{post_id}",
                destination,
                trace_id=record["trace_id"],
                method="pin",
                depends_on=card,
                defect_post_id=post_id,
            )
            await asyncio.to_thread(self._mark_public, post_id, record)
            await self.reply(
                message,
                {
                    "invalidated": post_id,
                    "lineage": "applied"
                    if self.defects.lineage
                    else "TODO(INVALIDATION-LINEAGE)",
                },
                trace_id,
            )
        except ValueError as error:
            await self.reply(message, {"error": str(error)}, trace_id)

    def _mark_public(self, post_id, record):
        with self.database.connection() as connection:
            rows = list(
                connection.execute(
                    "SELECT * FROM outbox WHERE sent_at IS NOT NULL "
                    "AND json_extract(payload, '$.post_id')=?",
                    (post_id,),
                )
            )
        for row in rows:
            payload = json.loads(row["payload"])
            self.publisher.enqueue_operation(
                f"defect-mark:{post_id}",
                Destination(**payload["destination"]),
                trace_id=record["trace_id"],
                method="edit",
                message_id=row["tg_message_id"],
                text=payload["text"]
                + f"\n\n[Invalidated: {record['category']}. {record['reason']}]",
                parse_mode=None,
            )

    async def document(self, message, bot, trace_id):
        document = message.document
        try:
            if not document.file_name or not document.file_name.lower().endswith(".md"):
                raise ValueError("Only Markdown articles are accepted")
            buffer = io.BytesIO()
            await bot.download(document, destination=buffer)
            source = await asyncio.to_thread(
                self.library.accept,
                document.file_name,
                buffer.getvalue(),
                trace_id=trace_id,
            )
            result = {
                "accepted": source.id,
                "topic": source.topic,
                "trust_prior": source.trust_prior,
            }
            if self.extractor is not None:
                await self.extractor.extract(source)
                result["extracted"] = True
            await self.reply(message, result, trace_id)
        except (ValueError, UnicodeError) as error:
            await self.reply(message, {"rejected": str(error)}, trace_id)
