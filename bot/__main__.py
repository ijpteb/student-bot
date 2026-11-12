"""Запуск бота: python -m bot"""

import asyncio
import logging

from bot import scheduler
from bot.config import load_settings
from bot.db import make_engine, make_sessionmaker
from bot.logging_setup import setup_logging
from bot.telegram.app import create_bot, create_dispatcher, set_commands
from bot.telegram.notifier import TelegramNotifier

log = logging.getLogger("bot")


async def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)
    engine = make_engine(settings.database_url, pool_pre_ping=True)
    sessionmaker = make_sessionmaker(engine)
    bot = create_bot(settings)
    notifier = TelegramNotifier(bot, settings.admin_id)
    dp = create_dispatcher(sessionmaker, settings, notifier)

    me = await bot.get_me()
    log.info("Бот @%s запущен, сервер Bot API: %s", me.username, settings.telegram_api_url)
    await set_commands(bot)
    if settings.admin_id:
        try:
            await bot.send_message(settings.admin_id, "✅ Бот запущен.")
        except Exception:
            log.warning("Не удалось написать владельцу: сначала отправьте боту /start со своего аккаунта")

    sched_task = asyncio.create_task(scheduler.run(sessionmaker, notifier))
    try:
        await dp.start_polling(bot, handle_signals=True)
    finally:
        sched_task.cancel()
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
