"""Run three Telegram identities and background workers in one asyncio process."""

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher

from src.bot import BotIngress
from src.commands import CommandService
from src.core.context import ContextBuilder
from src.core.db import Database
from src.core.llm_local import LocalLLM
from src.core.logging import configure_logging
from src.core.ops_log import OpsMirror
from src.core.settings import SettingsRegistry, SQLiteSettingsStore
from src.core.tasks import JobQueue
from src.core.telegram import TelegramLayout, TelegramTransport
from src.defects import SQLiteLineageStore
from src.extract import Extractor
from src.library import LibraryInbox
from src.publish import OutboxWorker, Publisher


async def run_telegram(args):
    layout = TelegramLayout.from_file(args.layout)
    tokens = {
        role: os.environ[f"{role.upper()}_BOT_TOKEN"]
        for role in ("mika", "curator", "ops")
    }
    if len(set(tokens.values())) != 3:
        raise ValueError("Three distinct Telegram bot tokens are required")
    configure_logging(args.log_file, secret_values=tuple(tokens.values()))
    bots = {role: Bot(token) for role, token in tokens.items()}
    database = Database(args.database)
    await asyncio.to_thread(database.initialize)
    store = SQLiteSettingsStore(database) if args.interface_storage else None
    registry = SettingsRegistry.from_file(args.settings, store=store)
    jobs = JobQueue()
    publisher = Publisher(database)
    mirror = OpsMirror(publisher, layout.destination("machine"), registry)
    logger = logging.getLogger("blogai")
    logger.addHandler(mirror)
    worker = OutboxWorker(database, TelegramTransport(bots))
    stop = asyncio.Event()

    async def drain():
        while not stop.is_set():
            await mirror.drain()
            for _ in range(64):
                if await worker.run_once() == "idle":
                    break
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                pass

    try:
        async with LocalLLM(args.generation_url, args.embedding_url) as llm:
            extractor = Extractor(
                database,
                llm,
                ContextBuilder(args.prompt_dir),
                grammar_dir=args.grammar_dir,
            )
            service = CommandService(
                database,
                layout,
                registry,
                LibraryInbox(args.library),
                log_path=args.log_file,
                extractor=extractor,
                lineage=SQLiteLineageStore() if args.interface_storage else None,
                health_urls={
                    "llama": args.generation_url,
                    "embeddings": args.embedding_url,
                },
            )
            ingress = BotIngress(
                layout, {bot.id: role for role, bot in bots.items()}, jobs, service
            )
            dispatcher = Dispatcher()
            dispatcher.include_router(ingress.router)
            await jobs.start()
            try:
                async with asyncio.TaskGroup() as tasks:
                    tasks.create_task(drain())
                    try:
                        await tasks.create_task(
                            dispatcher.start_polling(
                                *bots.values(),
                                allowed_updates=dispatcher.resolve_used_update_types(),
                                close_bot_session=False,
                            )
                        )
                    finally:
                        stop.set()
            finally:
                await jobs.close()
                await mirror.drain()
    finally:
        logger.removeHandler(mirror)
        for bot in bots.values():
            await bot.session.close()
