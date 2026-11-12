"""Предметы студента. Все запросы ограничены своим студентом (user_id)."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Homework, Lesson, Subject


class SubjectError(ValueError):
    pass


def clean_name(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise SubjectError("Название предмета не может быть пустым.")
    if len(name) > 100:
        raise SubjectError("Слишком длинное название, уложитесь в 100 символов.")
    return name


async def list_subjects(session: AsyncSession, user_id: int) -> list[Subject]:
    rows = await session.scalars(
        select(Subject).where(Subject.user_id == user_id).order_by(func.lower(Subject.name))
    )
    return list(rows)


async def get_subject(session: AsyncSession, user_id: int, subject_id: int) -> Subject | None:
    return await session.scalar(
        select(Subject).where(Subject.id == subject_id, Subject.user_id == user_id)
    )


async def find_or_create(session: AsyncSession, user_id: int, name: str) -> Subject:
    name = clean_name(name)
    for s in await list_subjects(session, user_id):
        if s.name.lower() == name.lower():
            return s
    subject = Subject(user_id=user_id, name=name)
    session.add(subject)
    await session.flush()
    return subject


async def rename(session: AsyncSession, subject: Subject, name: str) -> None:
    name = clean_name(name)
    for s in await list_subjects(session, subject.user_id):
        if s.id != subject.id and s.name.lower() == name.lower():
            raise SubjectError("Предмет с таким названием уже есть.")
    subject.name = name


async def usage(session: AsyncSession, subject: Subject) -> tuple[int, int]:
    """Сколько пар и заданий удалится вместе с предметом."""
    lessons = await session.scalar(select(func.count()).where(Lesson.subject_id == subject.id))
    hw = await session.scalar(select(func.count()).where(Homework.subject_id == subject.id))
    return lessons or 0, hw or 0


async def delete_subject(session: AsyncSession, subject: Subject) -> None:
    # Пары, задания и всё связанное удаляются каскадом в базе.
    await session.delete(subject)
    await session.flush()
