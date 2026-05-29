from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from app.models import (
    AnnotatedExample,
    StrategyTemplateDetail,
    StrategyTemplateExampleSaveRequest,
    StrategyTemplateFile,
    StrategyTemplateSaveRequest,
    StrategyTemplateSummary,
)
from app.repositories.database import db


class StrategyTemplateStore:
    """Persist named strategy templates and chart examples in SQLite."""

    def list_templates(self) -> list[StrategyTemplateSummary]:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM strategy_templates ORDER BY updated_at DESC, created_at DESC LIMIT 200"
            ).fetchall()
        return [self._to_summary(StrategyTemplateFile.model_validate(json.loads(row["payload"]))) for row in rows]

    def save_template(self, payload: StrategyTemplateSaveRequest) -> StrategyTemplateSummary:
        slug = self._slugify(payload.name)
        now = datetime.now(timezone.utc)
        with db.connect() as conn:
            row = conn.execute("SELECT payload FROM strategy_templates WHERE id = ?", (slug,)).fetchone()
            created_at = now
            examples: list[AnnotatedExample] = list(payload.examples)
            if row:
                existing = StrategyTemplateFile.model_validate(json.loads(row["payload"]))
                created_at = existing.created_at
                if not examples:
                    examples = existing.examples
            item = StrategyTemplateFile(
                id=slug,
                name=payload.name.strip(),
                description=(payload.description or "").strip(),
                created_at=created_at,
                updated_at=now,
                strategy=payload.strategy,
                rules=payload.rules,
                examples=examples,
            )
            conn.execute(
                """
                INSERT INTO strategy_templates (id, name, created_at, updated_at, payload)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    updated_at = excluded.updated_at,
                    payload = excluded.payload
                """,
                (slug, item.name, item.created_at.isoformat(), item.updated_at.isoformat(), item.model_dump_json(indent=2)),
            )
        return self._to_summary(item)

    def get_template(self, template_id: str) -> StrategyTemplateDetail:
        with db.connect() as conn:
            row = conn.execute("SELECT payload FROM strategy_templates WHERE id = ?", (template_id,)).fetchone()
        if not row:
            raise FileNotFoundError(template_id)
        item = StrategyTemplateFile.model_validate(json.loads(row["payload"]))
        return StrategyTemplateDetail(
            id=item.id,
            name=item.name,
            description=item.description,
            created_at=item.created_at.isoformat(),
            updated_at=item.updated_at.isoformat(),
            strategy=item.strategy,
            rules=item.rules,
            examples=item.examples,
        )

    def add_example(self, template_id: str, payload: StrategyTemplateExampleSaveRequest) -> StrategyTemplateDetail:
        with db.connect() as conn:
            row = conn.execute("SELECT payload FROM strategy_templates WHERE id = ?", (template_id,)).fetchone()
            if not row:
                raise FileNotFoundError(template_id)
            item = StrategyTemplateFile.model_validate(json.loads(row["payload"]))
            next_index = len(item.examples) + 1
            example = AnnotatedExample(
                example_id=f"{template_id}-example-{next_index}",
                symbol=payload.symbol,
                primary_timeframe=payload.primary_timeframe,
                execution_timeframe=payload.execution_timeframe,
                title=payload.title,
                note=payload.note,
                annotations=payload.annotations,
            )
            item.examples.append(example)
            item.updated_at = datetime.now(timezone.utc)
            conn.execute(
                "UPDATE strategy_templates SET updated_at = ?, payload = ? WHERE id = ?",
                (item.updated_at.isoformat(), item.model_dump_json(indent=2), template_id),
            )
        return StrategyTemplateDetail(
            id=item.id,
            name=item.name,
            description=item.description,
            created_at=item.created_at.isoformat(),
            updated_at=item.updated_at.isoformat(),
            strategy=item.strategy,
            rules=item.rules,
            examples=item.examples,
        )

    def _to_summary(self, item: StrategyTemplateFile) -> StrategyTemplateSummary:
        return StrategyTemplateSummary(
            id=item.id,
            name=item.name,
            description=item.description,
            created_at=item.created_at.isoformat(),
            updated_at=item.updated_at.isoformat(),
            school=item.strategy.school,
            strategy_key=item.strategy.strategy_key,
            primary_timeframe=item.strategy.primary_timeframe,
            execution_timeframe=item.strategy.execution_timeframe,
            examples_count=len(item.examples),
        )

    def _slugify(self, value: str) -> str:
        value = value.strip().lower()
        value = re.sub(r"[^a-z0-9\u0600-\u06FF]+", "-", value)
        return value.strip("-")[:80] or "template"
