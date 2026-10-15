import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache

from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.orm import Session


@cache
def get_engine() -> Engine:
    return create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)


def get_session() -> Session:
    return Session(get_engine())


@contextmanager
def transaction(bind: Engine | Connection) -> Iterator[Session]:
    """A short transaction that is saved as soon as the block ends.

    If bind is a connection already inside a transaction (as in tests), this nests inside it
    instead, so the outer transaction still decides whether anything is kept.
    """
    with Session(bind, join_transaction_mode="create_savepoint") as session, session.begin():
        yield session
