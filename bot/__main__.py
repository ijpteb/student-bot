"""Запуск бота: python -m bot"""

import asyncio
import logging
import os
import sys

from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from pydantic import ValidationError

from bot import scheduler
from bot.alerts import AdminAlerts
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
    alerts = AdminAlerts(notifier)
    dp = create_dispatcher(sessionmaker, settings, notifier, alerts)
    sched_task = None
    try:
        me = await bot.get_me()
        log.info("Бот @%s запущен, сервер Bot API: %s", me.username, settings.telegram_api_url)
        await set_commands(bot)
        if settings.admin_id:
            try:
                await bot.send_message(settings.admin_id, "✅ Бот запущен.")
            except Exception:
                log.warning("Не удалось написать владельцу: сначала отправьте боту /start со своего аккаунта")
        sched_task = asyncio.create_task(scheduler.run(sessionmaker, notifier, alerts))
        await dp.start_polling(bot, handle_signals=True)
    finally:
        if sched_task:
            sched_task.cancel()
        await bot.session.close()
        await engine.dispose()


def run() -> None:
    # Журнал настраиваем до чтения настроек, а любые ошибки запуска пишем через него:
    # так токен бота вырезается даже из текста ошибки.
    setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
    try:
        asyncio.run(main())
    except ValidationError as e:
        fields = ", ".join(sorted({str(err["loc"][0]).upper() for err in e.errors(include_input=False)}))
        reasons = "; ".join(err["msg"] for err in e.errors(include_input=False))
        log.error("Ошибка в файле .env — проверьте строки: %s (%s)", fields, reasons)
        sys.exit(1)
    except TelegramUnauthorizedError:
        log.error("Бот не смог запуститься: Telegram не принял токен (Unauthorized) — проверьте строку BOT_TOKEN в файле .env")
        sys.exit(1)
    except TelegramNetworkError as e:
        log.error(
            "Бот не смог запуститься: нет связи с сервером Telegram (%s). "
            "Проверьте интернет или строки TELEGRAM_API_URL / TELEGRAM_PROXY в файле .env",
            type(e).__name__,
        )
        sys.exit(1)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        log.exception("Бот не смог запуститься")
        sys.exit(1)


if __name__ == "__main__":
    run()
