import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from lotwatcher.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# The tables Alembic compares against the database when drafting a migration
target_metadata = Base.metadata


def database_url() -> str:
    # Tests pass their own URL in; everything else uses DATABASE_URL from the environment
    return config.get_main_option("sqlalchemy.url") or os.environ["DATABASE_URL"]


def run_migrations() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
