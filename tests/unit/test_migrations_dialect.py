"""Guard: migration DDL stays dialect-neutral (portable to Postgres)."""

import re

from refindery.adapters.metadata.migrator import load_migrations

FORBIDDEN = (
    "AUTOINCREMENT",
    "WITHOUT ROWID",
    "strftime",
    "json_extract",
    "PRAGMA",
    "datetime(",
)


def test_migrations_exist():
    migrations = load_migrations()
    assert migrations
    assert migrations[0][0] == 1


def test_no_sqlite_isms_in_migrations():
    for _version, name, sql in load_migrations():
        for token in FORBIDDEN:
            assert token.lower() not in sql.lower(), f"{name} contains {token}"


def test_only_portable_types():
    allowed = {"TEXT", "INTEGER", "REAL", "BLOB", "BOOLEAN", "TIMESTAMP"}
    constraints = {"PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "CONSTRAINT"}
    column_type = re.compile(r"^\s{2}(\w+)\s+([A-Z]+)", re.MULTILINE)
    for _version, name, sql in load_migrations():
        for match in column_type.finditer(sql):
            if match.group(1).upper() in constraints:
                continue
            assert match.group(2) in allowed, f"{name}: type {match.group(2)}"
