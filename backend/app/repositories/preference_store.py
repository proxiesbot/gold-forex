from __future__ import annotations

import json
from datetime import datetime, timezone

from app.models import PreferenceProfile, StrategySpec
from app.repositories.database import db


class PreferenceStore:
    """Persist user preferences in SQLite."""

    def get(self) -> PreferenceProfile:
        with db.connect() as conn:
            row = conn.execute("SELECT payload FROM preferences WHERE id = 1").fetchone()
        if not row:
            profile = PreferenceProfile()
            self._save(profile)
            return profile
        try:
            return PreferenceProfile.model_validate(json.loads(row["payload"]))
        except Exception:
            profile = PreferenceProfile()
            self._save(profile)
            return profile

    def reset(self) -> PreferenceProfile:
        profile = PreferenceProfile()
        self._save(profile)
        return profile

    def learn_from_strategy(self, strategy: StrategySpec, note: str | None = None) -> PreferenceProfile:
        profile = self.get()
        profile.default_symbol = strategy.symbol
        profile.default_market_label = strategy.market_label
        profile.default_primary_timeframe = strategy.primary_timeframe
        profile.default_execution_timeframe = strategy.execution_timeframe
        profile.preferred_direction = strategy.direction
        sessions = strategy.constraints.get("sessions") or []
        if isinstance(sessions, list):
            profile.preferred_sessions = [str(x) for x in sessions]
        step_types = [node.type for node in strategy.sequence]
        for node_type in ("order_block", "bos", "mss"):
            if node_type in step_types:
                profile.preferred_source_type = node_type
                break
        profile.prefer_zone_from_wick = "zone_from_wick" in step_types
        profile.prefer_retest = "retest_zone" in step_types
        profile.prefer_sweep = "liquidity_sweep" in step_types
        profile.prefer_fvg = "fvg" in step_types
        if strategy.constraints.get("zone_mode") in {"full_wick", "body_to_wick", "half_wick"}:
            profile.preferred_zone_mode = strategy.constraints["zone_mode"]
        if note:
            profile.notes.append(note)
            profile.notes = profile.notes[-20:]
        self._save(profile)
        return profile

    def learn_from_correction_text(self, correction_text: str) -> PreferenceProfile:
        profile = self.get()
        snippet = correction_text.strip()
        if snippet:
            profile.correction_examples.append(snippet)
            profile.correction_examples = profile.correction_examples[-20:]
        lowered = snippet.lower()
        if "half wick" in lowered or "نص الذيل" in lowered or "50%" in lowered:
            profile.preferred_zone_mode = "half_wick"
        elif "full wick" in lowered or "كامل الذيل" in lowered:
            profile.preferred_zone_mode = "full_wick"
        elif "body" in lowered or "الجسم" in lowered:
            profile.preferred_zone_mode = "body_to_wick"
        self._save(profile)
        return profile

    def _save(self, profile: PreferenceProfile) -> None:
        profile.updated_at = datetime.now(timezone.utc).isoformat()
        payload = profile.model_dump_json(indent=2)
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO preferences (id, payload, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at
                """,
                (payload, profile.updated_at),
            )
