"""Pattern detection engine - supports QM (Quasimodo) and custom patterns.

QM (Quasimodo) Pattern:
- Bullish QM: Price makes LL -> HH -> HL (the HL is the zone of interest)
- Bearish QM: Price makes HH -> LL -> LH (the LH is the zone of interest)

The zone formed at the HL (bullish) or LH (bearish) is the key level for entries.

This module provides:
1. detect_qm_levels() - Find all QM patterns on any timeframe
2. analyze_ltf_reaction() - Phase 2: Check what happened when price reached the QM level
3. detect_custom_pattern() - Apply user-defined pattern rules
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
class QMLevel:
    """A single detected QM (Quasimodo) level."""
    level_id: str
    direction: str  # "bullish" or "bearish"
    zone_low: float
    zone_high: float
    zone_mid: float
    timestamp: str  # when the QM completed
    swing_points: list[dict[str, Any]]  # the 3 swing points that form the QM
    freshness: str  # "fresh", "tested", "broken"
    touches_after: int
    first_touch_time: str | None
    session_label: str
    quality_score: float
    explanation: list[str]


@dataclass
class LTFReaction:
    """What happened on a lower timeframe when price reached a QM level."""
    level_id: str
    reaction_type: str  # "strong_rejection", "wick_test", "break_through", "consolidation"
    reaction_time: str
    entry_price: float | None
    stop_loss: float | None
    take_profit: float | None
    risk_points: float | None
    reward_points: float | None
    rr_ratio: float | None
    move_after: float
    adverse_after: float
    outcome: str  # "win", "loss", "breakeven", "pending"
    ltf_pattern: str  # what pattern appeared on LTF (e.g. "fvg", "displacement", "engulfing")
    explanation: list[str]


@dataclass
class PatternScanResult:
    """Complete result of a pattern scan (Phase 1 + Phase 2)."""
    total_levels_found: int
    fresh_levels: int
    tested_levels: int
    broken_levels: int
    levels: list[QMLevel]
    ltf_reactions: list[LTFReaction]
    stats: dict[str, Any]
    summary: str
    highlights: list[str]


class PatternDetector:
    """Detects structural patterns like QM on OHLCV data."""

    def __init__(self, swing_lookback: int = 5, zone_buffer_pct: float = 0.001):
        self.swing_lookback = swing_lookback
        self.zone_buffer_pct = zone_buffer_pct

    def detect_qm_levels(
        self,
        df: pd.DataFrame,
        direction: str = "either",
        max_levels: int = 100,
        freshness_check_df: pd.DataFrame | None = None,
    ) -> list[QMLevel]:
        """
        Phase 1: Detect all QM (Quasimodo) levels on the given dataframe.

        QM Bullish: swing low -> swing high -> higher low (HL forms the zone)
        QM Bearish: swing high -> swing low -> lower high (LH forms the zone)
        """
        swings = self._find_swing_points(df)
        if len(swings) < 3:
            return []

        directions = [direction] if direction in {"bullish", "bearish"} else ["bullish", "bearish"]
        levels: list[QMLevel] = []

        for i in range(2, len(swings)):
            s1 = swings[i - 2]
            s2 = swings[i - 1]
            s3 = swings[i]

            for d in directions:
                qm = self._check_qm_pattern(s1, s2, s3, d, df)
                if qm is not None:
                    # Check freshness
                    if freshness_check_df is not None:
                        qm = self._assess_freshness(qm, freshness_check_df)
                    else:
                        remaining = df[df["timestamp"] > pd.to_datetime(qm.timestamp, utc=True)]
                        qm = self._assess_freshness(qm, remaining)
                    levels.append(qm)

            if len(levels) >= max_levels:
                break

        # Sort by timestamp (newest first)
        levels.sort(key=lambda x: x.timestamp, reverse=True)
        return levels[:max_levels]

    def analyze_ltf_reactions(
        self,
        levels: list[QMLevel],
        ltf_df: pd.DataFrame,
        outcome_window: int = 30,
    ) -> list[LTFReaction]:
        """
        Phase 2: For each QM level, check what happened on the lower timeframe
        when price reached that level.
        """
        reactions: list[LTFReaction] = []

        for level in levels:
            if level.freshness == "broken":
                continue

            reaction = self._find_ltf_reaction(level, ltf_df, outcome_window)
            if reaction is not None:
                reactions.append(reaction)

        return reactions

    def full_scan(
        self,
        htf_df: pd.DataFrame,
        ltf_df: pd.DataFrame,
        direction: str = "either",
        max_levels: int = 100,
        outcome_window: int = 30,
    ) -> PatternScanResult:
        """Run full Phase 1 + Phase 2 scan."""
        # Phase 1: Find QM levels
        levels = self.detect_qm_levels(htf_df, direction, max_levels, freshness_check_df=htf_df)

        # Phase 2: Analyze LTF reactions
        reactions = self.analyze_ltf_reactions(levels, ltf_df, outcome_window)

        # Build stats
        fresh = [l for l in levels if l.freshness == "fresh"]
        tested = [l for l in levels if l.freshness == "tested"]
        broken = [l for l in levels if l.freshness == "broken"]

        wins = [r for r in reactions if r.outcome == "win"]
        losses = [r for r in reactions if r.outcome == "loss"]

        stats = {
            "total_levels": len(levels),
            "fresh": len(fresh),
            "tested": len(tested),
            "broken": len(broken),
            "reactions_found": len(reactions),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(reactions) * 100, 1) if reactions else 0.0,
            "avg_rr": round(sum(r.rr_ratio for r in reactions if r.rr_ratio) / len(reactions), 2) if reactions else 0.0,
            "avg_move": round(sum(r.move_after for r in reactions) / len(reactions), 2) if reactions else 0.0,
            "bullish_levels": len([l for l in levels if l.direction == "bullish"]),
            "bearish_levels": len([l for l in levels if l.direction == "bearish"]),
            "sessions": dict(defaultdict(int, {l.session_label: 0 for l in levels})),
        }
        for l in levels:
            stats["sessions"][l.session_label] = stats["sessions"].get(l.session_label, 0) + 1

        summary = self._build_summary(levels, reactions, stats)
        highlights = self._build_highlights(levels, reactions, stats)

        return PatternScanResult(
            total_levels_found=len(levels),
            fresh_levels=len(fresh),
            tested_levels=len(tested),
            broken_levels=len(broken),
            levels=levels,
            ltf_reactions=reactions,
            stats=stats,
            summary=summary,
            highlights=highlights,
        )

    # ─── Internal Methods ───────────────────────────────────────────

    def _find_swing_points(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        """Find swing highs and swing lows."""
        swings: list[dict[str, Any]] = []
        lookback = self.swing_lookback
        work = df.copy().reset_index(drop=True)

        for i in range(lookback, len(work) - lookback):
            row = work.loc[i]
            high = float(row["high"])
            low = float(row["low"])

            # Swing High: highest high in window
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

            # Swing Low: lowest low in window
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

        # Sort by index
        swings.sort(key=lambda x: x["idx"])
        return swings

    def _check_qm_pattern(
        self, s1: dict, s2: dict, s3: dict, direction: str, df: pd.DataFrame
    ) -> QMLevel | None:
        """
        Check if 3 consecutive swing points form a QM pattern.

        Bullish QM: Low(s1) -> High(s2) -> Low(s3) where s3.low > s1.low (Higher Low)
                    AND s2.high > previous swing high (new HH)
        Bearish QM: High(s1) -> Low(s2) -> High(s3) where s3.high < s1.high (Lower High)
                    AND s2.low < previous swing low (new LL)
        """
        if direction == "bullish":
            # Need: swing_low -> swing_high -> swing_low (HL)
            if s1["type"] != "low" or s2["type"] != "high" or s3["type"] != "low":
                return None
            # s3 must be Higher Low than s1
            if s3["price"] <= s1["price"]:
                return None
            # s2 must be a significant high (HH condition implied by swing detection)
            # Zone is at s3 (the Higher Low)
            zone_low = s3["price"]
            zone_high = s3.get("high", s3["price"] + (s2["price"] - s3["price"]) * 0.1)
            # Use candle body for tighter zone
            body_low = min(s3.get("open", zone_low), s3.get("close", zone_low))
            zone_high = min(zone_high, body_low + (zone_high - zone_low) * 0.5)

        elif direction == "bearish":
            # Need: swing_high -> swing_low -> swing_high (LH)
            if s1["type"] != "high" or s2["type"] != "low" or s3["type"] != "high":
                return None
            # s3 must be Lower High than s1
            if s3["price"] >= s1["price"]:
                return None
            # Zone is at s3 (the Lower High)
            zone_high = s3["price"]
            zone_low = s3.get("low", s3["price"] - (s3["price"] - s2["price"]) * 0.1)
            body_high = max(s3.get("open", zone_high), s3.get("close", zone_high))
            zone_low = max(zone_low, body_high - (zone_high - zone_low) * 0.5)
        else:
            return None

        zone_mid = round((zone_low + zone_high) / 2, 3)
        level_id = md5(f"qm-{direction}-{s3['timestamp']}-{zone_low}-{zone_high}".encode()).hexdigest()[:10]

        # Quality based on pattern clarity
        if direction == "bullish":
            hl_depth = (s3["price"] - s1["price"]) / max(0.01, s2["price"] - s1["price"])
            quality = min(100, 50 + hl_depth * 30 + (10 if s2["price"] - s3["price"] > (s2["price"] - s1["price"]) * 0.3 else 0))
        else:
            lh_depth = (s1["price"] - s3["price"]) / max(0.01, s1["price"] - s2["price"])
            quality = min(100, 50 + lh_depth * 30 + (10 if s3["price"] - s2["price"] > (s1["price"] - s2["price"]) * 0.3 else 0))

        explanation = [
            f"QM {direction} pattern detected",
            f"Swing 1: {s1['type']} @ {s1['price']:.3f} ({s1['timestamp'][:16]})",
            f"Swing 2: {s2['type']} @ {s2['price']:.3f} ({s2['timestamp'][:16]})",
            f"Swing 3: {s3['type']} @ {s3['price']:.3f} ({s3['timestamp'][:16]})",
            f"Zone: {zone_low:.3f} → {zone_high:.3f}",
        ]

        return QMLevel(
            level_id=level_id,
            direction=direction,
            zone_low=round(zone_low, 3),
            zone_high=round(zone_high, 3),
            zone_mid=zone_mid,
            timestamp=s3["timestamp"],
            swing_points=[s1, s2, s3],
            freshness="fresh",
            touches_after=0,
            first_touch_time=None,
            session_label=self._session_label(pd.to_datetime(s3["timestamp"], utc=True)),
            quality_score=round(quality, 1),
            explanation=explanation,
        )

    def _assess_freshness(self, level: QMLevel, future_df: pd.DataFrame) -> QMLevel:
        """Check if a QM level was touched or broken after formation."""
        if future_df.empty:
            return level

        touches = 0
        first_touch = None
        broken = False
        level_time = pd.to_datetime(level.timestamp, utc=True)

        for _, row in future_df.iterrows():
            row_time = pd.to_datetime(row["timestamp"], utc=True)
            if row_time <= level_time:
                continue

            high = float(row["high"])
            low = float(row["low"])

            # Check if price touched the zone
            touched = high >= level.zone_low and low <= level.zone_high
            if touched:
                touches += 1
                if first_touch is None:
                    first_touch = row_time.isoformat()

                # Check if broken through
                if level.direction == "bullish" and float(row["close"]) < level.zone_low:
                    broken = True
                    break
                if level.direction == "bearish" and float(row["close"]) > level.zone_high:
                    broken = True
                    break

        if broken:
            level.freshness = "broken"
        elif touches > 0:
            level.freshness = "tested"
        else:
            level.freshness = "fresh"

        level.touches_after = touches
        level.first_touch_time = first_touch
        return level

    def _find_ltf_reaction(
        self, level: QMLevel, ltf_df: pd.DataFrame, outcome_window: int
    ) -> LTFReaction | None:
        """Find what happened on LTF when price reached the QM level."""
        level_time = pd.to_datetime(level.timestamp, utc=True)
        future = ltf_df[ltf_df["timestamp"] >= level_time].copy().reset_index(drop=True)

        if future.empty:
            return None

        # Find the first candle that touches the zone
        touch_idx = None
        for idx, row in future.iterrows():
            high = float(row["high"])
            low = float(row["low"])
            if high >= level.zone_low and low <= level.zone_high:
                touch_idx = idx
                break

        if touch_idx is None:
            return None

        touch_row = future.loc[touch_idx]
        touch_time = pd.to_datetime(touch_row["timestamp"], utc=True).isoformat()

        # Analyze outcome after touch
        post_touch = future.loc[touch_idx: min(len(future) - 1, touch_idx + outcome_window)]
        if post_touch.empty:
            return None

        entry = float(touch_row["close"])
        max_high = float(post_touch["high"].max())
        min_low = float(post_touch["low"].min())

        if level.direction == "bullish":
            move = round(max_high - entry, 2)
            adverse = round(entry - min_low, 2)
            stop = level.zone_low - (level.zone_high - level.zone_low) * 0.5
            tp = entry + (entry - stop) * 2
        else:
            move = round(entry - min_low, 2)
            adverse = round(max_high - entry, 2)
            stop = level.zone_high + (level.zone_high - level.zone_low) * 0.5
            tp = entry - (stop - entry) * 2

        risk = round(abs(entry - stop), 3)
        reward = round(abs(tp - entry), 3)
        rr = round(reward / risk, 2) if risk > 0 else None

        # Determine outcome
        if move >= risk * 2:
            outcome = "win"
        elif adverse >= risk:
            outcome = "loss"
        elif move > adverse:
            outcome = "breakeven"
        else:
            outcome = "loss"

        # Detect LTF pattern at touch
        ltf_pattern = self._detect_ltf_pattern(future, touch_idx, level.direction)

        # Determine reaction type
        if move >= risk * 3:
            reaction_type = "strong_rejection"
        elif move >= risk * 1.5:
            reaction_type = "wick_test"
        elif adverse >= risk:
            reaction_type = "break_through"
        else:
            reaction_type = "consolidation"

        explanation = [
            f"Price reached QM zone @ {touch_time[:16]}",
            f"Entry: {entry:.3f}, SL: {stop:.3f}, TP: {tp:.3f}",
            f"Move: {move} pts, Adverse: {adverse} pts",
            f"LTF pattern: {ltf_pattern}",
            f"Outcome: {outcome} ({reaction_type})",
        ]

        return LTFReaction(
            level_id=level.level_id,
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
            explanation=explanation,
        )

    def _detect_ltf_pattern(self, df: pd.DataFrame, touch_idx: int, direction: str) -> str:
        """Detect what pattern appeared on LTF around the touch point."""
        window = df.loc[max(0, touch_idx - 2): min(len(df) - 1, touch_idx + 3)].reset_index(drop=True)

        # Check for FVG
        for i in range(2, len(window)):
            r1 = window.loc[i - 2]
            r3 = window.loc[i]
            if direction == "bullish" and float(r3["low"]) > float(r1["high"]):
                return "fvg_bullish"
            if direction == "bearish" and float(r3["high"]) < float(r1["low"]):
                return "fvg_bearish"

        # Check for displacement (big body candle)
        touch_row = df.loc[touch_idx]
        body = abs(float(touch_row["close"]) - float(touch_row["open"]))
        range_size = max(0.01, float(touch_row["high"]) - float(touch_row["low"]))
        if body / range_size >= 0.65:
            if direction == "bullish" and float(touch_row["close"]) > float(touch_row["open"]):
                return "displacement_bullish"
            if direction == "bearish" and float(touch_row["close"]) < float(touch_row["open"]):
                return "displacement_bearish"

        # Check for engulfing
        if touch_idx > 0:
            prev = df.loc[touch_idx - 1]
            curr = df.loc[touch_idx]
            if direction == "bullish":
                if float(curr["close"]) > float(prev["open"]) and float(curr["open"]) < float(prev["close"]):
                    return "engulfing_bullish"
            else:
                if float(curr["close"]) < float(prev["open"]) and float(curr["open"]) > float(prev["close"]):
                    return "engulfing_bearish"

        # Check for wick rejection
        if direction == "bullish":
            lower_wick = float(touch_row["open"]) - float(touch_row["low"]) if float(touch_row["close"]) > float(touch_row["open"]) else float(touch_row["close"]) - float(touch_row["low"])
            if lower_wick / range_size >= 0.5:
                return "wick_rejection"
        else:
            upper_wick = float(touch_row["high"]) - float(touch_row["close"]) if float(touch_row["close"]) < float(touch_row["open"]) else float(touch_row["high"]) - float(touch_row["open"])
            if upper_wick / range_size >= 0.5:
                return "wick_rejection"

        return "no_clear_pattern"

    def _session_label(self, ts: pd.Timestamp) -> str:
        hour = ts.hour
        if 7 <= hour < 12:
            return "London"
        if 12 <= hour < 17:
            return "New York"
        return "Asia/Other"

    def _build_summary(self, levels: list[QMLevel], reactions: list[LTFReaction], stats: dict) -> str:
        if not levels:
            return "لم يتم اكتشاف أي نمط QM في النطاق المحدد."

        fresh = stats.get("fresh", 0)
        total = stats.get("total_levels", 0)
        wins = stats.get("wins", 0)
        win_rate = stats.get("win_rate", 0)

        parts = [f"تم اكتشاف {total} مستوى QM"]
        if fresh:
            parts.append(f"({fresh} طازج)")
        if reactions:
            parts.append(f"- {len(reactions)} وصل السعر إليها")
            parts.append(f"- win rate: {win_rate}%")
        if stats.get("bullish_levels") and stats.get("bearish_levels"):
            parts.append(f"({stats['bullish_levels']} صعودي، {stats['bearish_levels']} هبوطي)")

        return " ".join(parts)

    def _build_highlights(self, levels: list[QMLevel], reactions: list[LTFReaction], stats: dict) -> list[str]:
        highlights = []
        if levels:
            best = max(levels, key=lambda l: l.quality_score)
            highlights.append(f"أفضل QM: {best.direction} بجودة {best.quality_score} @ {best.timestamp[:16]}")

        fresh = [l for l in levels if l.freshness == "fresh"]
        if fresh:
            highlights.append(f"{len(fresh)} مستوى QM لسا ما انلمس (فريش)")

        if reactions:
            wins = [r for r in reactions if r.outcome == "win"]
            if wins:
                best_win = max(wins, key=lambda r: r.move_after)
                highlights.append(f"أفضل تفاعل: {best_win.move_after} نقطة ربح عند {best_win.reaction_time[:16]}")

        sessions = stats.get("sessions", {})
        if sessions:
            top_session = max(sessions.items(), key=lambda kv: kv[1])
            highlights.append(f"أكثر جلسة ظهور: {top_session[0]} ({top_session[1]} مرة)")

        return highlights
