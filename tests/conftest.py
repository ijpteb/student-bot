import os

import pytest
from sqlalchemy import text
from sqlalchemy.pool import StaticPool

from bot.db import make_engine, make_sessionmaker
from bot.models import Base

# По умолчанию тесты идут на SQLite в памяти. Чтобы проверить на PostgreSQL:
# TEST_DATABASE_URL=postgresql+asyncpg://user:pass@host/db pytest
TEST_DB = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
async def engine():
    if TEST_DB:
        eng = make_engine(TEST_DB)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    else:
        eng = make_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    yield eng
    if TEST_DB:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest.fixture
def sessionmaker(engine):
    return make_sessionmaker(engine)


@pytest.fixture
async def session(sessionmaker):
    async with sessionmaker() as s:
        yield s
