"""Фоновый планировщик: раз в полминуты отправляет напоминания и ежедневные сводки."""

import asyncio
import logging
import time as _time
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.core import homework as hw_core
from bot.core import reminders, render
from bot.core.subjects import list_subjects
from bot.core.timeutil import local_now, now_utc
from bot.messenger import Notifier, RecipientUnavailable
from bot.models import User

log = logging.getLogger(__name__)


class AdminAlerts:
    """Сообщает владельцу об ошибках, но не чаще раза в час на один вид ошибки."""

    def __init__(self, notifier: Notifier, period_s: float = 3600):
        self.notifier = notifier
        self.period_s = period_s
        self._last: dict[str, float] = {}

    async def report(self, kind: str, text: str) -> None:
        now = _time.monotonic()
        if now - self._last.get(kind, -1e18) < self.period_s:
            return
        self._last[kind] = now
        try:
            await self.notifier.notify_admin(f"⚠️ {text}")
        except Exception:
            log.exception("Не удалось сообщить владельцу об ошибке")


async def send_reminders(sessionmaker: async_sessionmaker, notifier: Notifier, alerts: AdminAlerts, now: datetime) -> int:
    sent = 0
    async with sessionmaker() as session:
        for item in await reminders.due_reminders(session, now):
            r, hw, user = item.reminder, item.homework, item.user
            if reminders.should_skip(item):
                r.sent_at = now
                await session.commit()
                continue
            subjects = {s.id: s.name for s in await list_subjects(session, user.id)}
            try:
                await notifier.send_text(user, render.reminder_text(hw, subjects.get(hw.subject_id, "?")), homework_id=hw.id)
                r.sent_at = now
                sent += 1
            except RecipientUnavailable:
                user.blocked = True
                r.sent_at = now
            except Exception as e:  # сбой связи — попробуем в следующий раз
                r.attempts += 1
                log.warning("Напоминание %s не отправлено: %s", r.id, type(e).__name__)
                if r.attempts >= reminders.MAX_ATTEMPTS:
                    r.sent_at = now
                    await alerts.report("reminder", f"Напоминание не удалось отправить {r.attempts} раз: {type(e).__name__}")
            await session.commit()
    return sent


async def send_digests(sessionmaker: async_sessionmaker, notifier: Notifier, alerts: AdminAlerts, now: datetime) -> int:
    sent = 0
    async with sessionmaker() as session:
        users = list(await session.scalars(select(User).where(User.digest_enabled.is_(True), User.blocked.is_(False))))
        for user in users:
            if not reminders.digest_due(user, now):
                continue
            items = await hw_core.list_open(session, user.id)
            if items:
                subjects = {s.id: s.name for s in await list_subjects(session, user.id)}
                text = render.digest_text(
                    [(h, subjects.get(h.subject_id, "?")) for h in items], local_now(user.timezone, now).date()
                )
                try:
                    await notifier.send_text(user, text)
                    sent += 1
                except RecipientUnavailable:
                    user.blocked = True
                except Exception as e:
                    log.warning("Сводка не отправлена: %s", type(e).__name__)
                    await alerts.report("digest", f"Сводка не отправлена: {type(e).__name__}")
                    continue  # ничего не меняли — попробуем на следующем круге
            reminders.mark_digest(user, now)
            await session.commit()
    return sent


async def tick(sessionmaker: async_sessionmaker, notifier: Notifier, alerts: AdminAlerts, now: datetime | None = None) -> None:
    now = now or now_utc()
    await send_reminders(sessionmaker, notifier, alerts, now)
    await send_digests(sessionmaker, notifier, alerts, now)


async def run(sessionmaker: async_sessionmaker, notifier: Notifier, interval_s: float = 30) -> None:
    alerts = AdminAlerts(notifier)
    while True:
        try:
            await tick(sessionmaker, notifier, alerts)
        except Exception as e:
            log.exception("Ошибка планировщика")
            await alerts.report("scheduler", f"Ошибка планировщика: {type(e).__name__}")
        await asyncio.sleep(interval_s)
