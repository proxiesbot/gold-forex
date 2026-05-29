from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from app.models import SavedStrategyDetail, SavedStrategySummary, StrategyFile, StrategySaveRequest
from app.repositories.database import db


class StrategyStore:
    """Persist saved strategies in SQLite."""

    def list_strategies(self) -> list[SavedStrategySummary]:
        with db.connect() as conn:
            rows = conn.execute("SELECT payload FROM strategies ORDER BY updated_at DESC").fetchall()
        items: list[SavedStrategySummary] = []
        for row in rows:
            try:
                item = StrategyFile.model_validate(json.loads(row["payload"]))
                items.append(self._to_summary(item))
            except Exception:
                continue
        return items

    def save_strategy(self, payload: StrategySaveRequest) -> SavedStrategySummary:
        slug = self._slugify(payload.name)
        now = datetime.now(timezone.utc)
        created_at = now
        with db.connect() as conn:
            row = conn.execute("SELECT payload FROM strategies WHERE id = ?", (slug,)).fetchone()
            if row:
                try:
                    created_at = StrategyFile.model_validate(json.loads(row["payload"])).created_at
                except Exception:
                    created_at = now
            file_obj = StrategyFile(
                id=slug,
                name=payload.name.strip(),
                created_at=created_at,
                updated_at=now,
                strategy=payload.strategy,
            )
            conn.execute(
                """
                INSERT INTO strategies (id, name, created_at, updated_at, payload)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    updated_at = excluded.updated_at,
                    payload = excluded.payload
                """,
                (slug, file_obj.name, file_obj.created_at.isoformat(), file_obj.updated_at.isoformat(), file_obj.model_dump_json(indent=2)),
            )
        return self._to_summary(file_obj)

    def get_strategy(self, strategy_id: str) -> SavedStrategyDetail:
        with db.connect() as conn:
            row = conn.execute("SELECT payload FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
        if not row:
            raise FileNotFoundError(strategy_id)
        item = StrategyFile.model_validate(json.loads(row["payload"]))
        return SavedStrategyDetail(
            id=item.id,
            name=item.name,
            created_at=item.created_at.isoformat(),
            updated_at=item.updated_at.isoformat(),
            strategy=item.strategy,
        )

    def _to_summary(self, file_obj: StrategyFile) -> SavedStrategySummary:
        return SavedStrategySummary(
            id=file_obj.id,
            name=file_obj.name,
            created_at=file_obj.created_at.isoformat(),
            updated_at=file_obj.updated_at.isoformat(),
            primary_timeframe=file_obj.strategy.primary_timeframe,
            execution_timeframe=file_obj.strategy.execution_timeframe,
            direction=file_obj.strategy.direction,
            steps=[node.type for node in file_obj.strategy.sequence],
        )

    def _slugify(self, value: str) -> str:
        value = value.strip().lower()
        value = re.sub(r"[^a-z0-9\u0600-\u06FF]+", "-", value)
        value = value.strip("-")
        return value[:80] or "strategy"
