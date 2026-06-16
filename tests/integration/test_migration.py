"""S98.1 — migration up/down/up for bot_llm_rag_chunk (real PostgreSQL).

Loads the migration module directly and runs it through alembic's Operations
context, isolated from the conftest ``create_all`` (which already built the
table — so we drop it first to exercise a clean upgrade). Validates the chain
anchors on the always-present core root and the revision id is ≤ 32 chars.
"""
import importlib.util
import os

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

# Opens its OWN connection + transaction and rolls back itself, so it must run
# WITHOUT the autouse rolled-back-session isolation.
pytestmark = pytest.mark.no_db_isolation

_TABLE = "bot_llm_rag_chunk"


def _load_migration():
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "migrations",
        "versions",
        "20260616_1000_bot_llm_rag_chunk.py",
    )
    spec = importlib.util.spec_from_file_location("bot_llm_rag_chunk_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_migration()


def _has_table(connection) -> bool:
    return _TABLE in set(inspect(connection).get_table_names())


@pytest.fixture
def migration_connection(app):
    from vbwd.extensions import db

    connection = db.engine.connect()
    transaction = connection.begin()
    operations = Operations(MigrationContext.configure(connection))
    if inspect(connection).has_table(_TABLE):
        operations.drop_table(_TABLE)
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


@pytest.mark.integration
def test_revision_anchors_on_core_root_and_id_is_short():
    assert migration.revision == "20260616_1000_bot_llm_rag"
    assert migration.down_revision == "vbwd_001"
    assert len(migration.revision) <= 32


@pytest.mark.integration
def test_up_down_up(migration_connection):
    assert not _has_table(migration_connection)
    context = MigrationContext.configure(migration_connection)
    with Operations.context(context):
        migration.upgrade()
    assert _has_table(migration_connection)
    with Operations.context(context):
        migration.downgrade()
    assert not _has_table(migration_connection)
    with Operations.context(context):
        migration.upgrade()
    assert _has_table(migration_connection)
