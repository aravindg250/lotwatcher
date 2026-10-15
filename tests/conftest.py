import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url


def _test_database_url() -> URL:
    url = make_url(os.environ["TEST_DATABASE_URL"]).set(drivername="postgresql+psycopg")
    # Safety: this setup drops the database, so never let it point at a real one
    if not url.database or not url.database.endswith("_test"):
        raise RuntimeError(
            f"TEST_DATABASE_URL must name a database ending in _test, got {url.database!r}"
        )
    return url


def _recreate_database(url: URL) -> None:
    # Connect to the built-in "postgres" database, since you can't drop the one you're connected to
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()


@pytest.fixture(scope="session")
def alembic_cfg():
    url = _test_database_url()
    _recreate_database(url)
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
    command.upgrade(cfg, "head")
    return cfg


@pytest.fixture(scope="session")
def engine(alembic_cfg):
    engine = create_engine(_test_database_url())
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine):
    """A connection inside a transaction that is rolled back after each test."""
    with engine.connect() as conn:
        trans = conn.begin()
        yield conn
        trans.rollback()
