"""Тесты на ошибки, найденные перекрёстной проверкой этапа 2 (номера F… — из отчёта проверки)."""

import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from sqlalchemy import select

from bot import scheduler
from bot.alerts import AdminAlerts
from bot.config import Settings
from bot.core import homework as hw_core
from bot.core import reminders, render
from bot.core import schedule as sch
from bot.core import subjects as subj
from bot.core.timeutil import (
    ParseError,
    default_week1,
    local_now,
    parse_date,
    parse_date_time,
    parse_moment,
    parse_weekday,
    week_number,
)
from bot.core.users import get_or_create_user
from bot.db import make_engine
from bot.messenger import MessageRejected, TemporaryFailure
from bot.models import Attachment, Homework, Lesson, Reminder, User
from bot.telegram import kb
from bot.telegram.app import create_bot
from bot.telegram.handlers_schedule import _parse_period
from bot.telegram.ui import clip
from tests.test_reminders import FakeNotifier, utc
from tests.test_telegram_flow import TOKEN, A, B, Harness


@pytest.fixture
def h(sessionmaker):
    return Harness(sessionmaker)


TODAY = date(2026, 11, 12)  # четверг


# ---------------- Разбор дат и времени (F5, F8, F9, F11, F27, F32) ----------------

def test_F9_recent_past_date_stays_this_year():
    assert parse_date("10.11", TODAY) == date(2026, 11, 10)  # просрочено, а не через год
    assert parse_date("15.05", TODAY) == date(2027, 5, 15)
    assert parse_date("10.01", TODAY) == date(2027, 1, 10)
    moment = parse_moment("10.11 9:00", datetime(2026, 11, 12, 12, 0))
    assert moment < datetime(2026, 11, 12, 12, 0)  # обработчик отвергнет: «время уже прошло»


def test_F5_F8_semester_dates_nearest():
    assert parse_date("01.09", TODAY, mode="nearest") == date(2026, 9, 1)
    assert parse_date("08.02", date(2027, 2, 20), mode="nearest") == date(2027, 2, 8)
    assert parse_date("31.01", date(2026, 11, 1), mode="after") == date(2027, 1, 31)
    assert parse_date("29.02", TODAY) == date(2028, 2, 29)


@pytest.mark.parametrize(
    "text,today_,expected",
    [
        ("01.09-25.12", TODAY, (date(2026, 9, 1), date(2026, 12, 25))),
        ("01.10-", TODAY, (date(2026, 10, 1), None)),
        ("-30.10", TODAY, (None, date(2026, 10, 30))),
        ("-25.12", TODAY, (None, date(2026, 12, 25))),
        ("-", TODAY, (None, None)),
        ("01.11-31.01", TODAY, (date(2026, 11, 1), date(2027, 1, 31))),
        ("09.02-30.06", date(2026, 12, 10), (date(2027, 2, 9), date(2027, 6, 30))),
        ("09.02-30.06", date(2027, 3, 10), (date(2027, 2, 9), date(2027, 6, 30))),
        ("01.09.2026-25.12.2026", TODAY, (date(2026, 9, 1), date(2026, 12, 25))),
    ],
)
def test_F5_period_parsing(text, today_, expected):
    assert _parse_period(text, today_) == expected


def test_F5_period_errors():
    with pytest.raises(ParseError):
        _parse_period("25.12.2026-01.09.2026", TODAY)
    with pytest.raises(ParseError):
        _parse_period("01.09", TODAY)


def test_F11_dotted_time_for_reminder():
    now = datetime(2026, 11, 12, 18, 0)
    assert parse_moment("19.30", now) == datetime(2026, 11, 12, 19, 30)
    assert parse_moment("9.30", now) == datetime(2026, 11, 13, 9, 30)
    with pytest.raises(ParseError, match="двоеточие"):
        parse_moment("14.05", now)  # и дата, и время — переспрашиваем, как написать время


def test_F27_russian_phrasing():
    now = datetime(2026, 11, 12, 18, 0)
    assert parse_moment("в 19:30", now) == datetime(2026, 11, 12, 19, 30)
    assert parse_moment("завтра в 9:00", now) == datetime(2026, 11, 13, 9, 0)
    assert parse_moment("9:00 завтра", now) == datetime(2026, 11, 13, 9, 0)
    assert parse_moment("15.11, 18:00", now) == datetime(2026, 11, 15, 18, 0)
    assert parse_date_time("до 15.11.", TODAY) == (date(2026, 11, 15), None)
    assert parse_date_time("15.11 24:00", TODAY) == (date(2026, 11, 15), time(23, 59))
    assert parse_date_time("к 18:00 15.11", TODAY) == (date(2026, 11, 15), time(18, 0))
    assert parse_weekday("в среду") == 2 and parse_weekday("Пт") == 4 and parse_weekday("пончик") is None


def test_F32_default_first_week():
    w1 = default_week1(date(2027, 8, 30))  # 1 сентября 2027 — среда
    assert w1 == date(2027, 8, 30) and week_number(date(2027, 8, 30), w1) == 1
    assert default_week1(date(2030, 9, 2)) == date(2030, 9, 2)  # 1 сентября 2030 — воскресенье
    assert default_week1(TODAY) == date(2026, 8, 31)


# ---------------- Тексты (F6, F28, F30, F35) ----------------

def _hw(text="x", due=None, due_time=None, done=False):
    return Homework(id=1, user_id=1, subject_id=1, text=text, due_date=due, due_time=due_time, done=done)


def test_F6_digest_is_capped():
    items = [(_hw("Решить задачи " * 6, date(2026, 11, 20)), "Иностранный язык в профессиональной деятельности")] * 300
    text = render.digest_text(items, datetime(2026, 11, 12, 20, 0))
    assert render.visible_len(text) <= render.DIGEST_MAX_LEN
    assert "Невыполненные задания (300)" in text and "…и ещё" in text


def test_F28_overdue_today_and_due_times():
    now = datetime(2026, 11, 12, 20, 0)
    text = render.hw_list_text(
        [(_hw("лаба", TODAY, time(10, 0)), "Физика"), (_hw("эссе", TODAY + timedelta(days=1), time(8, 0)), "История")],
        now,
    )
    assert text.index("Просрочено") < text.index("лаба") < text.index("Завтра")
    assert "(до 8:00)" in text


def test_F30_week_compact_when_too_long():
    lessons, subjects = [], {}
    for wd in range(6):
        for i in range(5):
            sid = wd * 10 + i
            subjects[sid] = "Иностранный язык в профессиональной деятельности"
            lessons.append(Lesson(id=sid, user_id=1, subject_id=sid, weekday=wd, parity=0, start_time=time(8 + i * 2),
                                  end_time=time(9 + i * 2, 30), room="Ауд. 305 (корпус 2)", teacher="Иванова Анна Петровна", kind="практическое занятие"))
    s = sch.Schedule(lessons, subjects, set(), date(2026, 8, 31))
    text = render.week_text(s, TODAY, max_len=3900)
    assert render.visible_len(text) <= 3900
    assert "Иванова" not in text and text.count("Иностранный") == 30  # все пары на месте, без подробностей


def test_F35_clip_counts_visible_text():
    text = "<b>Код</b>\n" + "&quot;x&quot; " * 400 + "\n📅 Срок: завтра"  # ~4000 знаков разметки, ~1600 видимых
    assert clip(text) == text


# ---------------- Напоминания и сводка (F7, F10, F12, F24, F25, F26) ----------------

def test_F25_presets_respect_due_time():
    now = datetime(2026, 11, 12, 12, 0)
    labels = [l for l, _ in reminders.reminder_presets(TODAY + timedelta(days=1), time(7, 30), now)]
    assert labels == ["Накануне в 20:00"]


async def _user_hw(session, ext="1", tz="Europe/Moscow", digest=False):
    user = await get_or_create_user(session, "telegram", ext)
    user.timezone, user.digest_enabled = tz, digest
    s = await subj.find_or_create(session, user.id, "Матанализ")
    hw = await hw_core.add(session, user.id, s.id, "Решить задачи", date(2026, 11, 16))
    return user, hw


async def test_F12_late_digest_after_midnight(sessionmaker):
    async with sessionmaker() as session:
        user, _ = await _user_hw(session, digest=True)
        user.digest_time = time(22, 0)
        await session.commit()
    n = FakeNotifier()
    alerts = AdminAlerts(n)
    await scheduler.tick(sessionmaker, n, alerts, utc(2026, 11, 15, 21, 30))  # 00:30 МСК — бот «проснулся»
    assert len(n.sent) == 1
    await scheduler.tick(sessionmaker, n, alerts, utc(2026, 11, 15, 21, 40))
    assert len(n.sent) == 1  # не дважды
    await scheduler.tick(sessionmaker, n, alerts, utc(2026, 11, 16, 19, 0))  # 22:00 следующего дня
    assert len(n.sent) == 2


async def test_F10_reminder_waits_for_network(sessionmaker):
    async with sessionmaker() as session:
        user, hw = await _user_hw(session)
        await reminders.set_reminder(session, user, hw, datetime(2026, 11, 15, 20, 0))
        await session.commit()

    class Offline(FakeNotifier):
        async def send_text(self, user, text, homework_id=None):
            raise TemporaryFailure("нет связи")

    off = Offline()
    for minute in range(20):  # 10 минут без связи
        await scheduler.tick(sessionmaker, off, AdminAlerts(off), utc(2026, 11, 15, 17, minute))
    n = FakeNotifier()
    await scheduler.tick(sessionmaker, n, AdminAlerts(n), utc(2026, 11, 15, 18, 0))
    assert len(n.sent) == 1  # связь вернулась — напоминание пришло


async def test_F10_admin_alert_not_muted_by_failed_delivery():
    class Flaky(FakeNotifier):
        fail = True

        async def notify_admin(self, text):
            if self.fail:
                raise ConnectionError()
            self.admin.append(text)

    n = Flaky()
    alerts = AdminAlerts(n)
    await alerts.report("x", "первая")
    n.fail = False
    await alerts.report("x", "вторая")
    assert n.admin == ["⚠️ вторая"]


async def test_F6_rejected_digest_not_retried(sessionmaker):
    async with sessionmaker() as session:
        await _user_hw(session, digest=True)
        await session.commit()

    class Rejecting(FakeNotifier):
        calls = 0

        async def send_text(self, user, text, homework_id=None):
            self.calls += 1
            raise MessageRejected("message is too long")

    n = Rejecting()
    alerts = AdminAlerts(n)
    for minute in range(0, 10):
        await scheduler.tick(sessionmaker, n, alerts, utc(2026, 11, 15, 17, minute))
    assert n.calls == 1 and len(n.admin) == 1


async def test_F26_homework_deleted_while_sending(sessionmaker):
    async with sessionmaker() as session:
        user, hw = await _user_hw(session)
        await reminders.set_reminder(session, user, hw, datetime(2026, 11, 15, 20, 0))
        await session.commit()
        hw_id = hw.id

    class DeletingNotifier(FakeNotifier):
        async def send_text(self, user, text, homework_id=None):
            async with sessionmaker() as s2:  # студент удалил задание, пока шла отправка
                await s2.delete(await s2.get(Homework, hw_id))
                await s2.commit()
            self.sent.append((user.external_id, text))

    n = DeletingNotifier()
    await scheduler.tick(sessionmaker, n, AdminAlerts(n), utc(2026, 11, 15, 17, 0))  # без исключения
    assert n.admin == []


async def test_F24_bad_timezone_does_not_stop_other_digests(sessionmaker):
    async with sessionmaker() as session:
        bad, _ = await _user_hw(session, ext="1", digest=True)
        bad.timezone = "Asia/Ekaterinburg"  # опечатка, такого пояса нет
        await _user_hw(session, ext="2", digest=True)
        await session.commit()
    n = FakeNotifier()
    await scheduler.tick(sessionmaker, n, AdminAlerts(n), utc(2026, 11, 15, 17, 0))
    assert [ext for ext, _ in n.sent] == ["2"]


def test_F24_F18_settings_validation():
    with pytest.raises(ValueError):
        Settings(bot_token=TOKEN, default_timezone="Asia/Ekaterinburg", _env_file=None)
    with pytest.raises(ValueError):
        Settings(bot_token=TOKEN, telegram_api_url="api.telegram.org", _env_file=None)
    s = Settings(bot_token=f'  "{TOKEN}" ', _env_file=None)
    assert s.bot_token.get_secret_value() == TOKEN  # пробелы и кавычки при вставке не мешают


def test_F18_startup_error_does_not_print_token(monkeypatch, capsys):
    from bot import __main__ as entry

    monkeypatch.setenv("BOT_TOKEN", TOKEN)
    monkeypatch.setenv("ADMIN_ID", TOKEN)  # токен по ошибке вставлен не в ту строку
    monkeypatch.chdir("/")
    with pytest.raises(SystemExit):
        entry.run()
    err = capsys.readouterr().err
    assert "ADMIN_ID" in err and TOKEN not in err and "A" * 35 not in err
    logging.getLogger().handlers.clear()


def test_F14_proxy_supported():
    bot = create_bot(Settings(bot_token=TOKEN, telegram_proxy="socks5://user:pass@127.0.0.1:1080", _env_file=None))
    assert bot.session.proxy == "socks5://user:pass@127.0.0.1:1080"


async def test_F15_sql_parameters_hidden():
    engine = make_engine("sqlite+aiosqlite://")
    assert engine.sync_engine.hide_parameters is True
    await engine.dispose()


async def test_F16_lesson_on_foreign_subject_refused(session):
    a = await get_or_create_user(session, "telegram", "100")
    b = await get_or_create_user(session, "telegram", "200")
    sa = await subj.find_or_create(session, a.id, "Право")
    with pytest.raises(sch.ScheduleError):
        await sch.add_lesson(session, b.id, sa.id, time(9), time(10), weekday=0)


# ---------------- Диалоги в Telegram ----------------

async def test_F1_digest_menu_buttons(h):
    await h.text(A, kb.SETTINGS)
    await h.press_label(A, "🕗 Сводка")
    await h.press_label(A, "07:00")
    async with h.sessionmaker() as s:
        u = await s.scalar(select(User))
        assert u.digest_time == time(7, 0) and u.digest_enabled
    await h.press_label(A, "🕗 Сводка")
    await h.press_label(A, "Выключить сводку")
    async with h.sessionmaker() as s:
        assert not (await s.scalar(select(User))).digest_enabled
    assert h.unanswered() == []


async def _make_lesson(h, chat=A, subject="Матанализ"):
    await h.press(chat, kb.Nav(to="add_lesson").pack())
    await h.text(chat, subject)
    await h.press(chat, kb.Pick(kind="wd", value="0").pack())
    await h.text(chat, "9:00-10:30")
    await h.press_label(chat, "Каждую неделю")
    await h.press_label(chat, "Пропустить")


async def _make_hw(h, chat=A, subject="История", text="Конспект"):
    await h.text(chat, kb.ADD_HW)
    await h.text(chat, subject)
    await h.text(chat, text)
    await h.press_label(chat, "Без срока")
    await h.press_label(chat, "Без напоминания")


async def test_F2_delete_answers_once_and_persists(h):
    await _make_lesson(h)
    async with h.sessionmaker() as s:
        lesson = await s.scalar(select(Lesson))
    await h.press(A, kb.LessonCb(action="del", id=lesson.id).pack())
    await _make_hw(h)
    async with h.sessionmaker() as s:
        hw = await s.scalar(select(Homework))
    await h.press(A, kb.HwCb(action="delok", id=hw.id).pack())
    async with h.sessionmaker() as s:
        assert await s.scalar(select(Lesson)) is None and await s.scalar(select(Homework)) is None
    assert h.unanswered() == []
    assert "Пара удалена" in h.fake.alerts() and "Удалено" in h.fake.alerts()


async def test_F3_failed_answer_does_not_undo_work(h):
    await _make_hw(h)
    async with h.sessionmaker() as s:
        hw = await s.scalar(select(Homework))
    h.fake.fail = lambda m: (
        TelegramBadRequest(method=m, message="Bad Request: query is too old") if isinstance(m, AnswerCallbackQuery) else None
    )
    await h.press(A, kb.HwCb(action="done", id=hw.id).pack())
    await h.press(A, kb.Nav(to="delete_me").pack())
    await h.press(A, kb.Nav(to="delete_me_ok").pack())
    async with h.sessionmaker() as s:
        assert await s.scalar(select(User)) is None  # удаление не откатилось


async def test_F3_network_error_after_change_keeps_change(h):
    await _make_hw(h)
    async with h.sessionmaker() as s:
        hw = await s.scalar(select(Homework))
    h.fake.fail = lambda m: TelegramNetworkError(method=m, message="timeout") if isinstance(m, EditMessageText) else None
    await h.press(A, kb.HwCb(action="done", id=hw.id).pack())
    async with h.sessionmaker() as s:
        assert (await s.get(Homework, hw.id)).done


async def test_F4_album_and_double_tap(h):
    await h.text(A, kb.ADD_HW)
    await h.text(A, "Геометрия")
    album = [h.photo_update(A, "Задачи 1-5 со страницы 42", "g1"), h.photo_update(A, None, "g1"), h.photo_update(A, None, "g1")]
    await asyncio.gather(*(h.feed(u) for u in album))
    await h.press_label(A, "Без срока")
    upd = h.press_update(A, kb.Pick(kind="rem", value="none").pack())
    upd2 = h.press_update(A, kb.Pick(kind="rem", value="none").pack())
    await asyncio.gather(h.feed(upd), h.feed(upd2))  # двойное нажатие
    async with h.sessionmaker() as s:
        hws = list(await s.scalars(select(Homework)))
        assert len(hws) == 1 and hws[0].text == "Задачи 1-5 со страницы 42"
        assert len(list(await s.scalars(select(Attachment)))) == 3
    assert h.unanswered() == []


async def test_F7_no_reminder_for_done_or_disabled(h):
    await _make_hw(h)
    async with h.sessionmaker() as s:
        hw = await s.scalar(select(Homework))
    await h.press(A, kb.HwCb(action="done", id=hw.id).pack())
    assert not any(t == "⏰ Напоминание" for t, _ in h.fake.last_markup_buttons())
    await h.press(A, kb.HwCb(action="erem", id=hw.id).pack())  # старая кнопка
    assert "о нём я не напоминаю" in h.fake.texts()[-1]
    await h.press(A, kb.HwCb(action="undone", id=hw.id).pack())
    await h.text(A, kb.SETTINGS)
    await h.press(A, kb.Nav(to="set_rem_toggle").pack())  # напоминания выключены
    await h.press(A, kb.HwCb(action="erem", id=hw.id).pack())
    assert "Напоминания выключены" in h.fake.texts()[-1]
    await h.text(A, "23:59")
    assert "не придёт" in h.fake.texts()[-1]


async def test_F13_timezone_shown_in_reminder_prompt(h):
    await h.text(A, kb.ADD_HW)
    await h.text(A, "История")
    await h.text(A, "Эссе")
    await h.press_label(A, "Без срока")
    assert "Москва (UTC+3)" in h.fake.texts()[-1]


async def test_F17_delete_me_clears_dialog(h):
    await h.text(A, "/delete_me")
    await h.text(A, kb.ADD_HW)
    await h.text(A, "Право")
    await h.text(A, "ЛИЧНОЕ: телефон")
    await h.press(A, kb.Nav(to="delete_me_ok").pack())
    await h.press(A, kb.Pick(kind="due", value="none").pack())  # старая кнопка срока
    async with h.sessionmaker() as s:
        assert await s.scalar(select(Homework)) is None
    assert "Эта кнопка уже не действует. Начните заново из меню." in h.fake.alerts()


async def test_F19_one_off_lesson_with_empty_schedule(h):
    await h.text(A, kb.TODAY)
    labels = [t for t, _ in h.fake.last_markup_buttons()]
    assert "✏️ Изменения на этот день" in labels and "✏️ Внести расписание" in labels
    await h.press_label(A, "✏️ Изменения на этот день")
    await h.press_label(A, "➕ Разовая пара")
    await h.text(A, "Консультация")
    await h.text(A, "15:00-16:30")
    await h.press_label(A, "Пропустить")
    await h.text(A, kb.TODAY)
    assert "Консультация" in h.fake.texts()[-1]


async def test_F20_typed_answers_at_button_steps(h):
    await _make_hw(h, subject="История")
    await h.text(A, kb.ADD_HW)
    await h.text(A, "Химия")  # предметы уже есть, но название можно написать
    await h.text(A, "Параграф 5")
    assert "К какому сроку" in h.fake.texts()[-1]
    await h.press(A, kb.Nav(to="add_lesson").pack())
    await h.text(A, "История")
    await h.text(A, "в четверг")
    assert "время пары" in h.fake.texts()[-1].lower()
    await h.text(A, "/privacy")
    assert "Такой команды нет" in h.fake.texts()[-1]


async def test_F21_files_at_reminder_step_and_outside_dialog(h):
    await h.text(A, kb.ADD_HW)
    await h.text(A, "История")
    await h.text(A, "Эссе")
    await h.press_label(A, "Без срока")
    await h.feed(h.photo_update(A))
    assert "время напоминания" in h.fake.texts()[-1]
    await h.press_label(A, "Без напоминания")
    async with h.sessionmaker() as s:
        assert len(list(await s.scalars(select(Attachment)))) == 1
    await h.feed(h.photo_update(A))
    assert "➕ Задание" in h.fake.texts()[-1]


async def test_F22_F39_long_text(h):
    await _make_hw(h)
    async with h.sessionmaker() as s:
        hw = await s.scalar(select(Homework))
    await h.press(A, kb.HwCb(action="etext", id=hw.id).pack())
    t0 = datetime.now().timestamp()
    await h.text(A, "а" * 3500, date=t0)
    await h.text(A, "хвост длинной вставки", date=t0 + 1)  # Telegram разрезал вставку
    await h.text(A, "Короткий текст", date=t0 + 30)
    async with h.sessionmaker() as s:
        assert (await s.get(Homework, hw.id)).text == "Короткий текст"


async def test_F29_cancel_in_own_row(h):
    await _make_lesson(h)
    await h.press(A, kb.Nav(to="add_lesson").pack())
    await h.press_label(A, "Матанализ")
    await h.press(A, kb.Pick(kind="wd", value="1").pack())
    rows = [c.reply_markup.inline_keyboard for c in h.fake.calls if isinstance(c, EditMessageText) and c.reply_markup][-1]
    assert [b.text for b in rows[-1]] == ["✖️ Отмена"]


async def test_F33_next_with_empty_schedule(h):
    await h.text(A, kb.NEXT)
    assert "не внесено" in h.fake.texts()[-1]


async def test_F34_inaccessible_message(h):
    await _make_hw(h)
    async with h.sessionmaker() as s:
        hw = await s.scalar(select(Homework))
    await h.press(A, kb.HwCb(action="done", id=hw.id).pack(), inaccessible=True)
    async with h.sessionmaker() as s:
        assert (await s.get(Homework, hw.id)).done
    assert h.unanswered() == []


async def test_F36_F38_stale_buttons_answered(h):
    await _make_hw(h)
    async with h.sessionmaker() as s:
        hw = await s.scalar(select(Homework))
    await h.press(A, kb.Pick(kind="due", value="none").pack())
    await h.press(A, kb.HwCb(action="delok", id=hw.id).pack())
    await h.press(A, kb.HwCb(action="etext", id=hw.id).pack())
    assert h.unanswered() == []
    assert "Задание не найдено — возможно, оно удалено." in h.fake.alerts()


async def test_F37_hidden_links_kept(h):
    await h.text(A, kb.ADD_HW)
    await h.text(A, "История")
    await h.text(A, "Задание на сайте", entities=[{"type": "text_link", "offset": 0, "length": 7, "url": "https://example.org/task"}])
    await h.press_label(A, "Без срока")
    await h.press_label(A, "Без напоминания")
    async with h.sessionmaker() as s:
        assert "https://example.org/task" in (await s.scalar(select(Homework))).text


async def test_F40_F41_F42_texts(h):
    await _make_hw(h)
    async with h.sessionmaker() as s:
        hw = await s.scalar(select(Homework))
    await h.press(A, kb.HwCb(action="edue", id=hw.id).pack())
    await h.press_label(A, "Завтра")
    assert "Напоминание осталось прежним" not in h.fake.texts()[-1]
    await h.press(A, kb.HwCb(action="esubj", id=hw.id).pack())
    assert "Других предметов пока нет" in h.fake.texts()[-1]
    await h.press(A, kb.DayCb(action="edit", day="2026-11-18").pack())  # среда
    assert "Изменения на среду" in h.fake.texts()[-1]


async def test_F44_stats_hidden_from_others(h):
    await h.text(B, "/stats")
    assert "Не понял" in h.fake.texts()[-1]


async def test_F49_handler_error_reported(h, monkeypatch):
    from bot.telegram import handlers_schedule

    async def broken(*a, **kw):
        raise RuntimeError("сбой")

    monkeypatch.setattr(handlers_schedule, "_load", broken)
    await h.text(A, kb.TODAY)
    sent = [c for c in h.fake.calls if isinstance(c, SendMessage)]
    assert any("Что-то пошло не так" in c.text and c.chat_id == A for c in sent)
    assert any("Ошибка в боте: RuntimeError" in c.text and c.chat_id == 777 for c in sent)
