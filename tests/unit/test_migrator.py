"""Migration runner regression tests."""

import sqlite3

import aiosqlite
import pytest

from refindery.adapters.metadata import migrator


async def test_failed_migration_rolls_back_partial_state(tmp_path, monkeypatch):
    async with aiosqlite.connect(tmp_path / "migrations.db") as conn:
        monkeypatch.setattr(
            migrator,
            "load_migrations",
            lambda: [
                (1, "0001_ok.sql", "CREATE TABLE ok_table (id TEXT);"),
                (
                    2,
                    "0002_bad.sql",
                    "CREATE TABLE partial_table (id TEXT); "
                    "INSERT INTO missing_table VALUES ('x');",
                ),
            ],
        )

        with pytest.raises(sqlite3.OperationalError):
            await migrator.migrate(conn)

        rows = await (
            await conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ).fetchall()
        names = {row[0] for row in rows}
        assert "ok_table" in names
        assert "partial_table" not in names

        versions = await (
            await conn.execute("SELECT version FROM schema_migrations ORDER BY version")
        ).fetchall()
        assert [row[0] for row in versions] == [1]
