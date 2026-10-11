"""SQLite 引擎与会话工厂。

表结构设计见 docs/architecture/03-data-model.md；
ORM 模型与 Alembic 迁移在迭代 001 引入。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def make_engine(database_url: str) -> Engine:
    """根据配置创建引擎；SQLite 允许跨线程使用（调度/转写线程）。"""
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """事务作用域：正常提交，异常回滚。"""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(engine: Engine) -> None:
    """初始化表结构。迭代 001 改为 Alembic 迁移驱动。"""
    raise NotImplementedError("ORM 模型与 Alembic 迁移将在迭代 001 实现")
