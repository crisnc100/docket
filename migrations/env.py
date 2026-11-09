from alembic import context
from sqlalchemy import create_engine

from docket.db import Base
from docket.settings import Settings


def migrate(connection):
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        url=Settings().database_url, target_metadata=Base.metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()
elif connection := context.config.attributes.get("connection"):
    migrate(connection)
else:
    with create_engine(Settings().database_url).connect() as connection:
        migrate(connection)
