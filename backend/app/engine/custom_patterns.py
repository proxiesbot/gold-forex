"""Custom Pattern Learning System.

Allows users to define new patterns by describing them in natural language (Arabic/English).
The system stores pattern definitions and applies them to chart data for detection.

Example user flow:
1. User: "QM هو لما السعر يعمل قاع جديد، بعدين قمة جديدة، بعدين قاع أعلى من الأول"
2. System stores this as a custom pattern with structural rules
3. User: "حددلي كل QM على فريم 30m"
4. System uses stored rules to detect the pattern
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class PatternRule:
    """A single rule in a custom pattern definition."""
    step: int
    swing_type: str  # "high", "low", "higher_high", "lower_low", "higher_low", "lower_high"
    relation_to_prev: str  # "higher", "lower", "any", "break_above", "break_below"
    description: str  # User's original description for this step
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class CustomPatternDef:
    """A user-defined pattern stored for reuse."""
    pattern_id: str
    name: str
    description: str  # User's full natural language description
    direction: str  # "bullish", "bearish", "either"
    rules: list[PatternRule]
    zone_rule: str  # Which swing point forms the zone: "last", "first", "middle"
    zone_side: str  # "low_of_candle", "high_of_candle", "body"
    created_at: str
    updated_at: str
    examples_count: int = 0
    tags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ─── Pattern Description Parser ───────────────────────────────────────────

# Keywords that map to structural concepts
SWING_KEYWORDS = {
    # Arabic
    "قاع": "low", "قاع جديد": "lower_low", "قاع أعلى": "higher_low",
    "قمة": "high", "قمة جديدة": "higher_high", "قمة أقل": "lower_high",
    "هاي": "high", "لو": "low",
    "higher low": "higher_low", "lower high": "lower_high",
    "higher high": "higher_high", "lower low": "lower_low",
    "HL": "higher_low", "LH": "lower_high", "HH": "higher_high", "LL": "lower_low",
    "hl": "higher_low", "lh": "lower_high", "hh": "higher_high", "ll": "lower_low",
    # English
    "swing high": "high", "swing low": "low",
    "new high": "higher_high", "new low": "lower_low",
    "higher low": "higher_low", "lower high": "lower_high",
}

DIRECTION_KEYWORDS = {
    "صعودي": "bullish", "شراء": "bullish", "bullish": "bullish", "buy": "bullish", "long": "bullish",
    "هبوطي": "bearish", "بيع": "bearish", "bearish": "bearish", "sell": "bearish", "short": "bearish",
}

QM_PATTERN_KEYWORDS = ("qm", "quasimodo", "كيو ام", "كوازيمودو")


def parse_pattern_description(text: str, name: str = "") -> CustomPatternDef:
    """
    Parse a natural language description into a CustomPatternDef.

    Supports Arabic and English descriptions of structural patterns.
    Falls back to QM rules if QM keywords are detected.
    """
    lowered = text.lower()

    # Detect if this is a QM pattern
    is_qm = any(kw in lowered for kw in QM_PATTERN_KEYWORDS)

    # Detect direction
    direction = "either"
    for kw, d in DIRECTION_KEYWORDS.items():
        if kw in lowered:
            direction = d
            break

    # Extract structural steps from text
    rules = _extract_rules_from_text(text, direction)

    # If no rules extracted but it's QM, use default QM rules
    if not rules and is_qm:
        rules = _default_qm_rules(direction)

    # If still no rules, create a generic 3-point pattern
    if not rules:
        rules = _generic_swing_rules(text, direction)

    # Determine zone rule
    zone_rule = "last"  # The last swing point forms the zone (most common for QM)
    zone_side = "low_of_candle" if direction == "bullish" else "high_of_candle"

    pattern_id = f"custom_{name.lower().replace(' ', '_') or 'pattern'}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    return CustomPatternDef(
        pattern_id=pattern_id,
        name=name or _auto_name(rules, direction),
        description=text,
        direction=direction,
        rules=rules,
        zone_rule=zone_rule,
        zone_side=zone_side,
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        tags=["custom", direction] + (["qm"] if is_qm else []),
        notes=[f"Auto-parsed from: {text[:100]}"],
    )


def _extract_rules_from_text(text: str, direction: str) -> list[PatternRule]:
    """Extract pattern rules from text by finding swing keywords in order."""
    rules: list[PatternRule] = []
    lowered = text.lower()

    # Try to find ordered swing references
    # Sort keywords by length (longer first to avoid partial matches)
    sorted_keywords = sorted(SWING_KEYWORDS.items(), key=lambda x: len(x[0]), reverse=True)

    found_swings: list[tuple[int, str, str]] = []  # (position, keyword, swing_type)
    used_positions: set[int] = set()

    for keyword, swing_type in sorted_keywords:
        pos = lowered.find(keyword)
        while pos != -1:
            # Check if this position overlaps with already found keywords
            overlaps = any(pos >= up and pos < up + 3 for up in used_positions)
            if not overlaps:
                found_swings.append((pos, keyword, swing_type))
                used_positions.add(pos)
            pos = lowered.find(keyword, pos + len(keyword))

    # Sort by position in text
    found_swings.sort(key=lambda x: x[0])

    # Convert to rules
    prev_type = None
    for step_idx, (_, keyword, swing_type) in enumerate(found_swings):
        relation = "any"
        if prev_type:
            if "higher" in swing_type:
                relation = "higher"
            elif "lower" in swing_type:
                relation = "lower"

        rules.append(PatternRule(
            step=step_idx + 1,
            swing_type=swing_type,
            relation_to_prev=relation,
            description=keyword,
        ))
        prev_type = swing_type

    return rules


def _default_qm_rules(direction: str) -> list[PatternRule]:
    """Default QM pattern rules."""
    if direction == "bearish":
        return [
            PatternRule(step=1, swing_type="higher_high", relation_to_prev="any", description="قمة جديدة (HH)"),
            PatternRule(step=2, swing_type="lower_low", relation_to_prev="lower", description="قاع جديد (LL)"),
            PatternRule(step=3, swing_type="lower_high", relation_to_prev="higher", description="قمة أقل (LH) - هنا الزون"),
        ]
    # Default bullish
    return [
        PatternRule(step=1, swing_type="lower_low", relation_to_prev="any", description="قاع جديد (LL)"),
        PatternRule(step=2, swing_type="higher_high", relation_to_prev="higher", description="قمة جديدة (HH)"),
        PatternRule(step=3, swing_type="higher_low", relation_to_prev="lower", description="قاع أعلى (HL) - هنا الزون"),
    ]


def _generic_swing_rules(text: str, direction: str) -> list[PatternRule]:
    """Fallback: create generic rules from any 3 swings."""
    if direction == "bullish":
        return [
            PatternRule(step=1, swing_type="low", relation_to_prev="any", description="First swing low"),
            PatternRule(step=2, swing_type="high", relation_to_prev="higher", description="Swing high"),
            PatternRule(step=3, swing_type="higher_low", relation_to_prev="lower", description="Higher low (zone)"),
        ]
    elif direction == "bearish":
        return [
            PatternRule(step=1, swing_type="high", relation_to_prev="any", description="First swing high"),
            PatternRule(step=2, swing_type="low", relation_to_prev="lower", description="Swing low"),
            PatternRule(step=3, swing_type="lower_high", relation_to_prev="higher", description="Lower high (zone)"),
        ]
    return [
        PatternRule(step=1, swing_type="low", relation_to_prev="any", description="Swing point 1"),
        PatternRule(step=2, swing_type="high", relation_to_prev="any", description="Swing point 2"),
        PatternRule(step=3, swing_type="low", relation_to_prev="any", description="Swing point 3 (zone)"),
    ]


def _auto_name(rules: list[PatternRule], direction: str) -> str:
    """Generate a name from rules."""
    types = [r.swing_type.upper().replace("_", " ") for r in rules[:3]]
    return f"{direction.title()} {' → '.join(types)}"


# ─── Pattern Store (persistence) ─────────────────────────────────────────

class CustomPatternStore:
    """Store and retrieve custom pattern definitions."""

    def __init__(self, data_dir: Path | None = None):
        self._dir = data_dir or (settings.data_dir / "patterns")
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, pattern: CustomPatternDef) -> CustomPatternDef:
        """Save or update a custom pattern."""
        pattern.updated_at = datetime.now(timezone.utc).isoformat()
        path = self._dir / f"{pattern.pattern_id}.json"
        payload = asdict(pattern)
        # Convert PatternRule dataclasses to dicts
        payload["rules"] = [asdict(r) for r in pattern.rules]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("custom_pattern_saved id=%s name=%s", pattern.pattern_id, pattern.name)
        return pattern

    def get(self, pattern_id: str) -> CustomPatternDef | None:
        """Get a pattern by ID."""
        path = self._dir / f"{pattern_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        data["rules"] = [PatternRule(**r) for r in data["rules"]]
        return CustomPatternDef(**data)

    def list_all(self) -> list[dict[str, Any]]:
        """List all stored patterns (summary only)."""
        results = []
        for path in sorted(self._dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                results.append({
                    "pattern_id": data["pattern_id"],
                    "name": data["name"],
                    "direction": data["direction"],
                    "description": data["description"][:120],
                    "rules_count": len(data.get("rules", [])),
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

    def find_by_name(self, name: str) -> CustomPatternDef | None:
        """Find a pattern by name (case-insensitive partial match)."""
        lowered = name.lower()
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if lowered in data.get("name", "").lower() or lowered in data.get("description", "").lower():
                    data["rules"] = [PatternRule(**r) for r in data["rules"]]
                    return CustomPatternDef(**data)
            except Exception:
                continue
        return None
