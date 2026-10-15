import os
from functools import cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session


@cache
def get_engine() -> Engine:
    return create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)


def get_session() -> Session:
    return Session(get_engine())
