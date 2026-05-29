from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from app.models import ResearchRunFile, ResearchRunRequest, ResearchRunResponse, ResearchRunSummary
from app.repositories.database import db


class ResearchRunStore:
    """Persist research runs in SQLite."""

    def save_run(self, request: ResearchRunRequest, response: ResearchRunResponse) -> ResearchRunSummary:
        run_id = self._build_id(response.strategy_summary)
        response.run_id = run_id
        file_obj = ResearchRunFile(
            id=run_id,
            created_at=datetime.now(timezone.utc),
            request=request,
            response=response,
        )
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (id, created_at, summary, total_qualified, avg_quality_score, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    file_obj.created_at.isoformat(),
                    file_obj.response.strategy_summary,
                    file_obj.response.total_qualified,
                    float(file_obj.response.stats.get("avg_quality_score", 0.0)),
                    file_obj.model_dump_json(indent=2),
                ),
            )
        return self._to_summary(file_obj)

    def list_runs(self) -> list[ResearchRunSummary]:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT id, created_at, summary, total_qualified, avg_quality_score FROM runs ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
        return [
            ResearchRunSummary(
                id=row["id"],
                created_at=row["created_at"],
                summary=row["summary"],
                total_qualified=int(row["total_qualified"]),
                avg_quality_score=float(row["avg_quality_score"] or 0.0),
            )
            for row in rows
        ]

    def get_run(self, run_id: str) -> ResearchRunFile:
        with db.connect() as conn:
            row = conn.execute("SELECT payload FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise FileNotFoundError(run_id)
        return ResearchRunFile.model_validate(json.loads(row["payload"]))

    def count_runs(self) -> int:
        with db.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM runs").fetchone()
        return int(row["count"] if row else 0)

    def _to_summary(self, file_obj: ResearchRunFile) -> ResearchRunSummary:
        return ResearchRunSummary(
            id=file_obj.id,
            created_at=file_obj.created_at.isoformat(),
            summary=file_obj.response.strategy_summary,
            total_qualified=file_obj.response.total_qualified,
            avg_quality_score=float(file_obj.response.stats.get("avg_quality_score", 0.0)),
        )

    def _build_id(self, seed: str) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        slug = re.sub(r"[^a-z0-9]+", "-", seed.lower())[:28].strip("-") or "run"
        return f"{stamp}-{slug}"
