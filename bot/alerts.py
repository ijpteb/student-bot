"""Сообщения владельцу бота об ошибках — не чаще раза в час на один вид ошибки."""

import logging
import time

from bot.messenger import Notifier

log = logging.getLogger(__name__)


class AdminAlerts:
    def __init__(self, notifier: Notifier, period_s: float = 3600):
        self.notifier = notifier
        self.period_s = period_s
        self._last: dict[str, float] = {}

    async def report(self, kind: str, text: str) -> None:
        now = time.monotonic()
        prev = self._last.get(kind)
        if prev is not None and now - prev < self.period_s:
            return
        self._last[kind] = now  # занимаем место сразу: одновременные ошибки не шлют по сообщению каждая
        try:
            await self.notifier.notify_admin(f"⚠️ {text}")
        except Exception as e:
            # Не дошло (например, нет связи) — не считаем отправленным, попробуем при следующей ошибке.
            log.warning("Не удалось сообщить владельцу об ошибке: %s", type(e).__name__)
            if self._last.get(kind) == now:
                if prev is None:
                    self._last.pop(kind, None)
                else:
                    self._last[kind] = prev
