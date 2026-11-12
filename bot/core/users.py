"""Пользователи, заявки на подключение вузов, удаление данных."""

import re
from datetime import date

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core import reminders
from bot.core.timeutil import default_week1
from bot.models import (
    Attachment,
    Homework,
    Lesson,
    LessonCancel,
    Reminder,
    Subject,
    UniversityRequest,
    User,
)


async def get_or_create_user(
    session: AsyncSession, platform: str, external_id: str, default_tz: str = "Europe/Moscow"
) -> User:
    user = await session.scalar(
        select(User).where(User.platform == platform, User.external_id == external_id)
    )
    if user is None:
        user = User(platform=platform, external_id=external_id, timezone=default_tz)
        session.add(user)
        await session.flush()
    elif user.blocked:
        user.blocked = False
    return user


def week1_of(user: User, today: date) -> date:
    return user.week1_start or default_week1(today)


async def set_timezone(session: AsyncSession, user: User, tz_name: str) -> None:
    user.timezone = tz_name
    await reminders.recompute_for_user(session, user)


def _name_key(name: str) -> str:
    key = name.lower().replace("ё", "е")
    key = re.sub(r"[«»\"'.,()]", " ", key)
    return re.sub(r"\s+", " ", key).strip()


async def add_university_request(session: AsyncSession, user: User, name: str) -> bool:
    """Заявка на подключение вуза. Повторная заявка того же студента на тот же вуз не считается."""
    name = name.strip()[:200]
    key = _name_key(name)
    if not key:
        return False
    exists = await session.scalar(
        select(UniversityRequest.id).where(
            UniversityRequest.user_id == user.id, UniversityRequest.name_key == key
        )
    )
    if exists:
        return False
    session.add(UniversityRequest(user_id=user.id, name=name, name_key=key))
    user.university_name = name
    return True


async def request_counts(session: AsyncSession, limit: int = 20) -> list[tuple[str, int]]:
    """Сколько заявок на каждый вуз. Название показываем так, как его написали в первой заявке."""
    rows = (
        await session.execute(
            select(func.min(UniversityRequest.id), func.count())
            .group_by(UniversityRequest.name_key)
            .order_by(func.count().desc(), func.min(UniversityRequest.id))
            .limit(limit)
        )
    ).all()
    names = dict(
        (await session.execute(
            select(UniversityRequest.id, UniversityRequest.name).where(
                UniversityRequest.id.in_([first_id for first_id, _ in rows])
            )
        )).all()
    )
    return [(names[first_id], count) for first_id, count in rows]


async def delete_user_data(session: AsyncSession, user: User) -> None:
    """Полное удаление данных студента. Заявки на вузы остаются только как обезличенный счётчик."""
    uid = user.id
    hw_ids = select(Homework.id).where(Homework.user_id == uid)
    lesson_ids = select(Lesson.id).where(Lesson.user_id == uid)
    await session.execute(delete(Reminder).where(Reminder.user_id == uid))
    await session.execute(delete(Attachment).where(Attachment.homework_id.in_(hw_ids)))
    await session.execute(delete(Homework).where(Homework.user_id == uid))
    await session.execute(delete(LessonCancel).where(LessonCancel.lesson_id.in_(lesson_ids)))
    await session.execute(delete(Lesson).where(Lesson.user_id == uid))
    await session.execute(delete(Subject).where(Subject.user_id == uid))
    await session.execute(
        update(UniversityRequest).where(UniversityRequest.user_id == uid).values(user_id=None)
    )
    await session.execute(delete(User).where(User.id == uid))
