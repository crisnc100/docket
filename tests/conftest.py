import pytest


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter and reporter.stats.get("skipped"):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if any(item.iter_markers(name="skip")) or any(item.iter_markers(name="skipif")):
            raise pytest.UsageError(f"Skipped tests are forbidden: {item.nodeid}")


@pytest.fixture()
def postgres_engine():
    import uuid

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    from docket.settings import Settings

    schema = "test_" + uuid.uuid4().hex
    admin = create_engine(Settings().database_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        Settings().database_url, connect_args={"options": f"-csearch_path={schema}"}
    )
    try:
        config = Config("alembic.ini")
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
