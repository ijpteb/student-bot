from datetime import date, datetime, time, timezone

from bot import scheduler
from bot.core import homework as hw_core
from bot.core import reminders
from bot.core import subjects as subj
from bot.core.users import delete_user_data, get_or_create_user, set_timezone
from bot.messenger import RecipientUnavailable
from bot.models import Reminder


class FakeNotifier:
    def __init__(self, fail_for: set[str] | None = None, broken: bool = False):
        self.sent: list[tuple[str, str]] = []
        self.admin: list[str] = []
        self.fail_for = fail_for or set()
        self.broken = broken

    async def send_text(self, user, text, homework_id=None):
        if user.external_id in self.fail_for:
            raise RecipientUnavailable()
        if self.broken:
            raise ConnectionError("нет связи")
        self.sent.append((user.external_id, text))

    async def send_attachments(self, user, files):
        pass

    async def notify_admin(self, text):
        self.admin.append(text)


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


async def _hw(session, ext="1", tz="Europe/Moscow", text="Решить задачи", digest=False):
    user = await get_or_create_user(session, "telegram", ext)
    user.timezone = tz
    user.digest_enabled = digest  # в тестах напоминаний сводка не мешает
    s = await subj.find_or_create(session, user.id, "Матанализ")
    hw = await hw_core.add(session, user.id, s.id, text, date(2026, 11, 16))
    return user, hw


async def _tick(sessionmaker, notifier, now):
    await scheduler.tick(sessionmaker, notifier, scheduler.AdminAlerts(notifier), now)


async def test_reminder_fires_at_local_time_once(sessionmaker):
    async with sessionmaker() as session:
        user, hw = await _hw(session)
        await reminders.set_reminder(session, user, hw, datetime(2026, 11, 15, 20, 0))
        await session.commit()

    n = FakeNotifier()
    await _tick(sessionmaker, n, utc(2026, 11, 15, 16, 59))  # 19:59 по Москве — рано
    assert n.sent == []
    await _tick(sessionmaker, n, utc(2026, 11, 15, 17, 0))  # 20:00 по Москве
    assert len(n.sent) == 1 and "Решить задачи" in n.sent[0][1]
    # «Перезапуск»: новый планировщик читает ту же базу и не повторяет напоминание
    await _tick(sessionmaker, FakeNotifier(), utc(2026, 11, 15, 17, 1))
    await _tick(sessionmaker, n, utc(2026, 11, 15, 18, 0))
    assert len(n.sent) == 1


async def test_reminder_after_downtime_is_not_lost(sessionmaker):
    async with sessionmaker() as session:
        user, hw = await _hw(session)
        await reminders.set_reminder(session, user, hw, datetime(2026, 11, 15, 20, 0))
        await session.commit()
    n = FakeNotifier()
    await _tick(sessionmaker, n, utc(2026, 11, 15, 21, 0))  # бот «лежал» 4 часа
    assert len(n.sent) == 1


async def test_vladivostok_timezone(sessionmaker):
    async with sessionmaker() as session:
        user, hw = await _hw(session, tz="Asia/Vladivostok")
        await reminders.set_reminder(session, user, hw, datetime(2026, 11, 15, 8, 0))
        await session.commit()
    n = FakeNotifier()
    await _tick(sessionmaker, n, utc(2026, 11, 14, 21, 59))
    assert n.sent == []
    await _tick(sessionmaker, n, utc(2026, 11, 14, 22, 0))  # 8:00 во Владивостоке
    assert len(n.sent) == 1


async def test_timezone_change_moves_reminder(sessionmaker):
    async with sessionmaker() as session:
        user, hw = await _hw(session)
        await reminders.set_reminder(session, user, hw, datetime(2026, 11, 15, 20, 0))
        await set_timezone(session, user, "Asia/Yekaterinburg")
        await session.commit()
        r = await reminders.get_reminder(session, hw.id)
        assert r.fire_at == utc(2026, 11, 15, 15, 0)  # 20:00 по Екатеринбургу


async def test_no_reminder_for_done_or_disabled(sessionmaker):
    async with sessionmaker() as session:
        user, hw = await _hw(session)
        await reminders.set_reminder(session, user, hw, datetime(2026, 11, 15, 20, 0))
        await hw_core.set_done(hw, True)
        user2, hw2 = await _hw(session, ext="2")
        await reminders.set_reminder(session, user2, hw2, datetime(2026, 11, 15, 20, 0))
        user2.reminders_enabled = False
        await session.commit()
    n = FakeNotifier()
    await _tick(sessionmaker, n, utc(2026, 11, 15, 18, 0))
    assert n.sent == []


async def test_new_reminder_replaces_old(sessionmaker):
    async with sessionmaker() as session:
        user, hw = await _hw(session)
        await reminders.set_reminder(session, user, hw, datetime(2026, 11, 15, 20, 0))
        await reminders.set_reminder(session, user, hw, datetime(2026, 11, 16, 8, 0))
        await session.commit()
    n = FakeNotifier()
    await _tick(sessionmaker, n, utc(2026, 11, 15, 18, 0))
    assert n.sent == []
    await _tick(sessionmaker, n, utc(2026, 11, 16, 5, 0))
    assert len(n.sent) == 1


async def test_blocked_user_and_network_errors(sessionmaker):
    async with sessionmaker() as session:
        user, hw = await _hw(session)
        await reminders.set_reminder(session, user, hw, datetime(2026, 11, 15, 20, 0))
        await session.commit()
    broken = FakeNotifier(broken=True)
    for minute in range(reminders.MAX_ATTEMPTS):
        await scheduler.tick(sessionmaker, broken, scheduler.AdminAlerts(broken), utc(2026, 11, 15, 17, minute))
    assert broken.sent == [] and len(broken.admin) >= 1  # владелец узнал об ошибке
    async with sessionmaker() as session:
        r = await session.get(Reminder, 1)
        assert r.sent_at is not None  # после 5 попыток больше не пытаемся

    async with sessionmaker() as session:
        user, hw = await _hw(session, ext="9")
        await reminders.set_reminder(session, user, hw, datetime(2026, 11, 15, 20, 0))
        await session.commit()
    n = FakeNotifier(fail_for={"9"})
    await _tick(sessionmaker, n, utc(2026, 11, 15, 17, 30))
    from sqlalchemy import select

    from bot.models import User

    async with sessionmaker() as session:
        u = await session.scalar(select(User).where(User.external_id == "9"))
        assert u.blocked is True  # заблокировал бота — больше не пишем
        u = await get_or_create_user(session, "telegram", "9")
        assert u.blocked is False  # снова написал боту — отметка снимается


async def test_digest(sessionmaker):
    async with sessionmaker() as session:
        user, hw = await _hw(session, digest=True)
        user.digest_time = time(20, 0)
        empty = await get_or_create_user(session, "telegram", "2")  # без заданий
        await session.commit()
    n = FakeNotifier()
    await _tick(sessionmaker, n, utc(2026, 11, 15, 16, 59))
    assert n.sent == []
    await _tick(sessionmaker, n, utc(2026, 11, 15, 17, 0))
    await _tick(sessionmaker, n, utc(2026, 11, 15, 17, 30))
    assert [ext for ext, _ in n.sent] == ["1"]  # один раз и только тому, у кого есть задания
    assert "Невыполненные задания (1)" in n.sent[0][1]
    # следующий день — снова
    await _tick(sessionmaker, n, utc(2026, 11, 16, 17, 0))
    assert len(n.sent) == 2
    # бот был выключен больше 3 часов после времени сводки — сводку за этот день пропускаем
    await _tick(sessionmaker, n, utc(2026, 11, 17, 20, 30))
    assert len(n.sent) == 2


async def test_digest_skips_done(sessionmaker):
    async with sessionmaker() as session:
        user, hw = await _hw(session, digest=True)
        await hw_core.set_done(hw, True)
        await session.commit()
    n = FakeNotifier()
    await _tick(sessionmaker, n, utc(2026, 11, 15, 17, 0))
    assert n.sent == []


async def test_delete_user_data(sessionmaker):
    from sqlalchemy import func, select

    from bot.core.users import add_university_request, request_counts
    from bot.models import Homework, Subject, User

    async with sessionmaker() as session:
        user, hw = await _hw(session)
        await reminders.set_reminder(session, user, hw, datetime(2026, 11, 15, 20, 0))
        await add_university_request(session, user, "ИМЭС")
        await add_university_request(session, user, "Петров Пётр, тел 89991234567")  # случайный текст
        other, _ = await _hw(session, ext="2")
        await add_university_request(session, other, "имэс")
        await session.commit()
        await delete_user_data(session, user)
        await session.commit()
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        assert await session.scalar(select(func.count()).select_from(Homework)) == 1
        assert await session.scalar(select(func.count()).select_from(Subject)) == 1
        assert await session.scalar(select(func.count()).select_from(Reminder)) == 0
        # Счётчик по вузу, который просили и другие, сохранён без привязки к удалённому студенту,
        # а уникальный текст (мог быть чем угодно, вплоть до личных данных) удалён.
        assert await request_counts(session) == [("ИМЭС", 2)]
