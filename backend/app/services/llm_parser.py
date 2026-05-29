from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings
from app.models import Direction, StrategyCorrectRequest, StrategyInterpretResponse, StrategyNode, StrategySpec
from app.repositories.preference_store import PreferenceStore
from app.strategy_registry import detect_school, detect_strategy, strategy_templates

TIMEFRAME_PATTERNS = [
    (r"(?:فريم|tf|timeframe)?\s*1\s*(?:m|min|د|دقيقة)\b", "1m"),
    (r"(?:فريم|tf|timeframe)?\s*3\s*(?:m|min|د|دقيقة)|3min|3 minute|3 دقائق", "3m"),
    (r"(?:فريم|tf|timeframe)?\s*5\s*(?:m|min|د|دقيقة)", "5m"),
    (r"(?:فريم|tf|timeframe)?\s*15\s*(?:m|min|د|دقيقة)|ربع\s*ساعة", "15m"),
    (r"(?:فريم|tf|timeframe)?\s*30\s*(?:m|min|د|دقيقة)|نص\s*ساعة|half\s*hour", "30m"),
    (r"(?:فريم|tf|timeframe)?\s*(?:1h|60m|1\s*(?:h|ساعة|س))", "1h"),
    (r"(?:فريم|tf|timeframe)?\s*(?:4h|4\s*(?:h|ساعة|س))", "4h"),
]
SYMBOL_ALIASES = {
    "XAUUSD": ["ذهب", "الذهب", "gold", "xau", "xauusd", "xau/usd"],
    "EURUSD": ["eurusd", "يورو دولار", "euro usd"],
    "GBPUSD": ["gbpusd", "باوند دولار", "pound usd"],
    "BTCUSD": ["btc", "bitcoin", "بتكوين"],
}
MARKET_LABELS = {"XAUUSD": "Gold", "EURUSD": "EURUSD", "GBPUSD": "GBPUSD", "BTCUSD": "BTCUSD"}
NODE_ALIASES = {
    "bos": ["bos", "break of structure", "كسر هيكل", "بوس"],
    "mss": ["mss", "market structure shift", "choch", "change of character", "shift"],
    "order_block": ["order block", "اوردر بلوك", "ob"],
    "zone_from_wick": ["wick", "ذيل", "shadow", "zone from wick", "منطقة الذيل"],
    "retest_zone": ["retest", "return", "رجوع", "يرجع", "عودة", "touch", "لمس", "mitigation"],
    "liquidity_sweep": ["sweep", "raid", "liquidity", "سيولة", "سحب سيولة", "liquidity grab"],
    "fvg": ["fvg", "fair value gap", "فير فاليو", "imbalance", "imblance"],
    "breaker": ["breaker", "breaker block"],
    "premium_discount": ["premium", "discount"],
    "equal_highs_lows": ["equal highs", "equal lows", "eqh", "eql", "قمم متساوية", "قيعان متساوية"],
    "displacement": ["displacement", "اندفاع", "دفعة قوية", "impulse", "candle expansion"],
}
SESSION_ALIASES = {"London": ["london", "جلسة لندن"], "New York": ["new york", "جلسة نيويورك", "ny"], "Asia": ["asia", "asian", "جلسة اسيا", "جلسة آسيا", "tokyo"]}
BULLISH_WORDS = ["bullish", "buy", "شراء", "صاعد", "لونغ", "long", "support", "demand"]
BEARISH_WORDS = ["bearish", "sell", "بيع", "هابط", "شورت", "short", "resistance", "supply"]
EXECUTABLE_STEPS = {"bos", "mss", "order_block", "zone_from_wick", "retest_zone", "liquidity_sweep", "fvg", "breaker", "premium_discount", "equal_highs_lows", "displacement"}


class StrategyInterpreter:
    def __init__(self, preference_store: PreferenceStore) -> None:
        self.preference_store = preference_store

    async def interpret(self, text: str) -> StrategyInterpretResponse:
        if settings.app_enable_ollama:
            parsed = await self._try_ollama(text)
            if parsed is not None:
                return parsed
        return self._heuristic_parse(text)

    async def correct(self, payload: StrategyCorrectRequest) -> StrategyInterpretResponse:
        base = payload.strategy.model_copy(deep=True)
        merged = self._heuristic_parse(self._render_strategy_as_prompt(base) + "\n" + payload.correction_text)
        merged.understood.parser_source = "heuristic-correction"
        self.preference_store.learn_from_correction_text(payload.correction_text)
        self.preference_store.learn_from_strategy(merged.understood, note=f"Correction learned: {payload.correction_text[:140]}")
        return merged

    def templates(self) -> list[dict]:
        return strategy_templates()

    async def _try_ollama(self, text: str) -> StrategyInterpretResponse | None:
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                response = await client.post(
                    f"{settings.app_ollama_base_url}/api/generate",
                    json={"model": settings.app_ollama_model, "prompt": self._ollama_prompt(text), "stream": False},
                )
                response.raise_for_status()
                raw = (response.json() or {}).get("response", "")
                if not raw:
                    return None
                data = json.loads(raw)
                return self._heuristic_parse(data.get("normalized_prompt") or text)
        except Exception:
            return None

    def _ollama_prompt(self, text: str) -> str:
        return "Normalize this trading prompt and return JSON only with normalized_prompt. Prompt: " + text

    def _heuristic_parse(self, text: str) -> StrategyInterpretResponse:
        original = text.strip()
        lower = original.lower()
        prefs = self.preference_store.get()
        symbol = self._detect_symbol(lower) or prefs.default_symbol or "XAUUSD"
        market_label = MARKET_LABELS.get(symbol, symbol)
        mode = self._detect_mode(lower)
        timeframe = self._detect_primary_timeframe(lower) or "1h"
        execution_tf = self._detect_execution_timeframe(lower, timeframe)
        lookback_days = self._detect_lookback_days(lower) or 30
        direction = self._detect_direction(lower, prefs.preferred_direction)
        school_def = detect_school(lower)
        strategy_def = detect_strategy(lower)
        school_key = strategy_def.school if strategy_def else (school_def.key if school_def else "smc")
        strategy_key = strategy_def.key if strategy_def else "smc_core"
        if strategy_key == "fresh_snr" and self._looks_like_composite_snr_fvg(lower):
            strategy_def = detect_strategy("fresh snr reaction fvg poi")
            school_key = strategy_def.school if strategy_def else "hybrid"
            strategy_key = strategy_def.key if strategy_def else "snr_fresh_reaction_fvg_poi"
        if strategy_def and strategy_def.default_direction != "either" and direction == "either":
            direction = strategy_def.default_direction  # type: ignore[assignment]
        constraints = self._detect_constraints(lower, timeframe, execution_tf, direction, prefs.preferred_zone_mode, lookback_days, school_key, strategy_key)
        sequence = self._build_sequence(lower, timeframe, execution_tf, direction, strategy_def)
        unsupported = self._detect_unsupported(lower)
        warnings: list[str] = []
        if direction == "either":
            warnings.append("الاتجاه غير محدد، لذلك سيتم فحص الاتجاهين.")
        if not sequence:
            warnings.append("هذه الاستراتيجية تعتمد على كشف مناطق/مستويات مباشرة، وليس سلسلة SMC تقليدية.")
        spec = StrategySpec(
            school=school_key,
            strategy_key=strategy_key,
            symbol=symbol,
            market_label=market_label,
            timeframe=timeframe,
            lookback_days=lookback_days,
            mode=mode,
            primary_timeframe=timeframe,
            execution_timeframe=execution_tf,
            direction=direction,
            sequence=sequence,
            constraints=constraints,
            notes=self._collect_notes(mode, school_key, strategy_key, constraints),
            unsupported_phrases=unsupported,
            needs_confirmation=False,
            parser_source="heuristic-v3-school-registry",
            confidence=self._estimate_confidence(sequence, constraints, unsupported, strategy_def is not None),
        )
        spec = self._apply_preferences(spec)
        spec = self._normalize_spec(spec)
        return StrategyInterpretResponse(
            understood=spec,
            preview_text=self._preview_text(spec),
            follow_up_questions=[],
            warnings=warnings,
        )

    def _detect_mode(self, lower: str) -> str:
        if any(k in lower for k in ["backtest", "اختبر", "اعمل backtest", "باك تست"]):
            return "backtest"
        if any(k in lower for k in ["scan", "افحص", "سكان"]):
            return "scan"
        return "research"

    def _detect_symbol(self, lower: str) -> str | None:
        for symbol, aliases in SYMBOL_ALIASES.items():
            if any(alias in lower for alias in aliases):
                return symbol
        return None

    def _detect_primary_timeframe(self, lower: str) -> str | None:
        for pattern, tf in TIMEFRAME_PATTERNS[::-1]:
            if re.search(pattern, lower):
                return tf
        return None

    def _detect_execution_timeframe(self, lower: str, primary_tf: str) -> str:
        found = []
        for pattern, tf in TIMEFRAME_PATTERNS:
            if re.search(pattern, lower):
                found.append(tf)
        if len(found) >= 2:
            return found[0] if found[0] != primary_tf else found[1]
        mapping = {"4h": "1h", "1h": "15m", "30m": "5m", "15m": "5m", "5m": "1m", "3m": "1m", "1m": "1m"}
        return mapping.get(primary_tf, "15m")

    def _detect_lookback_days(self, lower: str) -> int | None:
        if "today" in lower or "اليوم" in lower:
            return 1
        if "yesterday" in lower or "أمس" in lower:
            return 1
        if "this week" in lower or "هذا الأسبوع" in lower or "هاد الأسبوع" in lower:
            return 7
        if match := re.search(r"(?:last|آخر)\s*(\d+)\s*(?:day|days|يوم|أيام)", lower):
            return int(match.group(1))
        if match := re.search(r"(\d+)\s*(?:day|days|يوم|أيام)", lower):
            return int(match.group(1))
        return None

    def _detect_direction(self, lower: str, default: Direction) -> Direction:
        bullish = any(word in lower for word in BULLISH_WORDS)
        bearish = any(word in lower for word in BEARISH_WORDS)
        if bullish and not bearish:
            return "bullish"
        if bearish and not bullish:
            return "bearish"
        return default or "either"

    def _looks_like_composite_snr_fvg(self, lower: str) -> bool:
        has_snr = any(token in lower for token in ["snr", "s&r", "support resistance", "دعم", "مقاومة"])
        has_fvg = any(token in lower for token in ["fvg", "fair value gap", "imbalance", "فير فاليو"])
        has_reaction = any(token in lower for token in ["reaction", "react", "تفاعل", "ارتد", "retest", "touch", "لمس"])
        has_poi = any(token in lower for token in ["poi", "point of interest", "نقطة الاهتمام", "entry"])
        return has_snr and has_fvg and (has_reaction or has_poi)

    def _detect_constraints(self, lower: str, primary_tf: str, execution_tf: str, direction: Direction, zone_mode: str, lookback_days: int, school_key: str, strategy_key: str) -> dict:
        now = datetime.now(timezone.utc)
        constraints: dict[str, object] = {
            "zone_mode": zone_mode,
            "source_timeframe_minutes": self._tf_minutes(primary_tf),
            "execution_timeframe_minutes": self._tf_minutes(execution_tf),
            "backtest_label": f"last_{lookback_days}_days",
            "backtest_start": (now - timedelta(days=lookback_days)).isoformat(),
            "backtest_end": now.isoformat(),
            "scan_policy": "focused",
            "school": school_key,
            "strategy_key": strategy_key,
            "fresh_only": "fresh" in lower or "فريش" in lower,
        }
        sessions = [name for name, aliases in SESSION_ALIASES.items() if any(alias in lower for alias in aliases)]
        if sessions:
            constraints["sessions"] = sessions
        if school_key in {"sr", "supply_demand", "hybrid"}:
            constraints["max_touches"] = 0 if constraints["fresh_only"] else 1
            constraints["level_tolerance_ratio"] = 0.18
            constraints["scan_policy"] = "level_scan"
        if strategy_key == "snr_fresh_reaction_fvg_poi":
            constraints["fresh_only"] = True
            constraints["scan_policy"] = "composite_scan"
            constraints["reaction_required"] = True
            constraints["poi_mode"] = "fvg_mid"
            constraints["confirmation_within_bars"] = 16
            constraints["max_touches"] = 0
        if strategy_key == "fresh_supply":
            constraints["zone_bias"] = "supply"
        elif strategy_key == "fresh_demand":
            constraints["zone_bias"] = "demand"
        elif strategy_key == "fresh_snr":
            constraints["zone_bias"] = "support" if direction == "bullish" else "resistance" if direction == "bearish" else "both"
        if "today" in lower or "اليوم" in lower:
            constraints["backtest_label"] = "today"
        elif "yesterday" in lower or "أمس" in lower:
            start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            constraints["backtest_start"] = start.isoformat()
            constraints["backtest_end"] = end.isoformat()
            constraints["backtest_label"] = "yesterday"
        elif "this week" in lower or "هذا الأسبوع" in lower or "هاد الأسبوع" in lower:
            start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            constraints["backtest_start"] = start.isoformat()
            constraints["backtest_end"] = now.isoformat()
            constraints["backtest_label"] = "this_week"
        return constraints

    def _build_sequence(self, lower: str, primary_tf: str, execution_tf: str, direction: Direction, strategy_def) -> list[StrategyNode]:
        if strategy_def and strategy_def.default_sequence:
            return strategy_def.build_sequence(primary_tf, execution_tf, direction)
        if strategy_def and strategy_def.key in {"fresh_snr", "fresh_supply", "fresh_demand", "level_flip"}:
            return []
        if strategy_def and strategy_def.key == "snr_fresh_reaction_fvg_poi":
            return strategy_def.build_sequence(primary_tf, execution_tf, direction)
        sequence: list[StrategyNode] = []
        for node_type, aliases in NODE_ALIASES.items():
            if any(alias in lower for alias in aliases):
                sequence.append(StrategyNode(type=node_type, timeframe=primary_tf if node_type in {"bos", "mss", "order_block", "breaker"} else execution_tf, direction=direction))
        if not sequence:
            sequence = [
                StrategyNode(type="bos", timeframe=primary_tf, direction=direction),
                StrategyNode(type="retest_zone", timeframe=execution_tf, direction=direction),
            ]
        elif all(node.type != "retest_zone" for node in sequence) and any(node.type in {"bos", "order_block", "breaker", "zone_from_wick"} for node in sequence):
            sequence.append(StrategyNode(type="retest_zone", timeframe=execution_tf, direction=direction))
        order = ["bos", "breaker", "order_block", "zone_from_wick", "retest_zone", "equal_highs_lows", "liquidity_sweep", "premium_discount", "mss", "displacement", "fvg"]
        sequence.sort(key=lambda n: order.index(n.type) if n.type in order else 999)
        return [node for node in sequence if node.type in EXECUTABLE_STEPS]

    def _detect_unsupported(self, lower: str) -> list[str]:
        unsupported = []
        for phrase in ["rsi", "macd", "elliott", "volume profile"]:
            if phrase in lower:
                unsupported.append(phrase)
        return unsupported

    def _collect_notes(self, mode: str, school_key: str, strategy_key: str, constraints: dict) -> list[str]:
        notes = [f"Mode: {mode}", f"School: {school_key}", f"Strategy: {strategy_key}", f"Lookback days: {constraints.get('backtest_label', 'last_30_days')}"]
        if constraints.get("sessions"):
            notes.append("Sessions filter enabled")
        if constraints.get("fresh_only"):
            notes.append("Fresh-only zone filter enabled")
        return notes

    def _estimate_confidence(self, sequence: list[StrategyNode], constraints: dict, unsupported: list[str], matched_registry: bool) -> float:
        score = 0.42 + min(len(sequence), 6) * 0.08
        if matched_registry:
            score += 0.18
        if constraints.get("sessions"):
            score += 0.05
        if constraints.get("fresh_only"):
            score += 0.04
        if unsupported:
            score -= 0.1
        return round(max(0.1, min(score, 0.99)), 2)

    def _apply_preferences(self, spec: StrategySpec) -> StrategySpec:
        prefs = self.preference_store.get()
        if not spec.symbol:
            spec.symbol = prefs.default_symbol
        spec.preferences_applied = []
        return spec

    def _normalize_spec(self, spec: StrategySpec) -> StrategySpec:
        spec.symbol = spec.symbol.upper().replace("=X", "")
        return spec

    def _preview_text(self, spec: StrategySpec) -> str:
        steps = " -> ".join(node.type for node in spec.sequence) if spec.sequence else spec.strategy_key
        return (
            f"{spec.mode.upper()} | {spec.school}/{spec.strategy_key} | {spec.market_label} ({spec.symbol}) | "
            f"{spec.primary_timeframe} -> {spec.execution_timeframe} | lookback {spec.lookback_days}d | direction {spec.direction} | steps: {steps}"
        )

    def _render_strategy_as_prompt(self, strategy: StrategySpec) -> str:
        steps = " ثم ".join(node.type for node in strategy.sequence)
        return f"{strategy.mode} {strategy.school} {strategy.strategy_key} {strategy.symbol} {strategy.primary_timeframe} last {strategy.lookback_days} days {steps}"

    def _tf_minutes(self, timeframe: str) -> int:
        return {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}.get(timeframe, 60)
