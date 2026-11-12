"""Домашние задания. Каждый запрос ограничен своим студентом (user_id) — чужое задание не найти."""

from datetime import date, time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.timeutil import now_utc
from bot.models import Attachment, Homework, Subject

MAX_TEXT = 3000


class HomeworkError(ValueError):
    pass


def clean_text(text: str) -> str:
    text = text.strip()
    if len(text) > MAX_TEXT:
        raise HomeworkError(f"Слишком длинный текст, уложитесь в {MAX_TEXT} символов.")
    return text


async def add(
    session: AsyncSession,
    user_id: int,
    subject_id: int,
    text: str,
    due_date: date | None = None,
    due_time: time | None = None,
    attachments: list[tuple[str, str]] | None = None,
    platform: str = "telegram",
) -> Homework:
    subject = await session.scalar(
        select(Subject).where(Subject.id == subject_id, Subject.user_id == user_id)
    )
    if subject is None:
        raise HomeworkError("Предмет не найден.")
    hw = Homework(
        user_id=user_id, subject_id=subject_id, text=clean_text(text), due_date=due_date, due_time=due_time
    )
    session.add(hw)
    await session.flush()
    for kind, file_id in attachments or []:
        session.add(Attachment(homework_id=hw.id, platform=platform, kind=kind, file_id=file_id))
    await session.flush()
    return hw


async def get(session: AsyncSession, user_id: int, hw_id: int) -> Homework | None:
    return await session.scalar(select(Homework).where(Homework.id == hw_id, Homework.user_id == user_id))


def _sort_key(hw: Homework):
    # Сначала со сроком (раньше — выше), потом без срока.
    return (hw.due_date is None, hw.due_date or date.max, hw.due_time or time.max, hw.id)


async def list_open(session: AsyncSession, user_id: int, subject_id: int | None = None) -> list[Homework]:
    q = select(Homework).where(Homework.user_id == user_id, Homework.done.is_(False))
    if subject_id is not None:
        q = q.where(Homework.subject_id == subject_id)
    return sorted(await session.scalars(q), key=_sort_key)


async def list_done(session: AsyncSession, user_id: int, limit: int = 20) -> list[Homework]:
    rows = await session.scalars(
        select(Homework)
        .where(Homework.user_id == user_id, Homework.done.is_(True))
        .order_by(Homework.done_at.desc())
        .limit(limit)
    )
    return list(rows)


async def open_counts_by_subject(session: AsyncSession, user_id: int) -> dict[int, int]:
    counts: dict[int, int] = {}
    for hw in await list_open(session, user_id):
        counts[hw.subject_id] = counts.get(hw.subject_id, 0) + 1
    return counts


async def attachments(session: AsyncSession, hw: Homework) -> list[Attachment]:
    return list(await session.scalars(select(Attachment).where(Attachment.homework_id == hw.id).order_by(Attachment.id)))


async def set_done(hw: Homework, done: bool) -> None:
    hw.done = done
    hw.done_at = now_utc() if done else None


async def delete(session: AsyncSession, hw: Homework) -> None:
    await session.delete(hw)
    await session.flush()
