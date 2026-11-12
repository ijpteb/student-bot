"""Сборка Telegram-бота: подключение к Bot API, обработчики, меню команд."""

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import Settings
from bot.telegram import handlers_common, handlers_homework, handlers_schedule, handlers_settings
from bot.telegram.middleware import DbMiddleware
from bot.telegram.notifier import TelegramNotifier

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
            api=TelegramAPIServer.from_base(settings.telegram_api_url.rstrip("/")),
            proxy=settings.telegram_proxy,
        )
    return Bot(
        token=settings.bot_token.get_secret_value(),
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(sessionmaker: async_sessionmaker, settings: Settings, notifier: TelegramNotifier) -> Dispatcher:
    # Состояние диалогов хранится в памяти: после перезапуска начатый ввод придётся повторить,
    # а все сохранённые данные и напоминания лежат в базе и не теряются.
    dp = Dispatcher(storage=MemoryStorage())
    dp["admin_id"] = settings.admin_id
    dp["notifier"] = notifier
    mw = DbMiddleware(sessionmaker, settings.default_timezone)
    dp.message.outer_middleware(mw)
    dp.callback_query.outer_middleware(mw)
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
