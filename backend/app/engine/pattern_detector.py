"""Learned Pattern Matcher - the user teaches, the bot learns and searches.

The user defines patterns by:
1. Marking swing points on a chart (annotations with price + timestamp)
2. Writing a text description in Arabic/English
3. The system extracts structural rules from the examples

Then the bot searches historical data for all occurrences matching
the learned structural sequence.

NO hardcoded pattern definitions - everything is learned from user input.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from hashlib import md5
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)



@dataclass
class SwingPoint:
    """A single swing point marked by the user or detected automatically."""
    type: str  # "high" or "low"
    price: float
    timestamp: str
    label: str = ""  # user label like "Left Shoulder", "Head", etc.
    idx: int = 0


@dataclass
class PatternExample:
    """One example of the pattern as taught by the user on the chart."""
    example_id: str
    swing_points: list[SwingPoint]
    zone_low: float
    zone_high: float
    zone_label: str = ""  # e.g. "entry zone", "الزون"
    direction: str = "either"
    timeframe: str = "30m"
    symbol: str = "XAUUSD"
    notes: str = ""


@dataclass
class LearnedPattern:
    """A pattern definition learned from user examples."""
    pattern_id: str
    name: str
    description: str  # user's text explanation
    direction: str  # "bullish", "bearish", "either"
    # Structural rules extracted from examples
    swing_sequence: list[dict[str, str]]  # [{"type": "high", "relation": "any"}, ...]
    zone_position: str  # which swing forms the zone: "last", "second_last", "custom"
    zone_from: str  # "swing_low", "swing_high", "body_low", "body_high"
    # Teaching data
    examples: list[PatternExample] = field(default_factory=list)
    # Matching parameters (tuned from examples)
    tolerance_pct: float = 0.15  # how close swings need to match proportionally
    min_swings: int = 3
    created_at: str = ""
    updated_at: str = ""
    tags: list[str] = field(default_factory=list)



@dataclass
class MatchedPattern:
    """A pattern occurrence found by the matcher."""
    match_id: str
    pattern_id: str
    direction: str
    timestamp: str  # when the pattern completed
    zone_low: float
    zone_high: float
    zone_mid: float
    swing_points: list[dict[str, Any]]
    freshness: str  # "fresh", "tested", "broken"
    touches_after: int
    similarity_score: float  # 0-100, how close to the taught example
    session_label: str
    explanation: list[str]


@dataclass
class LTFReaction:
    """What happened on LTF when price reached the pattern zone."""
    match_id: str
    reaction_type: str
    reaction_time: str
    entry_price: float | None
    stop_loss: float | None
    take_profit: float | None
    risk_points: float | None
    reward_points: float | None
    rr_ratio: float | None
    move_after: float
    adverse_after: float
    outcome: str  # "win", "loss", "breakeven"
    ltf_pattern: str
    explanation: list[str]


@dataclass
class PatternScanResult:
    """Complete result of a pattern scan."""
    total_levels_found: int
    fresh_levels: int
    tested_levels: int
    broken_levels: int
    levels: list[MatchedPattern]
    ltf_reactions: list[LTFReaction]
    stats: dict[str, Any]
    summary: str
    highlights: list[str]



class LearnedPatternMatcher:
    """
    The core engine: learns from user-taught examples and finds matches.

    Flow:
    1. User teaches: marks swing points on chart + writes description
    2. System extracts structural rules (sequence of highs/lows + relations)
    3. System searches chart data for all matching sequences
    4. Optionally checks LTF reaction at each matched zone
    """

    def __init__(self, swing_lookback: int = 5):
        self.swing_lookback = swing_lookback

    def learn_from_example(self, annotations: list[dict], description: str, direction: str, name: str = "") -> LearnedPattern:
        """
        Learn a pattern from user-provided chart annotations.

        annotations: list of {type, price, timestamp, label} - swing points marked by user
        description: text explanation in Arabic/English
        direction: "bullish", "bearish", "either"
        """
        # Extract structural sequence from annotations
        swing_sequence = []
        prev_price = None
        for ann in annotations:
            swing_type = ann.get("type", "").lower()
            if swing_type not in ("high", "low"):
                continue
            relation = "any"
            if prev_price is not None:
                relation = "higher" if ann["price"] > prev_price else "lower"
            swing_sequence.append({
                "type": swing_type,
                "relation": relation,
                "label": ann.get("label", ""),
            })
            prev_price = ann["price"]

        # Determine zone position from annotations
        zone_ann = next((a for a in annotations if a.get("is_zone") or "zone" in (a.get("label", "")).lower()), None)
        if zone_ann:
            zone_position = "custom"
            zone_from = "swing_low" if zone_ann.get("type") == "low" else "swing_high"
        else:
            # Default: last swing point is the zone
            zone_position = "last"
            zone_from = "swing_low" if direction == "bullish" else "swing_high"

        pattern_id = f"learned_{name.lower().replace(' ', '_') or 'pattern'}_{pd.Timestamp.now('UTC').strftime('%Y%m%d%H%M%S')}"

        # Build example from annotations
        example = PatternExample(
            example_id=f"ex_{pattern_id[:8]}",
            swing_points=[SwingPoint(type=a["type"], price=a["price"], timestamp=a.get("timestamp", ""), label=a.get("label", "")) for a in annotations if a.get("type") in ("high", "low")],
            zone_low=zone_ann["price"] if zone_ann and zone_ann["type"] == "low" else (annotations[-1]["price"] if annotations else 0),
            zone_high=zone_ann.get("zone_high", zone_ann["price"] * 1.002) if zone_ann else 0,
            direction=direction,
            notes=description,
        )

        return LearnedPattern(
            pattern_id=pattern_id,
            name=name or f"Custom {direction} pattern",
            description=description,
            direction=direction,
            swing_sequence=swing_sequence,
            zone_position=zone_position,
            zone_from=zone_from,
            examples=[example],
            min_swings=len(swing_sequence),
            created_at=pd.Timestamp.now("UTC").isoformat(),
            updated_at=pd.Timestamp.now("UTC").isoformat(),
            tags=["learned", direction],
        )


    def find_matches(
        self,
        pattern: LearnedPattern,
        df: pd.DataFrame,
        max_matches: int = 100,
    ) -> list[MatchedPattern]:
        """
        Search chart data for all occurrences matching the learned pattern.
        """
        swings = self._find_swing_points(df)
        if len(swings) < pattern.min_swings:
            return []

        target_seq = pattern.swing_sequence
        matches: list[MatchedPattern] = []

        # Slide through detected swings looking for matching sequences
        for start_idx in range(len(swings) - len(target_seq) + 1):
            candidate = swings[start_idx: start_idx + len(target_seq)]
            score = self._score_match(candidate, target_seq, pattern)
            if score >= 50.0:  # Minimum similarity threshold
                # Build zone from matched swings
                zone_low, zone_high = self._extract_zone(candidate, pattern)
                last_swing = candidate[-1]
                match_id = md5(f"{pattern.pattern_id}-{last_swing['timestamp']}-{zone_low}".encode()).hexdigest()[:10]

                # Check freshness
                freshness, touches, first_touch = self._check_freshness(
                    df, last_swing["idx"], zone_low, zone_high, pattern.direction
                )

                matches.append(MatchedPattern(
                    match_id=match_id,
                    pattern_id=pattern.pattern_id,
                    direction=pattern.direction,
                    timestamp=last_swing["timestamp"],
                    zone_low=round(zone_low, 3),
                    zone_high=round(zone_high, 3),
                    zone_mid=round((zone_low + zone_high) / 2, 3),
                    swing_points=[{"type": s["type"], "price": s["price"], "timestamp": s["timestamp"]} for s in candidate],
                    freshness=freshness,
                    touches_after=touches,
                    similarity_score=round(score, 1),
                    session_label=self._session_label(pd.to_datetime(last_swing["timestamp"], utc=True)),
                    explanation=self._build_match_explanation(candidate, target_seq, score, pattern),
                ))

            if len(matches) >= max_matches:
                break

        matches.sort(key=lambda m: (m.similarity_score, m.timestamp), reverse=True)
        return matches


    def analyze_ltf_reactions(
        self,
        matches: list[MatchedPattern],
        ltf_df: pd.DataFrame,
        outcome_window: int = 30,
    ) -> list[LTFReaction]:
        """Phase 2: Check LTF reaction at each matched zone."""
        reactions: list[LTFReaction] = []
        for match in matches:
            if match.freshness == "broken":
                continue
            reaction = self._find_ltf_reaction(match, ltf_df, outcome_window)
            if reaction:
                reactions.append(reaction)
        return reactions

    def full_scan(
        self,
        pattern: LearnedPattern,
        htf_df: pd.DataFrame,
        ltf_df: pd.DataFrame,
        max_matches: int = 100,
        outcome_window: int = 30,
    ) -> PatternScanResult:
        """Full Phase 1 + Phase 2 scan using a learned pattern."""
        matches = self.find_matches(pattern, htf_df, max_matches)
        reactions = self.analyze_ltf_reactions(matches, ltf_df, outcome_window)

        fresh = [m for m in matches if m.freshness == "fresh"]
        tested = [m for m in matches if m.freshness == "tested"]
        broken = [m for m in matches if m.freshness == "broken"]
        wins = [r for r in reactions if r.outcome == "win"]
        losses = [r for r in reactions if r.outcome == "loss"]

        stats = {
            "total_levels": len(matches),
            "fresh": len(fresh),
            "tested": len(tested),
            "broken": len(broken),
            "reactions_found": len(reactions),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(reactions) * 100, 1) if reactions else 0.0,
            "avg_rr": round(sum(r.rr_ratio for r in reactions if r.rr_ratio) / len(reactions), 2) if reactions else 0.0,
            "avg_similarity": round(sum(m.similarity_score for m in matches) / len(matches), 1) if matches else 0.0,
            "sessions": {},
        }
        for m in matches:
            stats["sessions"][m.session_label] = stats["sessions"].get(m.session_label, 0) + 1

        summary = self._build_summary(pattern, matches, reactions, stats)
        highlights = self._build_highlights(pattern, matches, reactions, stats)

        return PatternScanResult(
            total_levels_found=len(matches),
            fresh_levels=len(fresh),
            tested_levels=len(tested),
            broken_levels=len(broken),
            levels=matches,
            ltf_reactions=reactions,
            stats=stats,
            summary=summary,
            highlights=highlights,
        )


    # ─── Internal Methods ───────────────────────────────────────────

    def _find_swing_points(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        """Detect swing highs and swing lows from OHLCV data."""
        swings: list[dict[str, Any]] = []
        lookback = self.swing_lookback
        work = df.copy().reset_index(drop=True)

        for i in range(lookback, len(work) - lookback):
            row = work.loc[i]
            high = float(row["high"])
            low = float(row["low"])

            window_highs = work.loc[i - lookback: i + lookback, "high"].astype(float)
            if high == window_highs.max() and high > float(work.loc[i - 1, "high"]) and high > float(work.loc[i + 1, "high"]):
                swings.append({
                    "type": "high",
                    "price": high,
                    "low": low,
                    "idx": i,
                    "timestamp": pd.to_datetime(row["timestamp"], utc=True).isoformat(),
                    "open": float(row["open"]),
                    "close": float(row["close"]),
                })

            window_lows = work.loc[i - lookback: i + lookback, "low"].astype(float)
            if low == window_lows.min() and low < float(work.loc[i - 1, "low"]) and low < float(work.loc[i + 1, "low"]):
                swings.append({
                    "type": "low",
                    "price": low,
                    "high": high,
                    "idx": i,
                    "timestamp": pd.to_datetime(row["timestamp"], utc=True).isoformat(),
                    "open": float(row["open"]),
                    "close": float(row["close"]),
                })

        swings.sort(key=lambda x: x["idx"])
        return swings

    def _score_match(self, candidate: list[dict], target_seq: list[dict], pattern: LearnedPattern) -> float:
        """Score how well a candidate sequence matches the target pattern."""
        if len(candidate) != len(target_seq):
            return 0.0

        score = 60.0  # Base score if types match
        total_checks = len(target_seq)
        passed = 0

        for i, (swing, target) in enumerate(zip(candidate, target_seq)):
            # Check type match (high/low)
            if swing["type"] != target["type"]:
                return 0.0  # Hard fail - type must match

            passed += 1

            # Check relation (higher/lower than previous)
            if i > 0 and target.get("relation") in ("higher", "lower"):
                prev = candidate[i - 1]
                if target["relation"] == "higher" and swing["price"] <= prev["price"]:
                    score -= 20.0
                elif target["relation"] == "lower" and swing["price"] >= prev["price"]:
                    score -= 20.0
                else:
                    score += 10.0

        # Bonus for proportional similarity to examples
        if pattern.examples:
            prop_score = self._proportional_similarity(candidate, pattern.examples[0])
            score += prop_score * 0.3

        return max(0.0, min(100.0, score))


    def _proportional_similarity(self, candidate: list[dict], example: PatternExample) -> float:
        """Compare proportions of swing moves between candidate and example."""
        if len(example.swing_points) < 2 or len(candidate) < 2:
            return 0.0

        # Calculate relative moves in example
        ex_prices = [sp.price for sp in example.swing_points]
        ex_range = max(ex_prices) - min(ex_prices)
        if ex_range <= 0:
            return 0.0

        # Calculate relative moves in candidate
        cand_prices = [s["price"] for s in candidate]
        cand_range = max(cand_prices) - min(cand_prices)
        if cand_range <= 0:
            return 0.0

        # Compare normalized positions
        ex_norm = [(p - min(ex_prices)) / ex_range for p in ex_prices]
        cand_norm = [(p - min(cand_prices)) / cand_range for p in cand_prices]

        # Average difference in normalized positions
        min_len = min(len(ex_norm), len(cand_norm))
        diffs = [abs(ex_norm[i] - cand_norm[i]) for i in range(min_len)]
        avg_diff = sum(diffs) / len(diffs) if diffs else 1.0

        return max(0.0, (1.0 - avg_diff) * 30.0)

    def _extract_zone(self, candidate: list[dict], pattern: LearnedPattern) -> tuple[float, float]:
        """Extract the zone (entry area) from matched swing points."""
        if pattern.zone_position == "last":
            last = candidate[-1]
        elif pattern.zone_position == "second_last" and len(candidate) >= 2:
            last = candidate[-2]
        else:
            last = candidate[-1]

        if pattern.zone_from == "swing_low" or last["type"] == "low":
            zone_low = last["price"]
            zone_high = last.get("high", last["price"] * 1.003)
            # Tighter zone using candle body
            body_low = min(last.get("open", zone_low), last.get("close", zone_low))
            zone_high = min(zone_high, body_low + (zone_high - zone_low) * 0.6)
        else:
            zone_high = last["price"]
            zone_low = last.get("low", last["price"] * 0.997)
            body_high = max(last.get("open", zone_high), last.get("close", zone_high))
            zone_low = max(zone_low, body_high - (zone_high - zone_low) * 0.6)

        return zone_low, zone_high

    def _check_freshness(self, df: pd.DataFrame, start_idx: int, zone_low: float, zone_high: float, direction: str) -> tuple[str, int, str | None]:
        """Check if the zone was touched or broken after pattern formation."""
        future = df.loc[start_idx + 1:].reset_index(drop=True)
        touches = 0
        first_touch = None

        for _, row in future.iterrows():
            high = float(row["high"])
            low = float(row["low"])
            touched = high >= zone_low and low <= zone_high
            if touched:
                touches += 1
                if first_touch is None:
                    first_touch = pd.to_datetime(row["timestamp"], utc=True).isoformat()
                # Check if broken
                if direction == "bullish" and float(row["close"]) < zone_low:
                    return "broken", touches, first_touch
                if direction == "bearish" and float(row["close"]) > zone_high:
                    return "broken", touches, first_touch

        if touches > 0:
            return "tested", touches, first_touch
        return "fresh", 0, None


    def _find_ltf_reaction(self, match: MatchedPattern, ltf_df: pd.DataFrame, outcome_window: int) -> LTFReaction | None:
        """Find LTF reaction when price reaches the pattern zone."""
        match_time = pd.to_datetime(match.timestamp, utc=True)
        future = ltf_df[ltf_df["timestamp"] >= match_time].copy().reset_index(drop=True)
        if future.empty:
            return None

        # Find first touch of zone
        touch_idx = None
        for idx, row in future.iterrows():
            if float(row["high"]) >= match.zone_low and float(row["low"]) <= match.zone_high:
                touch_idx = idx
                break

        if touch_idx is None:
            return None

        touch_row = future.loc[touch_idx]
        touch_time = pd.to_datetime(touch_row["timestamp"], utc=True).isoformat()
        entry = float(touch_row["close"])

        # Outcome analysis
        post = future.loc[touch_idx: min(len(future) - 1, touch_idx + outcome_window)]
        if post.empty:
            return None

        max_high = float(post["high"].max())
        min_low = float(post["low"].min())

        if match.direction == "bullish":
            move = round(max_high - entry, 2)
            adverse = round(entry - min_low, 2)
            stop = match.zone_low - (match.zone_high - match.zone_low) * 0.5
            tp = entry + (entry - stop) * 2
        else:
            move = round(entry - min_low, 2)
            adverse = round(max_high - entry, 2)
            stop = match.zone_high + (match.zone_high - match.zone_low) * 0.5
            tp = entry - (stop - entry) * 2

        risk = round(abs(entry - stop), 3)
        reward = round(abs(tp - entry), 3)
        rr = round(reward / risk, 2) if risk > 0 else None

        if move >= risk * 2:
            outcome = "win"
        elif adverse >= risk:
            outcome = "loss"
        else:
            outcome = "breakeven"

        # Detect LTF pattern
        ltf_pattern = self._detect_ltf_pattern(future, touch_idx, match.direction)

        if move >= risk * 3:
            reaction_type = "strong_rejection"
        elif move >= risk * 1.5:
            reaction_type = "wick_test"
        elif adverse >= risk:
            reaction_type = "break_through"
        else:
            reaction_type = "consolidation"

        return LTFReaction(
            match_id=match.match_id,
            reaction_type=reaction_type,
            reaction_time=touch_time,
            entry_price=round(entry, 3),
            stop_loss=round(stop, 3),
            take_profit=round(tp, 3),
            risk_points=risk,
            reward_points=reward,
            rr_ratio=rr,
            move_after=move,
            adverse_after=adverse,
            outcome=outcome,
            ltf_pattern=ltf_pattern,
            explanation=[
                f"Price reached zone @ {touch_time[:16]}",
                f"Entry: {entry:.3f}, SL: {stop:.3f}, TP: {tp:.3f}",
                f"Move: {move}, Adverse: {adverse}",
                f"LTF pattern: {ltf_pattern}",
            ],
        )


    def _detect_ltf_pattern(self, df: pd.DataFrame, touch_idx: int, direction: str) -> str:
        """Detect what pattern appeared on LTF at the touch point."""
        window = df.loc[max(0, touch_idx - 2): min(len(df) - 1, touch_idx + 3)].reset_index(drop=True)

        # FVG check
        for i in range(2, len(window)):
            r1 = window.loc[i - 2]
            r3 = window.loc[i]
            if direction == "bullish" and float(r3["low"]) > float(r1["high"]):
                return "fvg"
            if direction == "bearish" and float(r3["high"]) < float(r1["low"]):
                return "fvg"

        # Displacement check
        touch_row = df.loc[touch_idx]
        body = abs(float(touch_row["close"]) - float(touch_row["open"]))
        rng = max(0.01, float(touch_row["high"]) - float(touch_row["low"]))
        if body / rng >= 0.65:
            if direction == "bullish" and float(touch_row["close"]) > float(touch_row["open"]):
                return "displacement"
            if direction == "bearish" and float(touch_row["close"]) < float(touch_row["open"]):
                return "displacement"

        # Engulfing check
        if touch_idx > 0:
            prev = df.loc[touch_idx - 1]
            curr = df.loc[touch_idx]
            if direction == "bullish" and float(curr["close"]) > float(prev["open"]) and float(curr["open"]) < float(prev["close"]):
                return "engulfing"
            if direction == "bearish" and float(curr["close"]) < float(prev["open"]) and float(curr["open"]) > float(prev["close"]):
                return "engulfing"

        # Wick rejection
        if direction == "bullish":
            wick = min(float(touch_row["open"]), float(touch_row["close"])) - float(touch_row["low"])
            if wick / rng >= 0.5:
                return "wick_rejection"
        else:
            wick = float(touch_row["high"]) - max(float(touch_row["open"]), float(touch_row["close"]))
            if wick / rng >= 0.5:
                return "wick_rejection"

        return "no_clear_pattern"

    def _session_label(self, ts: pd.Timestamp) -> str:
        hour = ts.hour
        if 7 <= hour < 12:
            return "London"
        if 12 <= hour < 17:
            return "New York"
        return "Asia/Other"

    def _build_match_explanation(self, candidate: list[dict], target_seq: list[dict], score: float, pattern: LearnedPattern) -> list[str]:
        lines = [f"Pattern: {pattern.name} (similarity: {score:.0f}%)"]
        for i, (swing, target) in enumerate(zip(candidate, target_seq)):
            label = target.get("label") or f"Point {i+1}"
            lines.append(f"  {label}: {swing['type']} @ {swing['price']:.3f} ({swing['timestamp'][:16]})")
        return lines

    def _build_summary(self, pattern: LearnedPattern, matches: list[MatchedPattern], reactions: list[LTFReaction], stats: dict) -> str:
        if not matches:
            return f"لم يتم العثور على أي تطابق لنمط '{pattern.name}' في البيانات المحددة."
        parts = [f"تم اكتشاف {len(matches)} حالة تطابق لنمط '{pattern.name}'"]
        fresh = stats.get("fresh", 0)
        if fresh:
            parts.append(f"({fresh} طازج)")
        if reactions:
            parts.append(f"- {len(reactions)} وصل السعر للزون")
            parts.append(f"- win rate: {stats.get('win_rate', 0)}%")
        return " ".join(parts)

    def _build_highlights(self, pattern: LearnedPattern, matches: list[MatchedPattern], reactions: list[LTFReaction], stats: dict) -> list[str]:
        highlights = []
        if matches:
            best = max(matches, key=lambda m: m.similarity_score)
            highlights.append(f"أقوى تطابق: {best.similarity_score}% @ {best.timestamp[:16]}")
        fresh = [m for m in matches if m.freshness == "fresh"]
        if fresh:
            highlights.append(f"{len(fresh)} مستوى لسا ما انلمس")
        if reactions:
            wins = [r for r in reactions if r.outcome == "win"]
            if wins:
                best_win = max(wins, key=lambda r: r.move_after)
                highlights.append(f"أفضل تفاعل: +{best_win.move_after} نقطة")
        return highlights
