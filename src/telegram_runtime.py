"""Run three Telegram identities and background workers in one asyncio process."""

import asyncio
import logging

import structlog
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
from src.core.telegram import TelegramLayout, TelegramTransport, load_bot_tokens
from src.core.time_utils import now, to_utc_iso
from src.defects import SQLiteLineageStore
from src.extract import Extractor
from src.library import LibraryInbox
from src.publish import OutboxWorker, Publisher


async def run_telegram(
    args, *, chat_factory=None, learning_factory=None, assemble=None
):
    # TODO(TELEGRAM-DEPLOYMENT): supply real layout IDs and verify test-group delivery.
    layout = TelegramLayout.from_file(args.layout)
    tokens = load_bot_tokens(layout, args.env_file)
    configure_logging(args.log_file, secret_values=tuple(tokens.values()))
    structlog.get_logger("blogai.telegram").info(
        "telegram_credentials_loaded", roles=sorted(tokens)
    )
    bots = {role: Bot(token) for role, token in tokens.items()}
    database = Database(args.database)
    await asyncio.to_thread(database.initialize)
    store = SQLiteSettingsStore(database)
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
        async with LocalLLM(
            args.generation_url,
            args.embedding_url,
            database=database,
            settings=registry,
        ) as llm:
            extractor = Extractor(
                database,
                llm,
                ContextBuilder(args.prompt_dir, settings=registry),
                grammar_dir=args.grammar_dir,
            )
            service = CommandService(
                database,
                layout,
                registry,
                LibraryInbox(args.library),
                log_path=args.log_file,
                extractor=extractor,
                chat_gateway=(
                    await chat_factory(database, llm, publisher, layout)
                    if chat_factory
                    else None
                ),
                lineage=SQLiteLineageStore(),
                llm=llm,
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
            learning = (
                await assemble(args, service, jobs)
                if assemble
                else await learning_factory(database, llm, publisher, layout, jobs)
                if learning_factory
                else None
            )

            async def expire_chat():
                while not stop.is_set():
                    if service.chat_gateway is not None:
                        instant = now()
                        jobs.submit(
                            "chat-expiry:" + to_utc_iso(instant),
                            "chat-expiry",
                            lambda at=instant: service.chat_gateway.service.expire(
                                at=at
                            ),
                        )
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=60)
                    except TimeoutError:
                        pass

            try:
                await jobs.start()
                if learning is not None:
                    if assemble is not None:

                        async def allowed(payload, at):
                            return not (await learning.providers.blackout(at)).blocked

                        worker.allowed = allowed
                    await learning.start()
                async with asyncio.TaskGroup() as tasks:
                    tasks.create_task(drain())
                    tasks.create_task(expire_chat())
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
                if learning is not None:
                    await learning.close()
                await jobs.close()
                await mirror.drain()
    finally:
        logger.removeHandler(mirror)
        for bot in bots.values():
            await bot.session.close()
