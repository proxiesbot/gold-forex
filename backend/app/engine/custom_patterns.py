"""Custom Pattern Store - persistence for user-taught patterns.

The user teaches patterns by:
1. Marking swing points on the chart (annotations with price + timestamp)
2. Writing a description in Arabic/English
3. Optionally adding more examples over time

This module handles storage and retrieval of learned patterns.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.engine.pattern_detector import LearnedPattern, PatternExample, SwingPoint

logger = logging.getLogger(__name__)



class PatternStore:
    """Store and retrieve user-taught pattern definitions."""

    def __init__(self, data_dir: Path | None = None):
        self._dir = data_dir or (settings.data_dir / "patterns")
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, pattern: LearnedPattern) -> LearnedPattern:
        """Save or update a learned pattern."""
        pattern.updated_at = datetime.now(timezone.utc).isoformat()
        path = self._dir / f"{pattern.pattern_id}.json"
        payload = asdict(pattern)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("pattern_saved id=%s name=%s examples=%d", pattern.pattern_id, pattern.name, len(pattern.examples))
        return pattern

    def get(self, pattern_id: str) -> LearnedPattern | None:
        """Get a pattern by ID."""
        path = self._dir / f"{pattern_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return self._deserialize(data)

    def find_by_name(self, name: str) -> LearnedPattern | None:
        """Find a pattern by name (case-insensitive partial match)."""
        lowered = name.lower()
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if lowered in data.get("name", "").lower() or lowered in data.get("description", "").lower():
                    return self._deserialize(data)
            except Exception:
                continue
        return None

    def list_all(self) -> list[dict[str, Any]]:
        """List all stored patterns (summary)."""
        results = []
        for path in sorted(self._dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                results.append({
                    "pattern_id": data["pattern_id"],
                    "name": data["name"],
                    "direction": data["direction"],
                    "description": data["description"][:150],
                    "examples_count": len(data.get("examples", [])),
                    "swing_count": len(data.get("swing_sequence", [])),
                    "created_at": data.get("created_at"),
                    "tags": data.get("tags", []),
                })
            except Exception:
                continue
        return results

    def delete(self, pattern_id: str) -> bool:
        """Delete a pattern."""
        path = self._dir / f"{pattern_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def add_example(self, pattern_id: str, example: PatternExample) -> LearnedPattern | None:
        """Add another example to an existing pattern (user teaches more)."""
        pattern = self.get(pattern_id)
        if pattern is None:
            return None
        pattern.examples.append(example)
        # Re-calculate tolerance from multiple examples if available
        if len(pattern.examples) >= 2:
            pattern.tolerance_pct = self._calc_tolerance(pattern.examples)
        return self.save(pattern)

    def _calc_tolerance(self, examples: list[PatternExample]) -> float:
        """Calculate matching tolerance from multiple examples."""
        if len(examples) < 2:
            return 0.15
        # Compare proportional differences between examples
        ranges = []
        for ex in examples:
            prices = [sp.price for sp in ex.swing_points]
            if prices:
                ranges.append(max(prices) - min(prices))
        if not ranges or max(ranges) == 0:
            return 0.15
        # Higher variance in examples = more tolerant matching
        avg_range = sum(ranges) / len(ranges)
        variance = sum((r - avg_range) ** 2 for r in ranges) / len(ranges)
        normalized = min(0.4, max(0.08, (variance / avg_range) * 0.5)) if avg_range > 0 else 0.15
        return round(normalized, 3)

    def _deserialize(self, data: dict) -> LearnedPattern:
        """Convert stored JSON back to LearnedPattern."""
        examples = []
        for ex_data in data.get("examples", []):
            swing_points = [SwingPoint(**sp) for sp in ex_data.get("swing_points", [])]
            examples.append(PatternExample(
                example_id=ex_data.get("example_id", ""),
                swing_points=swing_points,
                zone_low=ex_data.get("zone_low", 0),
                zone_high=ex_data.get("zone_high", 0),
                zone_label=ex_data.get("zone_label", ""),
                direction=ex_data.get("direction", "either"),
                timeframe=ex_data.get("timeframe", "30m"),
                symbol=ex_data.get("symbol", "XAUUSD"),
                notes=ex_data.get("notes", ""),
            ))

        return LearnedPattern(
            pattern_id=data["pattern_id"],
            name=data["name"],
            description=data["description"],
            direction=data["direction"],
            swing_sequence=data.get("swing_sequence", []),
            zone_position=data.get("zone_position", "last"),
            zone_from=data.get("zone_from", "swing_low"),
            examples=examples,
            tolerance_pct=data.get("tolerance_pct", 0.15),
            min_swings=data.get("min_swings", 3),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            tags=data.get("tags", []),
        )
