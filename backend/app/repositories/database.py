from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import settings

SCHEMA_VERSION = 3


class Database:
    """Simple SQLite helper for application storage with lightweight migrations."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            self._ensure_base_tables(conn)
            self._run_migrations(conn)

    def _ensure_base_tables(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS preferences (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategies (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                summary TEXT NOT NULL,
                total_qualified INTEGER NOT NULL,
                avg_quality_score REAL NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_cache (
                cache_key TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                periods INTEGER NOT NULL,
                cached_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_templates (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_strategies_updated_at ON strategies(updated_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_market_cache_expires_at ON market_cache(expires_at)")

    def _run_migrations(self, conn: sqlite3.Connection) -> None:
        current = self.schema_version(conn)
        if current < 1:
            self._set_schema_version(conn, 1)
            current = 1
        if current < 2:
            conn.execute("DELETE FROM market_cache WHERE payload IS NULL OR payload = ''")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_market_cache_symbol_tf ON market_cache(symbol, timeframe)")
            self._set_schema_version(conn, 2)
            current = 2
        if current < 3:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_templates (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_templates_updated_at ON strategy_templates(updated_at DESC)")
            self._set_schema_version(conn, 3)

    def schema_version(self, conn: sqlite3.Connection | None = None) -> int:
        owns_conn = conn is None
        if conn is None:
            conn = sqlite3.connect(self.path)
            conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()
            return int(row[0]) if row else 0
        finally:
            if owns_conn and conn is not None:
                conn.close()

    def _set_schema_version(self, conn: sqlite3.Connection, version: int) -> None:
        conn.execute(
            """
            INSERT INTO app_meta(key, value) VALUES('schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(version),),
        )

    def stats(self) -> dict[str, int]:
        with self.connect() as conn:
            return {
                "schema_version": self.schema_version(conn),
                "runs": int(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]),
                "strategies": int(conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]),
                "cache_entries": int(conn.execute("SELECT COUNT(*) FROM market_cache").fetchone()[0]),
                "strategy_templates": int(conn.execute("SELECT COUNT(*) FROM strategy_templates").fetchone()[0]),
            }


db = Database(settings.database_path)
