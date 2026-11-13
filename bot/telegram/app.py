"""Сборка Telegram-бота: подключение к Bot API, обработчики, меню команд."""

import logging
import traceback

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import BotCommand, ErrorEvent
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.alerts import AdminAlerts
from bot.config import Settings
from bot.telegram import handlers_common, handlers_homework, handlers_schedule, handlers_settings
from bot.telegram.middleware import DbMiddleware
from bot.telegram.notifier import TelegramNotifier

log = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="today", description="Расписание на сегодня"),
    BotCommand(command="tomorrow", description="Расписание на завтра"),
    BotCommand(command="week", description="Расписание на неделю"),
    BotCommand(command="next", description="Ближайшая пара"),
    BotCommand(command="homework", description="Домашние задания"),
    BotCommand(command="add", description="Добавить задание"),
    BotCommand(command="settings", description="Настройки"),
    BotCommand(command="help", description="Что умеет бот"),
]


def create_bot(settings: Settings, session: AiohttpSession | None = None) -> Bot:
    if session is None:
        session = AiohttpSession(
            api=TelegramAPIServer.from_base(settings.telegram_api_url),
            proxy=settings.telegram_proxy,
        )
    return Bot(
        token=settings.bot_token.get_secret_value(),
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def on_error(event: ErrorEvent, alerts: AdminAlerts) -> bool:
    """Любая ошибка в обработчике: студенту — короткое извинение, владельцу — сообщение,
    в журнал — только вид ошибки и место в коде (текст ошибки может содержать то, что ввёл студент)."""
    exc = event.exception
    where = "".join(traceback.format_tb(exc.__traceback__)[-3:])
    log.error("Ошибка при обработке обновления %s: %s\n%s", event.update.update_id, type(exc).__name__, where)
    upd = event.update
    try:
        if upd.callback_query:
            await upd.callback_query.answer("Что-то пошло не так 😕 Попробуйте ещё раз.", show_alert=True)
        elif upd.message:
            await upd.message.answer("Что-то пошло не так 😕 Попробуйте ещё раз. Выйти из ввода — /cancel.")
    except Exception:
        pass  # ответить не удалось (например, нет связи) — ничего страшного
    await alerts.report(f"handler:{type(exc).__name__}", f"Ошибка в боте: {type(exc).__name__}. Подробности — в журнале.")
    return True


def create_dispatcher(
    sessionmaker: async_sessionmaker,
    settings: Settings,
    notifier: TelegramNotifier,
    alerts: AdminAlerts | None = None,
) -> Dispatcher:
    # Состояние диалогов хранится в памяти: после перезапуска начатый ввод придётся повторить,
    # а все сохранённые данные и напоминания лежат в базе и не теряются.
    # SimpleEventIsolation: сообщения одного студента обрабатываются строго по очереди
    # (альбом из нескольких фото, двойное нажатие кнопки).
    dp = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())
    dp["admin_id"] = settings.admin_id
    dp["notifier"] = notifier
    dp["alerts"] = alerts or AdminAlerts(notifier)
    mw = DbMiddleware(sessionmaker, settings.default_timezone)
    dp.message.outer_middleware(mw)
    dp.callback_query.outer_middleware(mw)
    dp.errors.register(on_error)
    routers = [
        handlers_common.router,
        handlers_schedule.router,
        handlers_homework.router,
        handlers_settings.router,
        handlers_common.fallback_router,  # «не понял» — последним
    ]
    for r in routers:
        # Обработчики можно подключить только к одному диспетчеру; в тестах диспетчер создаётся заново.
        if r._parent_router is not None:
            r._parent_router.sub_routers.remove(r)
            r._parent_router = None
    dp.include_routers(*routers)
    return dp


async def set_commands(bot: Bot) -> None:
    await bot.set_my_commands(COMMANDS)
