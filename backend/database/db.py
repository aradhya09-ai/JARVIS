"""SQLite access layer.

A connection is opened per operation so the API and background tasks can use
the database from any thread without sharing cursor state.

``SCHEMA`` is the frozen v1 layout that existing installs were built with, so
it must never be edited to add anything: an already-installed database has
those tables and ``CREATE TABLE IF NOT EXISTS`` would silently do nothing.
Everything added since then goes into ``MIGRATIONS`` keyed by the schema
version it introduces, which is stored in ``PRAGMA user_version``.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id                  TEXT PRIMARY KEY,
    text                TEXT NOT NULL,
    source              TEXT NOT NULL DEFAULT 'text',
    intent              TEXT,
    tool                TEXT,
    args_json           TEXT,
    risk                TEXT,
    status              TEXT NOT NULL,
    result_json         TEXT,
    error               TEXT,
    planner             TEXT,
    created_at          TEXT NOT NULL,
    started_at          TEXT,
    finished_at         TEXT,
    confirmation_token  TEXT,
    cancel_requested    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);

CREATE TABLE IF NOT EXISTS task_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    at         TEXT NOT NULL,
    level      TEXT NOT NULL,
    message    TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    task_id    TEXT
);

CREATE TABLE IF NOT EXISTS devices (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    token_hash  TEXT NOT NULL,
    paired_at   TEXT NOT NULL,
    last_seen   TEXT,
    revoked     INTEGER NOT NULL DEFAULT 0,
    info_json   TEXT
);

CREATE TABLE IF NOT EXISTS app_shortcuts (
    name       TEXT PRIMARY KEY,
    spec_json  TEXT NOT NULL,
    builtin    INTEGER NOT NULL DEFAULT 0,
    added_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    source     TEXT NOT NULL,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    read       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS integration_config (
    provider     TEXT PRIMARY KEY,
    config_json  TEXT NOT NULL,
    enabled      INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    risk        TEXT NOT NULL,
    detail_json TEXT,
    outcome     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_log(at);

CREATE TABLE IF NOT EXISTS memory (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'preference',
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS phone_commands (
    id           TEXT PRIMARY KEY,
    device_id    TEXT,
    action       TEXT NOT NULL,
    args_json    TEXT,
    status       TEXT NOT NULL,
    result_json  TEXT,
    created_at   TEXT NOT NULL,
    finished_at  TEXT
);
"""


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return column in existing


def _add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Idempotent ALTER, because a migration may run against a database that a
    previous partial upgrade already touched."""
    if not _has_column(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


# v2: personal knowledge, timetable, recent artifacts, editable site aliases.
def _migrate_v2(conn: sqlite3.Connection) -> None:
    _add_column(conn, "memory", "category", "TEXT NOT NULL DEFAULT 'preference'")
    _add_column(conn, "memory", "source", "TEXT NOT NULL DEFAULT 'user'")
    _add_column(conn, "memory", "verified", "INTEGER NOT NULL DEFAULT 1")
    _add_column(conn, "memory", "as_of", "TEXT")
    _add_column(conn, "memory", "note", "TEXT")
    _add_column(conn, "memory", "created_at", "TEXT")
    conn.execute("UPDATE memory SET created_at = updated_at WHERE created_at IS NULL")

    conn.executescript(
        """
CREATE TABLE IF NOT EXISTS schedule_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    title       TEXT NOT NULL,
    course      TEXT,
    location    TEXT,
    start_at    TEXT,
    end_at      TEXT,
    due_at      TEXT,
    weekdays    TEXT,
    start_time  TEXT,
    end_time    TEXT,
    status      TEXT NOT NULL DEFAULT 'scheduled',
    source      TEXT NOT NULL DEFAULT 'manual',
    verified    INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_schedule_due ON schedule_items(due_at);
CREATE INDEX IF NOT EXISTS idx_schedule_kind ON schedule_items(kind, status);

CREATE TABLE IF NOT EXISTS artifacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    path        TEXT NOT NULL,
    label       TEXT,
    task_id     TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_kind ON artifacts(kind, created_at);

CREATE TABLE IF NOT EXISTS site_aliases (
    name      TEXT PRIMARY KEY,
    url       TEXT NOT NULL,
    aliases   TEXT,
    builtin   INTEGER NOT NULL DEFAULT 0,
    added_at  TEXT NOT NULL
);
"""
    )


# v3: GATE / AI-ML coaching stores and reminders.
def _migrate_v3(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
CREATE TABLE IF NOT EXISTS study_topics (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    track          TEXT NOT NULL,
    subject        TEXT NOT NULL,
    topic          TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'not_started',
    confidence     INTEGER NOT NULL DEFAULT 0,
    last_studied   TEXT,
    next_revision  TEXT,
    notes          TEXT,
    source         TEXT NOT NULL DEFAULT 'manual',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    UNIQUE (track, subject, topic)
);
CREATE INDEX IF NOT EXISTS idx_topics_revision ON study_topics(next_revision);

CREATE TABLE IF NOT EXISTS study_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    day         TEXT NOT NULL,
    minutes     INTEGER NOT NULL DEFAULT 0,
    track       TEXT,
    subject     TEXT,
    topic       TEXT,
    note        TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_day ON study_sessions(day);

CREATE TABLE IF NOT EXISTS practice_questions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    track          TEXT NOT NULL DEFAULT 'gate',
    subject        TEXT NOT NULL,
    topic          TEXT NOT NULL,
    question       TEXT NOT NULL,
    answer         TEXT NOT NULL,
    explanation    TEXT,
    source         TEXT NOT NULL DEFAULT 'user',
    official_pyq   INTEGER NOT NULL DEFAULT 0,
    difficulty     TEXT,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS practice_attempts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id  INTEGER NOT NULL,
    asked_at     TEXT NOT NULL,
    revealed_at  TEXT,
    user_answer  TEXT,
    verdict      TEXT,
    correct      INTEGER,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_question ON practice_attempts(question_id);

CREATE TABLE IF NOT EXISTS mistakes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    subject            TEXT,
    topic              TEXT,
    question           TEXT,
    what_went_wrong    TEXT,
    fix                TEXT,
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT NOT NULL,
    due_at      TEXT NOT NULL,
    fired       INTEGER NOT NULL DEFAULT 0,
    fired_at    TEXT,
    cancelled   INTEGER NOT NULL DEFAULT 0,
    speak       INTEGER NOT NULL DEFAULT 1,
    source      TEXT NOT NULL DEFAULT 'manual',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(fired, cancelled, due_at);
"""
    )


# v4: ordered tool steps, so "open Chrome and search for X" is one task.
def _migrate_v4(conn: sqlite3.Connection) -> None:
    _add_column(conn, "tasks", "steps_json", "TEXT")


# v5: the GATE/AI-ML coach and the class timetable were removed as products.
# Migrations 2-4 stay exactly as they ran, so an installed database still
# upgrades through them and then drops the stores here. Every table below was
# verified empty before the drop was written; `reminders` shares the v3 script
# and is deliberately left alone.
def _migrate_v5(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
DROP TABLE IF EXISTS schedule_items;
DROP TABLE IF EXISTS study_topics;
DROP TABLE IF EXISTS study_sessions;
DROP TABLE IF EXISTS practice_questions;
DROP TABLE IF EXISTS practice_attempts;
DROP TABLE IF EXISTS mistakes;
"""
    )


MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (2, _migrate_v2),
    (3, _migrate_v3),
    (4, _migrate_v4),
    (5, _migrate_v5),
]


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            before = self.schema_version()
            if 0 < before < SCHEMA_VERSION:
                self._backup_before_upgrade()
            with self._connection() as conn:
                conn.executescript(SCHEMA)
                version = int(conn.execute("PRAGMA user_version").fetchone()[0])
                for target, migrate in MIGRATIONS:
                    if version >= target:
                        continue
                    migrate(conn)
                    conn.execute(f"PRAGMA user_version = {target}")
                    version = target
                conn.commit()

    def _backup_before_upgrade(self) -> None:
        """A copy of the pre-upgrade database, so a bad migration is reversible.

        Only touched when the schema actually advances, so normal restarts do
        not leave files behind.
        """
        try:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            target = self.path.with_name(f"{self.path.stem}.pre-migration-{stamp}{self.path.suffix}")
            if target.exists():
                return
            with sqlite3.connect(self.path) as source, sqlite3.connect(target) as dest:
                source.backup(dest)
        except sqlite3.Error:
            logger.warning("Could not back up %s before migrating", self.path, exc_info=True)

    def schema_version(self) -> int:
        with self._connection() as conn:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._lock, self._connection() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self._lock, self._connection() as conn:
            cursor = conn.execute(sql, params)
            conn.commit()
            return int(cursor.lastrowid or 0)

    def execute_many(self, sql: str, params: Sequence[Sequence[Any]]) -> None:
        with self._lock, self._connection() as conn:
            conn.executemany(sql, params)
            conn.commit()

    def healthy(self) -> bool:
        try:
            self.query_one("SELECT 1 AS ok")
            return True
        except sqlite3.Error:
            return False
