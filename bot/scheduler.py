"""Фоновый планировщик: раз в полминуты отправляет напоминания и ежедневные сводки."""

import asyncio
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from bot.alerts import AdminAlerts
from bot.core import homework as hw_core
from bot.core import reminders, render
from bot.core.subjects import list_subjects
from bot.core.timeutil import local_now, now_utc
from bot.messenger import MessageRejected, Notifier, RecipientUnavailable, TemporaryFailure
from bot.models import User

log = logging.getLogger(__name__)

__all__ = ["AdminAlerts", "run", "tick"]


async def _commit(session: AsyncSession) -> bool:
    """Сохранить. False — запись удалили, пока шла отправка (студент удалил задание или все данные)."""
    try:
        await session.commit()
        return True
    except StaleDataError:
        await session.rollback()
        log.info("Запись удалена во время отправки — пропускаю")
        return False


async def send_reminders(sessionmaker: async_sessionmaker, notifier: Notifier, alerts: AdminAlerts, now: datetime) -> int:
    sent = 0
    async with sessionmaker() as session:
        for item in await reminders.due_reminders(session, now):
            r, hw, user = item.reminder, item.homework, item.user
            if reminders.should_skip(item):
                r.sent_at = now
                if not await _commit(session):
                    break
                continue
            subjects = {s.id: s.name for s in await list_subjects(session, user.id)}
            try:
                await notifier.send_text(user, render.reminder_text(hw, subjects.get(hw.subject_id, "?")), homework_id=hw.id)
                r.sent_at = now
                sent += 1
            except RecipientUnavailable:
                user.blocked = True
                r.sent_at = now
            except TemporaryFailure as e:
                # Нет связи с Telegram: напоминание остаётся в очереди и уйдёт, когда связь вернётся.
                log.warning("Нет связи с мессенджером (%s) — напоминания отправлю позже", e)
                break
            except MessageRejected as e:
                r.sent_at = now
                log.warning("Напоминание %s отклонено мессенджером: %s", r.id, e)
                await alerts.report("reminder_rejected", f"Telegram отклонил напоминание: {e}")
            except Exception as e:  # неожиданная ошибка — несколько попыток, потом сдаёмся
                r.attempts += 1
                log.warning("Напоминание %s не отправлено: %s", r.id, type(e).__name__)
                if r.attempts >= reminders.MAX_ATTEMPTS:
                    r.sent_at = now
                    await alerts.report("reminder", f"Напоминание не удалось отправить {r.attempts} раз: {type(e).__name__}")
            if not await _commit(session):
                break
    return sent


async def _digest_for_user(sessionmaker: async_sessionmaker, notifier: Notifier, alerts: AdminAlerts, uid: int, now: datetime) -> bool:
    """Сводка одному студенту. У каждого своя сессия: ошибка у одного не мешает остальным."""
    async with sessionmaker() as session:
        user = await session.get(User, uid)
        if user is None or not reminders.digest_due(user, now):
            return False
        items = await hw_core.list_open(session, user.id)
        sent = False
        if items:
            subjects = {s.id: s.name for s in await list_subjects(session, user.id)}
            text = render.digest_text([(h, subjects.get(h.subject_id, "?")) for h in items], local_now(user.timezone, now))
            try:
                await notifier.send_text(user, text)
                sent = True
            except RecipientUnavailable:
                user.blocked = True
            except MessageRejected as e:
                # Повтор не поможет — за этот день сводку пропускаем и сообщаем владельцу.
                await alerts.report("digest_rejected", f"Telegram отклонил сводку: {e}")
        reminders.mark_digest(user, now)
        await _commit(session)
        return sent


async def send_digests(sessionmaker: async_sessionmaker, notifier: Notifier, alerts: AdminAlerts, now: datetime) -> int:
    async with sessionmaker() as session:
        ids = list(await session.scalars(select(User.id).where(User.digest_enabled.is_(True), User.blocked.is_(False))))
    sent = 0
    for uid in ids:
        try:
            sent += await _digest_for_user(sessionmaker, notifier, alerts, uid, now)
        except TemporaryFailure as e:
            log.warning("Нет связи с мессенджером (%s) — сводки отправлю позже", e)
            break  # повторим на следующем круге, пока не вышли 3 часа
        except Exception as e:
            log.warning("Сводка не отправлена: %s", type(e).__name__)
            await alerts.report("digest", f"Сводка не отправлена: {type(e).__name__}")
    return sent


async def tick(sessionmaker: async_sessionmaker, notifier: Notifier, alerts: AdminAlerts, now: datetime | None = None) -> None:
    now = now or now_utc()
    await send_reminders(sessionmaker, notifier, alerts, now)
    await send_digests(sessionmaker, notifier, alerts, now)


async def run(sessionmaker: async_sessionmaker, notifier: Notifier, alerts: AdminAlerts | None = None, interval_s: float = 30) -> None:
    alerts = alerts or AdminAlerts(notifier)
    while True:
        try:
            await tick(sessionmaker, notifier, alerts)
        except Exception as e:
            log.exception("Ошибка планировщика")
            await alerts.report("scheduler", f"Ошибка планировщика: {type(e).__name__}")
        await asyncio.sleep(interval_s)
