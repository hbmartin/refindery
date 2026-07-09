"""Numbered-script migration runner.

Migration files live in ``refindery.adapters.metadata.migrations`` as
``NNNN_name.sql``; each unapplied file runs in one transaction and is
recorded in ``schema_migrations``.
"""

import logging
import sqlite3
from importlib import resources

import aiosqlite

logger = logging.getLogger(__name__)

_MIGRATIONS_PACKAGE = "refindery.adapters.metadata.migrations"


def load_migrations() -> list[tuple[int, str, str]]:
    """Return (version, name, sql) for every bundled migration, ordered."""
    migrations: list[tuple[int, str, str]] = []
    for entry in resources.files(_MIGRATIONS_PACKAGE).iterdir():
        if not entry.name.endswith(".sql"):
            continue
        version = int(entry.name.split("_", maxsplit=1)[0])
        migrations.append((version, entry.name, entry.read_text(encoding="utf-8")))
    return sorted(migrations)


def _split_sql_statements(sql: str) -> list[str]:
    """Split a migration script into complete SQLite statements."""
    statements: list[str] = []
    current: list[str] = []
    for char in sql:
        current.append(char)
        candidate = "".join(current).strip()
        if candidate and sqlite3.complete_statement(candidate):
            statements.append(candidate)
            current = []
    trailing = "".join(current).strip()
    if trailing:
        statements.append(trailing)
    return statements


async def migrate(conn: aiosqlite.Connection) -> int:
    """Apply all unapplied migrations; return how many ran."""
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
        "applied_at TIMESTAMP NOT NULL)"
    )
    await conn.commit()
    cursor = await conn.execute("SELECT version FROM schema_migrations")
    applied = {row[0] for row in await cursor.fetchall()}

    ran = 0
    for version, name, sql in load_migrations():
        if version in applied:
            continue
        logger.info("applying migration %s", name)
        await conn.execute("BEGIN")
        try:
            for statement in _split_sql_statements(sql):
                await conn.execute(statement)
            await conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) "
                "VALUES (?, ?, datetime('now'))",
                (version, name),
            )
        except Exception:
            await conn.rollback()
            raise
        else:
            await conn.commit()
        ran += 1
    return ran
