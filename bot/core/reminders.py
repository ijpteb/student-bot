"""Напоминания о заданиях и ежедневная сводка.

Напоминания лежат в базе, поэтому не теряются при перезапуске: планировщик раз в полминуты
берёт те, которым пора сработать, и помечает отправленные.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.timeutil import local_now, local_to_utc
from bot.models import Homework, Reminder, User

# Если бот был выключен во время сводки, она придёт позже, но не позднее этого срока.
DIGEST_GRACE = timedelta(hours=3)
# Сколько раз пробовать отправить напоминание при неожиданной ошибке (не при пропаже связи:
# тогда напоминание просто ждёт, пока связь вернётся).
MAX_ATTEMPTS = 5


async def get_reminder(session: AsyncSession, homework_id: int) -> Reminder | None:
    return await session.scalar(
        select(Reminder).where(Reminder.homework_id == homework_id, Reminder.sent_at.is_(None))
    )


async def set_reminder(session: AsyncSession, user: User, hw: Homework, local_at: datetime | None) -> None:
    """Одно напоминание на задание: новое заменяет старое. None — убрать напоминание."""
    await session.execute(
        delete(Reminder).where(Reminder.homework_id == hw.id, Reminder.sent_at.is_(None))
    )
    if local_at is not None:
        session.add(
            Reminder(
                homework_id=hw.id,
                user_id=user.id,
                local_at=local_at,
                fire_at=local_to_utc(local_at, user.timezone),
            )
        )
    await session.flush()


async def recompute_for_user(session: AsyncSession, user: User) -> None:
    """После смены часового пояса напоминания срабатывают по новым местным часам."""
    rows = await session.scalars(
        select(Reminder).where(Reminder.user_id == user.id, Reminder.sent_at.is_(None))
    )
    for r in rows:
        r.fire_at = local_to_utc(r.local_at, user.timezone)


@dataclass
class DueReminder:
    reminder: Reminder
    homework: Homework
    user: User


async def due_reminders(session: AsyncSession, now: datetime, limit: int = 200) -> list[DueReminder]:
    rows = await session.execute(
        select(Reminder, Homework, User)
        .join(Homework, Homework.id == Reminder.homework_id)
        .join(User, User.id == Reminder.user_id)
        .where(Reminder.sent_at.is_(None), Reminder.fire_at <= now)
        .order_by(Reminder.fire_at)
        .limit(limit)
    )
    return [DueReminder(r, h, u) for r, h, u in rows.all()]


def should_skip(item: DueReminder) -> bool:
    """Не напоминаем о выполненном и тем, кто выключил напоминания или заблокировал бота."""
    return item.homework.done or not item.user.reminders_enabled or item.user.blocked


def _digest_slot(user: User, now_l: datetime) -> datetime:
    """Последнее наступившее время сводки: сегодняшнее или, если оно ещё впереди, вчерашнее."""
    target = datetime.combine(now_l.date(), user.digest_time)
    return target - timedelta(days=1) if target > now_l else target


def digest_due(user: User, now: datetime) -> bool:
    """Пора ли отправить ежедневную сводку (без учёта того, есть ли задания).
    Опоздавшая сводка (бот был выключен) приходит в течение 3 часов, даже после полуночи."""
    if not user.digest_enabled or user.blocked:
        return False
    now_l = local_now(user.timezone, now)
    slot = _digest_slot(user, now_l)
    return user.last_digest_date != slot.date() and now_l < slot + DIGEST_GRACE


def mark_digest(user: User, now: datetime) -> None:
    user.last_digest_date = _digest_slot(user, local_now(user.timezone, now)).date()


def reminder_presets(
    due_date: date | None, due_time: time | None, now_local: datetime
) -> list[tuple[str, datetime]]:
    """Готовые варианты напоминания для кнопок: только те, что ещё впереди и раньше срока сдачи."""
    if due_date is None:
        return []
    deadline = datetime.combine(due_date, due_time) if due_time else None
    options = [
        ("Накануне в 20:00", datetime.combine(due_date - timedelta(days=1), time(20, 0))),
        ("В день сдачи в 8:00", datetime.combine(due_date, time(8, 0))),
    ]
    return [
        (label, dt) for label, dt in options
        if dt > now_local and (deadline is None or dt < deadline)
    ]
