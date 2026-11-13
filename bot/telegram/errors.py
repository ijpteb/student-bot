"""Какие ошибки означают «не удалось связаться с Telegram» (а не ошибку в самом боте)."""

import asyncio

from aiogram.exceptions import ClientDecodeError, TelegramNetworkError, TelegramServerError

_TRANSPORT: tuple[type[BaseException], ...] = (
    TelegramNetworkError,  # нет сети, обрыв, таймаут
    TelegramServerError,  # Telegram ответил 5xx
    ClientDecodeError,  # вместо ответа Telegram пришла, например, HTML-страница «502»
    OSError,
    asyncio.TimeoutError,
)
# Ошибки прокси (TELEGRAM_PROXY). В aiohttp_socks и python_socks — свои, разные классы ошибок,
# и они не наследуют OSError, поэтому перечисляем их явно (ставятся вместе с aiogram[proxy]).
try:
    import aiohttp_socks

    _TRANSPORT += tuple(
        getattr(aiohttp_socks, name)
        for name in ("ProxyError", "ProxyConnectionError", "ProxyTimeoutError", "SocksError", "SocksConnectionError")
        if hasattr(aiohttp_socks, name)
    )
except ImportError:  # pragma: no cover
    pass
try:
    import python_socks

    _TRANSPORT += (python_socks.ProxyError, python_socks.ProxyConnectionError, python_socks.ProxyTimeoutError)
except ImportError:  # pragma: no cover
    pass


def is_transport_error(exc: BaseException) -> bool:
    return isinstance(exc, _TRANSPORT)
