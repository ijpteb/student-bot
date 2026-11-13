"""Сквозная проверка диалогов: настоящие обработчики aiogram, поддельный сервер Telegram."""

from datetime import datetime, timedelta, timezone

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, GetMe, SendMessage, TelegramMethod
from aiogram.types import Chat, Message, Update, User as TgUser
from sqlalchemy import select

from bot import scheduler
from bot.config import Settings
from bot.core.timeutil import local_now
from bot.models import Homework, Lesson, Reminder, Subject, UniversityRequest, User
from bot.telegram import kb
from bot.telegram.app import create_dispatcher
from bot.telegram.notifier import TelegramNotifier

TOKEN = "123456:" + "A" * 35


class FakeSession(BaseSession):
    """Вместо отправки в Telegram запоминает, что бот хотел сделать.
    Как и настоящий Telegram, отклоняет второй ответ на одно и то же нажатие кнопки.
    fail(method) -> исключение позволяет имитировать сбои Telegram."""

    def __init__(self):
        super().__init__()
        self.calls: list[TelegramMethod] = []
        self._mid = 1000
        self.answered: set[str] = set()
        self.fail = None

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if self.fail is not None and (exc := self.fail(method)) is not None:
            raise exc
        if isinstance(method, AnswerCallbackQuery):
            if method.callback_query_id in self.answered:
                raise TelegramBadRequest(
                    method=method,
                    message="Bad Request: query is too old and response timeout expired or query ID is invalid",
                )
            self.answered.add(method.callback_query_id)
        if isinstance(method, GetMe):
            return TgUser(id=1, is_bot=True, first_name="Bot", username="test_bot")
        if isinstance(method, (SendMessage, EditMessageText)):
            self._mid += 1
            return Message(
                message_id=self._mid,
                date=datetime.now(timezone.utc),
                chat=Chat(id=int(method.chat_id or 0), type="private"),
                text=method.text,
            )
        return True

    async def close(self):
        pass

    async def stream_content(self, *a, **kw):  # pragma: no cover
        yield b""

    def texts(self) -> list[str]:
        return [c.text for c in self.calls if isinstance(c, (SendMessage, EditMessageText))]

    def last_markup_buttons(self) -> list[tuple[str, str]]:
        for c in reversed(self.calls):
            if isinstance(c, (SendMessage, EditMessageText)) and c.reply_markup is not None:
                rm = c.reply_markup
                if hasattr(rm, "inline_keyboard"):
                    return [(b.text, b.callback_data) for row in rm.inline_keyboard for b in row]
        return []

    def alerts(self) -> list[str]:
        return [c.text for c in self.calls if isinstance(c, AnswerCallbackQuery) and c.text]


class Harness:
    def __init__(self, sessionmaker):
        self.sessionmaker = sessionmaker
        self.fake = FakeSession()
        self.bot = Bot(TOKEN, session=self.fake)
        settings = Settings(bot_token=TOKEN, admin_id=777, database_url="sqlite://")
        self.notifier = TelegramNotifier(self.bot, settings.admin_id)
        self.dp = create_dispatcher(sessionmaker, settings, self.notifier)
        self.uid = 0
        self.pressed: list[str] = []

    def _next(self) -> int:
        self.uid += 1
        return self.uid

    def _message(self, chat_id: int, date: float | None = None, **fields) -> dict:
        return {
            "message_id": self._next(),
            "date": int(date if date is not None else datetime.now().timestamp()),
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "is_bot": False, "first_name": "Студент"},
            **fields,
        }

    async def feed(self, upd: dict) -> None:
        await self.dp.feed_update(self.bot, Update.model_validate(upd, context={"bot": self.bot}))

    def text_update(self, chat_id: int, text: str, date: float | None = None, **fields) -> dict:
        return {"update_id": self._next(), "message": self._message(chat_id, date, text=text, **fields)}

    def photo_update(self, chat_id: int, caption: str | None = None, media_group: str | None = None) -> dict:
        n = self._next()
        msg = self._message(chat_id, photo=[{"file_id": f"photo{n}", "file_unique_id": f"u{n}", "width": 10, "height": 10}])
        if caption is not None:
            msg["caption"] = caption
        if media_group is not None:
            msg["media_group_id"] = media_group
        return {"update_id": self._next(), "message": msg}

    def press_update(self, chat_id: int, data: str, inaccessible: bool = False) -> dict:
        message = {
            "message_id": 5,
            "date": 0 if inaccessible else int(datetime.now().timestamp()),
            "chat": {"id": chat_id, "type": "private"},
        }
        if not inaccessible:
            message["text"] = "…"
        cq = {
            "id": str(self._next()),
            "chat_instance": "x",
            "from": {"id": chat_id, "is_bot": False, "first_name": "Студент"},
            "data": data,
            "message": message,
        }
        self.pressed.append(cq["id"])
        return {"update_id": self._next(), "callback_query": cq}

    async def text(self, chat_id: int, text: str, date: float | None = None, **fields) -> None:
        await self.feed(self.text_update(chat_id, text, date, **fields))

    async def press(self, chat_id: int, data: str, inaccessible: bool = False) -> None:
        await self.feed(self.press_update(chat_id, data, inaccessible))

    def unanswered(self) -> list[str]:
        """Нажатия кнопок, на которые бот так и не ответил (у студента вечно крутится значок)."""
        return [i for i in self.pressed if i not in self.fake.answered]

    async def press_label(self, chat_id: int, starts: str) -> None:
        for text, data in self.fake.last_markup_buttons():
            if text.startswith(starts):
                await self.press(chat_id, data)
                return
        raise AssertionError(f"Нет кнопки «{starts}»: {self.fake.last_markup_buttons()}")


@pytest.fixture
def h(sessionmaker):
    return Harness(sessionmaker)


A, B = 111, 222


async def test_onboarding_and_request(h):
    await h.text(A, "/start")
    assert any("неофициальный" in t for t in h.fake.texts())
    await h.text(A, "ИМЭС")
    async with h.sessionmaker() as s:
        assert (await s.scalar(select(UniversityRequest))).name == "ИМЭС"
        assert (await s.scalar(select(User))).onboarded


async def test_add_homework_with_reminder_and_delivery(h):
    await h.text(A, "/start")
    await h.press_label(A, "Пропустить")
    await h.text(A, kb.ADD_HW)
    await h.text(A, "Матанализ")  # предметов ещё нет — вводим название
    await h.text(A, "Решить №1-10")
    await h.press_label(A, "Завтра")
    await h.text(A, "23:59")
    assert any("Задание сохранено" in t for t in h.fake.texts())

    async with h.sessionmaker() as s:
        hw = await s.scalar(select(Homework))
        assert hw.text == "Решить №1-10"
        assert hw.due_date == local_now("Europe/Moscow").date() + timedelta(days=1)
        r = await s.scalar(select(Reminder))
        assert r.local_at.hour == 23 and r.local_at.minute == 59

    # Планировщик отправляет напоминание в нужный момент через Telegram
    h.fake.calls.clear()
    alerts = scheduler.AdminAlerts(h.notifier)
    await scheduler.tick(h.sessionmaker, h.notifier, alerts, now=r.fire_at - timedelta(minutes=1))
    assert not [c for c in h.fake.calls if isinstance(c, SendMessage) and "Напоминание" in c.text]
    await scheduler.tick(h.sessionmaker, h.notifier, alerts, now=r.fire_at)
    sent = [c for c in h.fake.calls if isinstance(c, SendMessage) and "Напоминание" in c.text]
    assert len(sent) == 1 and sent[0].chat_id == A and "Решить №1-10" in sent[0].text

    # Отметить выполненным прямо из напоминания
    await h.press(A, kb.HwCb(action="done", id=hw.id).pack())
    async with h.sessionmaker() as s:
        assert (await s.get(Homework, hw.id)).done


async def test_cannot_open_foreign_homework(h):
    await h.text(A, kb.ADD_HW)
    await h.text(A, "История")
    await h.text(A, "Секретное задание А")
    await h.press_label(A, "Без срока")
    await h.press_label(A, "Без напоминания")
    async with h.sessionmaker() as s:
        hw = await s.scalar(select(Homework))
        subject = await s.scalar(select(Subject))

    h.fake.calls.clear()
    for action in ("open", "done", "delok", "etext", "files"):
        await h.press(B, kb.HwCb(action=action, id=hw.id).pack())
    await h.press(B, kb.SubjCb(action="delok", id=subject.id).pack())
    await h.text(B, kb.HW)
    assert not any("Секретное" in (t or "") for t in h.fake.texts())
    assert any("не найдено" in a for a in h.fake.alerts())
    async with h.sessionmaker() as s:
        hw = await s.get(Homework, hw.id)
        assert hw is not None and not hw.done
        assert await s.get(Subject, subject.id) is not None


async def test_add_lesson_and_view_today(h):
    await h.text(A, "/start")
    await h.press(A, kb.Nav(to="add_lesson").pack())
    await h.text(A, "Мировая экономика")
    today = local_now("Europe/Moscow").date()
    await h.press(A, kb.Pick(kind="wd", value=str(today.weekday())).pack())
    await h.text(A, "9:00-10:30")
    await h.press_label(A, "Каждую неделю")
    await h.text(A, "305, Иванова А.П., лекция")
    async with h.sessionmaker() as s:
        lesson = await s.scalar(select(Lesson))
        assert lesson.room == "305" and lesson.teacher == "Иванова А.П." and lesson.kind == "лекция"
    h.fake.calls.clear()
    await h.text(A, kb.TODAY)
    assert "Мировая экономика" in h.fake.texts()[-1]
    # Отмена пары только на сегодня
    await h.press(A, kb.LessonCb(action="cancel", id=lesson.id, day=today.isoformat()).pack())
    await h.text(A, kb.TODAY)
    assert "Мировая экономика" not in h.fake.texts()[-1]


async def test_due_next_lesson_and_reminder_preset_buttons(h):
    # Пара по предмету каждый день недели — «следующая пара» всегда есть
    for wd in range(7):
        await h.press(A, kb.Nav(to="add_lesson").pack())
        if wd == 0:
            await h.text(A, "Статистика")
        else:
            await h.press_label(A, "Статистика")
        await h.press(A, kb.Pick(kind="wd", value=str(wd)).pack())
        await h.text(A, "10:00-11:30")
        await h.press_label(A, "Каждую неделю")
        await h.press_label(A, "Пропустить")
    await h.text(A, kb.ADD_HW)
    await h.press_label(A, "Статистика")
    await h.text(A, "Отчёт")
    await h.press_label(A, "К следующей паре")
    await h.press_label(A, "Без напоминания")
    async with h.sessionmaker() as s:
        hw = await s.scalar(select(Homework))
        assert hw.due_time is not None and hw.due_time.hour == 10
    # Готовый вариант напоминания: срок через неделю → «В день сдачи в 8:00»
    await h.press(A, kb.HwCb(action="edue", id=hw.id).pack())
    await h.press_label(A, "Через неделю")
    await h.press(A, kb.HwCb(action="erem", id=hw.id).pack())
    await h.press_label(A, "В день сдачи")
    async with h.sessionmaker() as s:
        r = await s.scalar(select(Reminder))
        assert r.local_at.hour == 8 and r.local_at.date() == local_now("Europe/Moscow").date() + timedelta(days=7)


async def test_menu_interrupts_dialog_and_settings(h):
    await h.text(A, kb.ADD_HW)
    await h.text(A, kb.SETTINGS)  # кнопка меню посреди диалога
    assert "Настройки" in h.fake.texts()[-1]
    await h.press(A, kb.Nav(to="set_tz").pack())
    await h.press_label(A, "Владивосток")
    async with h.sessionmaker() as s:
        assert (await s.scalar(select(User))).timezone == "Asia/Vladivostok"


async def test_admin_stats_only_for_admin(h):
    await h.text(A, "/stats")
    assert not any("Статистика" in t for t in h.fake.texts())
    await h.text(777, "/stats")
    assert any("Статистика" in t for t in h.fake.texts())


async def test_delete_me(h):
    await h.text(A, kb.ADD_HW)
    await h.text(A, "Право")
    await h.text(A, "Конспект")
    await h.press_label(A, "Без срока")
    await h.press_label(A, "Без напоминания")
    await h.text(A, "/delete_me")
    await h.press_label(A, "Да, удалить всё")
    async with h.sessionmaker() as s:
        assert await s.scalar(select(User)) is None
        assert await s.scalar(select(Homework)) is None
