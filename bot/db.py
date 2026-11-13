from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


def make_engine(url: str, **kwargs) -> AsyncEngine:
    # Значения из запросов (то, что вводят студенты) не попадают в тексты ошибок и журнал.
    kwargs.setdefault("hide_parameters", True)
    engine = create_async_engine(url, **kwargs)
    if engine.dialect.name == "sqlite":
        # В SQLite связи между таблицами (каскадное удаление) включаются отдельно.
        @event.listens_for(engine.sync_engine, "connect")
        def _fk_on(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
