from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import md5

import pandas as pd

from app.models import ResearchRunRequest, ResearchRunResponse, SetupResult, StrategyNode
from app.services.market_data import MarketDataError, MarketDataService

SOURCE_TYPES = {"bos", "mss", "order_block", "breaker"}


class ResearchScanner:
    def __init__(self) -> None:
        self.market = MarketDataService()
        self._last_total_candidates = 0

    def run(self, request: ResearchRunRequest) -> ResearchRunResponse:
        self._last_total_candidates = 0
        strategy = request.strategy.model_copy(deep=True)
        strategy.constraints = self._apply_request_scope(strategy.constraints or {}, request)
        htf_report = None
        ltf_report = None
        try:
            htf_raw = self.market.fetch(strategy.symbol, strategy.primary_timeframe, periods=1800)
            htf_report = self.market.get_last_fetch_report()
            ltf_raw = self.market.fetch(strategy.symbol, strategy.execution_timeframe, periods=5000)
            ltf_report = self.market.get_last_fetch_report()
        except MarketDataError as exc:
            failed_report = self.market.get_last_fetch_report()
            return ResearchRunResponse(
                strategy_summary=f"{strategy.market_label}: market data fetch failed",
                human_summary="فشل جلب بيانات السوق الحقيقية، لذلك لم يتم تنفيذ الفحص.",
                supported=False,
                unsupported_reasons=[str(exc)],
                total_candidates=0,
                total_qualified=0,
                setups=[],
                executed_plan=[n.type for n in strategy.sequence],
                diagnostics={
                    "notes": ["No demo data fallback was used."],
                    "market_data_error": str(exc),
                    "market_data": {"failed_request": failed_report},
                },
                stats={},
                search_scope=self._build_scope(strategy, pd.DataFrame(), pd.DataFrame()),
                highlights=["تأكد من الرمز، الاتصال، أو مزود البيانات."],
                warnings=["Real market data is unavailable."],
            )

        htf = self.market.trim_to_window(htf_raw, strategy.constraints)
        ltf = self.market.trim_to_window(ltf_raw, strategy.constraints)
        if htf.empty or ltf.empty:
            return ResearchRunResponse(
                strategy_summary=f"{strategy.market_label}: no market data in requested scope",
                human_summary="لم أجد بيانات ضمن نطاق الباك تست المطلوب.",
                supported=False,
                unsupported_reasons=["No market data in requested scope"],
                total_candidates=0,
                total_qualified=0,
                setups=[],
                executed_plan=[n.type for n in strategy.sequence],
                diagnostics={},
                stats={},
                search_scope=self._build_scope(strategy, htf, ltf),
                highlights=["جرّب توسيع نطاق الباك تست أو إزالة فلتر الجلسة."],
                warnings=["Requested backtest window returned no candles."],
            )

        htf = self._prepare(htf, request.source_lookback)
        ltf = self._prepare(ltf, request.mss_lookback)

        if strategy.strategy_key == "snr_fresh_reaction_fvg_poi":
            results, diagnostics = self._scan_snr_reaction_fvg_poi(strategy, htf, ltf, request)
            results.sort(key=lambda item: (item.quality_score, item.outcome_move_points, -item.adverse_move_points), reverse=True)
            stats = self._build_stats(results)
            scope = self._build_scope(strategy, htf, ltf)
            diagnostics["market_data"] = {"htf": htf_report, "ltf": ltf_report}
            summary = f"{strategy.market_label}: {len(results)} composite setups from {self._last_total_candidates} fresh S&R candidates"
            return ResearchRunResponse(
                strategy_summary=summary,
                human_summary=self._human_summary(strategy, results, stats, diagnostics, scope),
                supported=True,
                unsupported_reasons=list(strategy.unsupported_phrases),
                total_candidates=self._last_total_candidates,
                total_qualified=len(results),
                setups=results[: request.max_setups],
                executed_plan=[n.type for n in strategy.sequence] or [strategy.strategy_key],
                diagnostics=diagnostics,
                stats=stats,
                search_scope=scope,
                highlights=self._build_highlights(results, stats, diagnostics),
                warnings=self._build_warnings(results, scope),
            )

        if strategy.strategy_key in {"fresh_snr", "fresh_supply", "fresh_demand", "level_flip"}:
            results, diagnostics = self._scan_levels(strategy, htf, ltf, request)
            results.sort(key=lambda item: (item.quality_score, item.outcome_move_points, -item.adverse_move_points), reverse=True)
            stats = self._build_stats(results)
            scope = self._build_scope(strategy, htf, ltf)
            diagnostics["market_data"] = {"htf": htf_report, "ltf": ltf_report}
            summary = f"{strategy.market_label}: {len(results)} {strategy.strategy_key} levels from {self._last_total_candidates} candidates"
            return ResearchRunResponse(
                strategy_summary=summary,
                human_summary=self._human_summary(strategy, results, stats, diagnostics, scope),
                supported=True,
                unsupported_reasons=list(strategy.unsupported_phrases),
                total_candidates=self._last_total_candidates,
                total_qualified=len(results),
                setups=results[: request.max_setups],
                executed_plan=[strategy.strategy_key],
                diagnostics=diagnostics,
                stats=stats,
                search_scope=scope,
                highlights=self._build_highlights(results, stats, diagnostics),
                warnings=self._build_warnings(results, scope),
            )

        source_node = self._find_source(strategy.sequence)
        if source_node is None:
            return ResearchRunResponse(
                strategy_summary=f"{strategy.market_label}: no executable source step was found",
                human_summary="الاستراتيجية لا تحتوي على خطوة بداية قابلة للتنفيذ مثل BOS أو MSS أو order block أو breaker.",
                supported=False,
                unsupported_reasons=["Missing a source step such as bos, mss, order_block, or breaker"],
                total_candidates=0,
                total_qualified=0,
                setups=[],
                executed_plan=[n.type for n in strategy.sequence],
                diagnostics={},
                stats={},
                search_scope=self._build_scope(strategy, htf, ltf),
                highlights=[],
            )

        results, diagnostics = self._scan(strategy.sequence, strategy.direction, strategy.constraints, htf, ltf, request)
        if not results and strategy.constraints.get("include_exploratory", True):
            results, exploratory_diag = self._scan_exploratory(source_node, strategy.direction, strategy.constraints, htf, ltf, request)
            diagnostics["notes"].append("Exploratory fallback used because no full chain was found.")
            diagnostics.update({"exploratory": exploratory_diag})

        results.sort(key=lambda item: (item.quality_score, item.outcome_move_points, -item.adverse_move_points), reverse=True)
        stats = self._build_stats(results)
        scope = self._build_scope(strategy, htf, ltf)
        summary = f"{strategy.market_label}: {len(results)} qualified setups from {self._last_total_candidates} focused candidates"
        warnings = self._build_warnings(results, scope)
        diagnostics["market_data"] = {
            "htf": htf_report,
            "ltf": ltf_report,
        }
        return ResearchRunResponse(
            strategy_summary=summary,
            human_summary=self._human_summary(strategy, results, stats, diagnostics, scope),
            supported=True,
            unsupported_reasons=list(strategy.unsupported_phrases),
            total_candidates=self._last_total_candidates,
            total_qualified=len(results),
            setups=results[: request.max_setups],
            executed_plan=[n.type for n in strategy.sequence],
            diagnostics=diagnostics,
            stats=stats,
            search_scope=scope,
            highlights=self._build_highlights(results, stats, diagnostics),
            warnings=warnings,
        )


    def _apply_request_scope(self, constraints: dict, request: ResearchRunRequest) -> dict:
        merged = dict(constraints or {})
        scope = request.backtest_range
        if not scope:
            return merged
        if scope.start:
            merged["backtest_start"] = scope.start
        if scope.end:
            merged["backtest_end"] = scope.end
        if scope.start or scope.end:
            merged["backtest_label"] = scope.label or "custom_range"
        elif scope.label and not merged.get("backtest_label"):
            merged["backtest_label"] = scope.label
        return merged

    def _scan(self, sequence: list[StrategyNode], direction: str, constraints: dict, htf: pd.DataFrame, ltf: pd.DataFrame, request: ResearchRunRequest) -> tuple[list[SetupResult], dict]:
        directions = [direction] if direction in {"bullish", "bearish"} else ["bullish", "bearish"]
        sessions = constraints.get("sessions") or []
        zone_mode = constraints.get("zone_mode", "body_to_wick")
        require_pd = any(node.type == "premium_discount" for node in sequence)
        source_node = self._find_source(sequence)
        post_retest_steps = self._steps_after_retest(sequence)
        confirmation_window = int(constraints.get("confirmation_within_bars") or request.ltf_confirmation_window_bars)
        filtered_htf = self._filter_sessions(htf, sessions).reset_index(drop=True)
        filtered_ltf = self._filter_sessions(ltf, sessions).reset_index(drop=True)
        diagnostics: dict = {
            "requested_steps": [n.type for n in sequence],
            "enabled_filters": {
                "sessions": sessions,
                "zone_mode": zone_mode,
                "reclaim_mode": constraints.get("reclaim_mode"),
                "scan_policy": constraints.get("scan_policy", "focused"),
                "confirmation_within_bars": confirmation_window,
                "backtest_label": constraints.get("backtest_label"),
            },
            "coverage": defaultdict(int),
            "drop_off": defaultdict(int),
            "notes": [],
        }
        results: list[SetupResult] = []
        total_candidates = 0
        max_sources = int(constraints.get("max_source_candidates") or 120)

        for chosen_direction in directions:
            source_hits = 0
            for idx, row in filtered_htf.iterrows():
                if source_hits >= max_sources:
                    diagnostics["notes"].append(f"Source scan capped at {max_sources} candidates for speed.")
                    break
                if not self._is_source_match(source_node, chosen_direction, filtered_htf, idx, bool(constraints.get("require_displacement"))):
                    continue
                source_hits += 1
                total_candidates += 1
                diagnostics["coverage"][f"source:{source_node.type}"] += 1
                source_time = pd.to_datetime(row["timestamp"], utc=True)
                zone_low, zone_high = self._build_zone(row, chosen_direction, zone_mode)
                source_range_points = round(float(row["high"]) - float(row["low"]), 2)

                future = filtered_ltf[filtered_ltf["timestamp"] >= source_time].copy().reset_index(drop=True)
                if future.empty:
                    diagnostics["drop_off"]["no_ltf_after_source"] += 1
                    continue
                future = future.head(max(request.zone_retest_window_bars + request.outcome_window_bars + confirmation_window + 12, 50))
                retest_idx = self._find_retest(future, zone_low, zone_high, request.zone_retest_window_bars, constraints, chosen_direction)
                if retest_idx is None:
                    diagnostics["drop_off"]["no_retest"] += 1
                    continue
                diagnostics["coverage"]["retest_zone"] += 1

                retest_row = future.loc[retest_idx]
                matched: dict[str, pd.Series] = {"retest_zone": retest_row}
                cursor = retest_idx
                chain = [f"retest@{pd.to_datetime(retest_row['timestamp'], utc=True).isoformat()}"]

                pd_state = self._premium_discount_state(future, retest_idx, chosen_direction, request.source_lookback)
                if require_pd:
                    target = "discount" if chosen_direction == "bullish" else "premium"
                    if pd_state != target:
                        diagnostics["drop_off"]["premium_discount_filter"] += 1
                        continue
                    diagnostics["coverage"]["premium_discount"] += 1

                success = True
                missing_steps: list[str] = []
                for step in post_retest_steps:
                    window = future.loc[cursor : cursor + confirmation_window].copy().reset_index(drop=True)
                    local_idx = self._find_step(window, step, chosen_direction, request)
                    if local_idx is None:
                        success = False
                        missing_steps.append(step.type)
                        diagnostics["drop_off"][f"missing_{step.type}"] += 1
                        break
                    actual_idx = cursor + local_idx
                    matched[step.type] = future.loc[actual_idx]
                    cursor = actual_idx
                    diagnostics["coverage"][step.type] += 1
                    chain.append(f"{step.type}@{pd.to_datetime(future.loc[actual_idx, 'timestamp'], utc=True).isoformat()}")
                if not success:
                    continue

                outcome_label, move, adverse, explanation = self._evaluate_outcome(future, cursor, request.outcome_window_bars, chosen_direction)
                quality_score = self._quality_score(outcome_label, move, adverse, matched, require_pd, pd_state)
                explanation = self._augment_explanation(explanation, source_node.type, chosen_direction, zone_low, zone_high, matched, pd_state, missing_steps)
                setup_id = md5(f"{source_time.isoformat()}-{chosen_direction}-{source_node.type}-{zone_low}-{zone_high}".encode()).hexdigest()[:10]
                chart_end = pd.to_datetime(future.loc[min(len(future)-1, cursor + request.outcome_window_bars), "timestamp"], utc=True).isoformat()
                chart_start = pd.to_datetime(future.loc[max(0, retest_idx - 18), "timestamp"], utc=True).isoformat()
                trade_plan = self._build_trade_plan(chosen_direction, float(retest_row["close"]), zone_low, zone_high, execution_model="confirmation_retest")
                results.append(
                    SetupResult(
                        setup_id=setup_id,
                        htf_time=source_time.isoformat(),
                        source_type=source_node.type,
                        source_direction=chosen_direction,
                        session_label=self._session_label(source_time),
                        zone_low=round(zone_low, 3),
                        zone_high=round(zone_high, 3),
                        entry_reference_price=round(float(retest_row["close"]), 3),
                        source_open=round(float(row["open"]), 3),
                        source_high=round(float(row["high"]), 3),
                        source_low=round(float(row["low"]), 3),
                        source_close=round(float(row["close"]), 3),
                        source_range_points=source_range_points,
                        ltf_retest_time=self._ts(retest_row),
                        sweep_time=self._ts(matched.get("liquidity_sweep")),
                        equal_level_time=self._ts(matched.get("equal_highs_lows")),
                        displacement_time=self._ts(matched.get("displacement")),
                        breaker_time=self._ts(matched.get("breaker")),
                        mss_time=self._ts(matched.get("mss")),
                        fvg_time=self._ts(matched.get("fvg")),
                        premium_discount_state=pd_state,
                        outcome_label=outcome_label,
                        outcome_move_points=move,
                        adverse_move_points=adverse,
                        matched_steps_count=len(matched),
                        missing_steps=missing_steps,
                        quality_score=quality_score,
                        quality_label=self._quality_label(quality_score),
                        confirmation_chain=chain,
                        tags=list(dict.fromkeys([source_node.type, chosen_direction, outcome_label, self._quality_label(quality_score)])),
                        chart_focus=self._chart_trade_focus({
                            "start_time": chart_start,
                            "end_time": chart_end,
                            "source_time": source_time.isoformat(),
                            "retest_time": self._ts(retest_row),
                            "zone_low": round(zone_low, 3),
                            "zone_high": round(zone_high, 3),
                            "source_open": round(float(row["open"]), 3),
                            "source_low": round(float(row["low"]), 3),
                            "source_high": round(float(row["high"]), 3),
                            "source_close": round(float(row["close"]), 3),
                            "annotation_title": self._annotation_title(source_node.type, chosen_direction),
                        }, trade_plan),
                        entry_price=trade_plan["entry_price"],
                        stop_loss=trade_plan["stop_loss"],
                        take_profit=trade_plan["take_profit"],
                        risk_points=trade_plan["risk_points"],
                        reward_points=trade_plan["reward_points"],
                        rr_ratio=trade_plan["rr_ratio"],
                        execution_model=trade_plan["execution_model"],
                        execution_notes=trade_plan["execution_notes"],
                        explanation=explanation,
                    )
                )

        self._last_total_candidates = total_candidates
        diagnostics["coverage"] = dict(diagnostics["coverage"])
        diagnostics["drop_off"] = dict(diagnostics["drop_off"])
        return results, diagnostics


    def _scan_levels(self, strategy, htf: pd.DataFrame, ltf: pd.DataFrame, request: ResearchRunRequest) -> tuple[list[SetupResult], dict]:
        directions = [strategy.direction] if strategy.direction in {"bullish", "bearish"} else ["bullish", "bearish"]
        max_touches = int(strategy.constraints.get("max_touches", 0))
        tolerance_ratio = float(strategy.constraints.get("level_tolerance_ratio", 0.18))
        zone_mode = strategy.constraints.get("zone_mode", "body_to_wick")
        results: list[SetupResult] = []
        diagnostics: dict = {"notes": [], "drop_off": defaultdict(int), "coverage": defaultdict(int), "scan_type": strategy.strategy_key}
        for chosen_direction in directions:
            for idx, row in htf.iterrows():
                if idx + 3 >= len(htf):
                    continue
                if not self._is_level_source(strategy.strategy_key, chosen_direction, htf, idx):
                    continue
                self._last_total_candidates += 1
                source_time = pd.to_datetime(row["timestamp"], utc=True)
                zone_low, zone_high = self._build_zone(row, chosen_direction, zone_mode)
                touches, first_touch_time = self._count_future_touches(htf.loc[idx + 1 :], zone_low, zone_high, tolerance_ratio)
                diagnostics["coverage"]["level_candidate"] += 1
                if strategy.strategy_key == "level_flip":
                    flipped = self._is_flipped_level(htf.loc[idx + 1 :].reset_index(drop=True), zone_low, zone_high, chosen_direction)
                    if not flipped:
                        diagnostics["drop_off"]["not_flipped"] += 1
                        continue
                else:
                    if touches > max_touches:
                        diagnostics["drop_off"]["not_fresh"] += 1
                        continue
                future_ltf = ltf[ltf["timestamp"] >= source_time].copy().reset_index(drop=True).head(request.outcome_window_bars + request.zone_retest_window_bars + 24)
                outcome_label, move, adverse, explanation = self._evaluate_outcome(future_ltf if not future_ltf.empty else ltf.tail(1), 0, min(request.outcome_window_bars, max(len(future_ltf) - 1, 0)), chosen_direction)
                quality_score = self._level_quality_score(strategy.strategy_key, touches, move, adverse)
                setup_id = md5(f"level-{strategy.strategy_key}-{source_time.isoformat()}-{chosen_direction}-{zone_low}-{zone_high}".encode()).hexdigest()[:10]
                chart_end = (pd.to_datetime(future_ltf.loc[len(future_ltf)-1, "timestamp"], utc=True).isoformat() if not future_ltf.empty else source_time.isoformat())
                explanation = [
                    f"School strategy: {strategy.school}/{strategy.strategy_key}",
                    f"Fresh touches after source: {touches}",
                    f"First touch after source: {first_touch_time or 'none'}",
                    f"Zone: {round(zone_low, 3)} -> {round(zone_high, 3)}",
                ] + explanation
                tags = [strategy.school, strategy.strategy_key, chosen_direction, "fresh" if touches == 0 else f"touches:{touches}"]
                trade_plan = self._build_trade_plan(chosen_direction, float(row["close"]), zone_low, zone_high, execution_model="fresh_level")
                results.append(
                    SetupResult(
                        setup_id=setup_id,
                        htf_time=source_time.isoformat(),
                        source_type=strategy.strategy_key,
                        source_direction=chosen_direction,
                        session_label=self._session_label(source_time),
                        zone_low=round(zone_low, 3),
                        zone_high=round(zone_high, 3),
                        entry_reference_price=round(float(row["close"]), 3),
                        source_open=round(float(row["open"]), 3),
                        source_high=round(float(row["high"]), 3),
                        source_low=round(float(row["low"]), 3),
                        source_close=round(float(row["close"]), 3),
                        source_range_points=round(float(row["high"]) - float(row["low"]), 2),
                        ltf_retest_time=first_touch_time,
                        outcome_label="fresh_level" if strategy.strategy_key != "level_flip" else "level_flip",
                        outcome_move_points=move,
                        adverse_move_points=adverse,
                        matched_steps_count=1,
                        quality_score=quality_score,
                        quality_label=self._quality_label(quality_score),
                        confirmation_chain=[strategy.strategy_key],
                        tags=tags,
                        chart_focus=self._chart_trade_focus({
                            "start_time": source_time.isoformat(),
                            "end_time": chart_end,
                            "source_time": source_time.isoformat(),
                            "zone_low": round(zone_low, 3),
                            "zone_high": round(zone_high, 3),
                            "annotation_title": self._annotation_title(strategy.strategy_key, chosen_direction),
                        }, trade_plan),
                        entry_price=trade_plan["entry_price"],
                        stop_loss=trade_plan["stop_loss"],
                        take_profit=trade_plan["take_profit"],
                        risk_points=trade_plan["risk_points"],
                        reward_points=trade_plan["reward_points"],
                        rr_ratio=trade_plan["rr_ratio"],
                        execution_model=trade_plan["execution_model"],
                        execution_notes=trade_plan["execution_notes"],
                        explanation=explanation,
                    )
                )
        diagnostics["coverage"] = dict(diagnostics["coverage"])
        diagnostics["drop_off"] = dict(diagnostics["drop_off"])
        results.sort(key=lambda item: (item.quality_score, item.source_range_points), reverse=True)
        return results, diagnostics

    def _is_level_source(self, strategy_key: str, direction: str, htf: pd.DataFrame, idx: int) -> bool:
        row = htf.loc[idx]
        next_rows = htf.loc[idx + 1 : idx + 3]
        if next_rows.empty:
            return False
        bullish_body = row["close"] > row["open"]
        bearish_body = row["close"] < row["open"]
        if strategy_key == "fresh_supply":
            return bearish_body and float(next_rows["low"].min()) < float(row["low"])
        if strategy_key == "fresh_demand":
            return bullish_body and float(next_rows["high"].max()) > float(row["high"])
        if strategy_key in {"fresh_snr", "level_flip"}:
            prior = htf.loc[max(0, idx - 6) : max(0, idx - 1)]
            local_high = float(prior["high"].max()) if not prior.empty else None
            local_low = float(prior["low"].min()) if not prior.empty else None
            if direction == "bullish":
                rolling_high = row.get("rolling_high")
                breakout = (pd.notna(rolling_high) and float(row["close"]) > float(rolling_high)) or (local_high is not None and float(row["close"]) > local_high)
                return bullish_body and breakout
            rolling_low = row.get("rolling_low")
            breakdown = (pd.notna(rolling_low) and float(row["close"]) < float(rolling_low)) or (local_low is not None and float(row["close"]) < local_low)
            return bearish_body and breakdown
        return False

    def _count_future_touches(self, future: pd.DataFrame, zone_low: float, zone_high: float, tolerance_ratio: float) -> tuple[int, str | None]:
        if future.empty:
            return 0, None
        zone_height = max(zone_high - zone_low, 0.01)
        tolerance = max(0.05, zone_height * tolerance_ratio)
        touches = 0
        first_touch_time = None
        for _, row in future.iterrows():
            touched = float(row["high"]) >= (zone_low - tolerance) and float(row["low"]) <= (zone_high + tolerance)
            if touched:
                touches += 1
                if first_touch_time is None:
                    first_touch_time = pd.to_datetime(row["timestamp"], utc=True).isoformat()
        return touches, first_touch_time

    def _is_flipped_level(self, future: pd.DataFrame, zone_low: float, zone_high: float, direction: str) -> bool:
        if future.empty:
            return False
        for _, row in future.iterrows():
            close = float(row["close"])
            if direction == "bullish" and close < zone_low:
                return True
            if direction == "bearish" and close > zone_high:
                return True
        return False

    def _level_quality_score(self, strategy_key: str, touches: int, move: float, adverse: float) -> float:
        base = 74.0 if touches == 0 else max(40.0, 74.0 - touches * 11.0)
        if strategy_key == "level_flip":
            base += 6.0
        base += min(10.0, move * 0.8)
        base -= min(10.0, adverse * 0.7)
        return round(max(0.0, min(100.0, base)), 1)

    def _scan_snr_reaction_fvg_poi(self, strategy, htf: pd.DataFrame, ltf: pd.DataFrame, request: ResearchRunRequest) -> tuple[list[SetupResult], dict]:
        directions = [strategy.direction] if strategy.direction in {"bullish", "bearish"} else ["bullish", "bearish"]
        tolerance_ratio = float(strategy.constraints.get("level_tolerance_ratio", 0.18))
        reaction_window = int(strategy.constraints.get("confirmation_within_bars") or request.ltf_confirmation_window_bars)
        max_touches = int(strategy.constraints.get("max_touches", 0))
        diagnostics: dict = {
            "notes": ["Composite strategy: fresh S&R -> reaction -> FVG -> POI"],
            "drop_off": defaultdict(int),
            "coverage": defaultdict(int),
            "scan_type": strategy.strategy_key,
        }
        results: list[SetupResult] = []
        total_candidates = 0

        for chosen_direction in directions:
            for idx, row in htf.iterrows():
                if idx + 3 >= len(htf):
                    continue
                if not self._is_level_source("fresh_snr", chosen_direction, htf, idx):
                    continue
                total_candidates += 1
                source_time = pd.to_datetime(row["timestamp"], utc=True)
                zone_low, zone_high = self._build_zone(row, chosen_direction, strategy.constraints.get("zone_mode", "body_to_wick"))
                touches, first_touch_time = self._count_future_touches(htf.loc[idx + 1 :], zone_low, zone_high, tolerance_ratio)
                diagnostics["coverage"]["fresh_snr_candidate"] += 1
                if touches > max_touches:
                    diagnostics["drop_off"]["not_fresh"] += 1
                    continue

                future_ltf = ltf[ltf["timestamp"] >= source_time].copy().reset_index(drop=True)
                if future_ltf.empty:
                    diagnostics["drop_off"]["no_ltf_window"] += 1
                    continue
                reaction_idx = self._find_level_reaction(future_ltf, zone_low, zone_high, chosen_direction, request.zone_retest_window_bars)
                if reaction_idx is None:
                    diagnostics["drop_off"]["no_reaction"] += 1
                    continue
                diagnostics["coverage"]["reaction"] += 1
                reaction_row = future_ltf.loc[reaction_idx]

                fvg_window = future_ltf.loc[max(0, reaction_idx - 1) : reaction_idx + reaction_window].copy().reset_index(drop=True)
                fvg_idx, fvg_bounds = self._find_fvg_with_bounds(fvg_window, chosen_direction)
                if fvg_idx is None or fvg_bounds is None:
                    diagnostics["drop_off"]["no_fvg"] += 1
                    continue
                diagnostics["coverage"]["fvg"] += 1
                fvg_row = fvg_window.loc[fvg_idx]
                actual_fvg_idx = max(0, reaction_idx - 1) + fvg_idx
                poi_entry, stop_loss = self._derive_poi_prices(chosen_direction, zone_low, zone_high, fvg_bounds)
                outcome_label, move, adverse, explanation = self._evaluate_outcome(future_ltf, min(len(future_ltf)-1, actual_fvg_idx), request.outcome_window_bars, chosen_direction)

                matched = {"retest_zone": reaction_row, "fvg": fvg_row}
                quality_score = min(100.0, self._quality_score(outcome_label, move, adverse, matched, False, None) + (8.0 if touches == 0 else 0.0))
                setup_id = md5(f"composite-{source_time.isoformat()}-{chosen_direction}-{zone_low}-{zone_high}-{poi_entry}".encode()).hexdigest()[:10]
                chart_start = pd.to_datetime(future_ltf.loc[max(0, reaction_idx - 18), "timestamp"], utc=True).isoformat()
                chart_end = pd.to_datetime(future_ltf.loc[min(len(future_ltf)-1, actual_fvg_idx + request.outcome_window_bars), "timestamp"], utc=True).isoformat()
                trade_plan = self._build_trade_plan(chosen_direction, poi_entry, zone_low, zone_high, stop_hint=stop_loss, execution_model="poi_from_fvg")
                explanation = [
                    f"Composite setup detected on {strategy.primary_timeframe} -> {strategy.execution_timeframe}",
                    f"Level freshness touches: {touches}",
                    f"First higher-timeframe touch after source: {first_touch_time or 'none'}",
                    f"Reaction candle at {pd.to_datetime(reaction_row['timestamp'], utc=True).isoformat()}",
                    f"FVG bounds: {round(fvg_bounds[0], 3)} -> {round(fvg_bounds[1], 3)}",
                    f"POI entry: {round(poi_entry, 3)}",
                    f"Suggested stop: {round(stop_loss, 3)}",
                ] + explanation
                results.append(
                    SetupResult(
                        setup_id=setup_id,
                        htf_time=source_time.isoformat(),
                        source_type=strategy.strategy_key,
                        source_direction=chosen_direction,
                        session_label=self._session_label(source_time),
                        zone_low=round(zone_low, 3),
                        zone_high=round(zone_high, 3),
                        entry_reference_price=round(float(poi_entry), 3),
                        source_open=round(float(row["open"]), 3),
                        source_high=round(float(row["high"]), 3),
                        source_low=round(float(row["low"]), 3),
                        source_close=round(float(row["close"]), 3),
                        source_range_points=round(float(row["high"])-float(row["low"]), 2),
                        ltf_retest_time=self._ts(reaction_row),
                        fvg_time=self._ts(fvg_row),
                        outcome_label=outcome_label,
                        outcome_move_points=move,
                        adverse_move_points=adverse,
                        matched_steps_count=3,
                        quality_score=round(quality_score, 1),
                        quality_label=self._quality_label(quality_score),
                        confirmation_chain=[
                            f"fresh_snr@{source_time.isoformat()}",
                            f"reaction@{pd.to_datetime(reaction_row['timestamp'], utc=True).isoformat()}",
                            f"fvg@{pd.to_datetime(fvg_row['timestamp'], utc=True).isoformat()}",
                            f"poi@{round(poi_entry, 3)}",
                        ],
                        tags=[strategy.school, strategy.strategy_key, chosen_direction, "poi", "fvg", "fresh"],
                        chart_focus=self._chart_trade_focus({
                            "start_time": chart_start,
                            "end_time": chart_end,
                            "source_time": source_time.isoformat(),
                            "retest_time": self._ts(reaction_row),
                            "zone_low": round(zone_low, 3),
                            "zone_high": round(zone_high, 3),
                            "poi_entry": round(poi_entry, 3),
                            "stop_loss": round(stop_loss, 3),
                            "fvg_low": round(fvg_bounds[0], 3),
                            "fvg_high": round(fvg_bounds[1], 3),
                            "annotation_title": self._annotation_title("snr_fvg_poi", chosen_direction),
                        }, trade_plan),
                        entry_price=trade_plan["entry_price"],
                        stop_loss=trade_plan["stop_loss"],
                        take_profit=trade_plan["take_profit"],
                        risk_points=trade_plan["risk_points"],
                        reward_points=trade_plan["reward_points"],
                        rr_ratio=trade_plan["rr_ratio"],
                        execution_model=trade_plan["execution_model"],
                        execution_notes=trade_plan["execution_notes"],
                        explanation=explanation,
                    )
                )
        self._last_total_candidates = total_candidates
        diagnostics["coverage"] = dict(diagnostics["coverage"])
        diagnostics["drop_off"] = dict(diagnostics["drop_off"])
        return results, diagnostics

    def _find_level_reaction(self, df: pd.DataFrame, zone_low: float, zone_high: float, direction: str, window: int) -> int | None:
        for idx, row in df.head(window).iterrows():
            touched = float(row["high"]) >= zone_low and float(row["low"]) <= zone_high
            if not touched:
                continue
            body = abs(float(row["close"]) - float(row["open"]))
            candle_range = max(0.01, float(row["high"]) - float(row["low"]))
            body_ratio = body / candle_range
            if direction == "bullish" and float(row["close"]) >= zone_high and body_ratio >= 0.2:
                return int(idx)
            if direction == "bearish" and float(row["close"]) <= zone_low and body_ratio >= 0.2:
                return int(idx)
        return None

    def _find_fvg_with_bounds(self, df: pd.DataFrame, direction: str) -> tuple[int | None, tuple[float, float] | None]:
        work = df.copy().reset_index(drop=True)
        for idx in range(2, len(work)):
            r1 = work.loc[idx - 2]
            r3 = work.loc[idx]
            if direction == "bullish" and float(r3["low"]) > float(r1["high"]):
                return int(idx), (float(r1["high"]), float(r3["low"]))
            if direction == "bearish" and float(r3["high"]) < float(r1["low"]):
                return int(idx), (float(r3["high"]), float(r1["low"]))
        return None, None

    def _derive_poi_prices(self, direction: str, zone_low: float, zone_high: float, fvg_bounds: tuple[float, float]) -> tuple[float, float]:
        fvg_low, fvg_high = fvg_bounds
        poi = fvg_low + ((fvg_high - fvg_low) / 2.0)
        if direction == "bullish":
            stop = min(zone_low, fvg_low) - 0.05
        else:
            stop = max(zone_high, fvg_high) + 0.05
        return round(poi, 5), round(stop, 5)

    def _build_trade_plan(self, direction: str, entry: float, zone_low: float, zone_high: float, *, stop_hint: float | None = None, target_hint: float | None = None, execution_model: str = "zone_retest") -> dict[str, float | str | list[str]]:
        entry_value = round(float(entry), 3)
        if stop_hint is None:
            stop_value = round((zone_low - 0.08) if direction == "bullish" else (zone_high + 0.08), 3)
        else:
            stop_value = round(float(stop_hint), 3)
        risk = round(abs(entry_value - stop_value), 3)
        if risk <= 0:
            risk = 0.05
            stop_value = round(entry_value - risk, 3) if direction == "bullish" else round(entry_value + risk, 3)
        if target_hint is None:
            target_value = round(entry_value + (risk * 2.0), 3) if direction == "bullish" else round(entry_value - (risk * 2.0), 3)
        else:
            target_value = round(float(target_hint), 3)
        reward = round(abs(target_value - entry_value), 3)
        rr_ratio = round(reward / risk, 2) if risk > 0 else None
        notes = [
            f"Entry model: {execution_model}",
            f"Risk approx: {risk} points",
            f"Reward approx: {reward} points",
        ]
        return {
            "entry_price": entry_value,
            "stop_loss": stop_value,
            "take_profit": target_value,
            "risk_points": risk,
            "reward_points": reward,
            "rr_ratio": rr_ratio,
            "execution_model": execution_model,
            "execution_notes": notes,
        }

    def _chart_trade_focus(self, focus: dict, trade_plan: dict) -> dict:
        merged = dict(focus or {})
        for key in ("entry_price", "stop_loss", "take_profit", "risk_points", "reward_points", "rr_ratio", "execution_model"):
            if trade_plan.get(key) is not None:
                merged[key] = trade_plan[key]
        return merged

    def _scan_exploratory(self, source_node: StrategyNode, direction: str, constraints: dict, htf: pd.DataFrame, ltf: pd.DataFrame, request: ResearchRunRequest) -> tuple[list[SetupResult], dict]:
        directions = [direction] if direction in {"bullish", "bearish"} else ["bullish", "bearish"]
        sessions = constraints.get("sessions") or []
        zone_mode = constraints.get("zone_mode", "body_to_wick")
        filtered_htf = self._filter_sessions(htf, sessions).reset_index(drop=True)
        filtered_ltf = self._filter_sessions(ltf, sessions).reset_index(drop=True)
        results: list[SetupResult] = []
        total_candidates = 0
        for chosen_direction in directions:
            for idx, row in filtered_htf.iterrows():
                if not self._is_source_match(source_node, chosen_direction, filtered_htf, idx, False):
                    continue
                total_candidates += 1
                source_time = pd.to_datetime(row["timestamp"], utc=True)
                zone_low, zone_high = self._build_zone(row, chosen_direction, zone_mode)
                future = filtered_ltf[filtered_ltf["timestamp"] >= source_time].copy().reset_index(drop=True).head(request.zone_retest_window_bars + request.outcome_window_bars + 12)
                if future.empty:
                    continue
                retest_idx = self._find_retest(future, zone_low, zone_high, request.zone_retest_window_bars, constraints, chosen_direction)
                if retest_idx is None:
                    continue
                retest_row = future.loc[retest_idx]
                outcome_label, move, adverse, explanation = self._evaluate_outcome(future, retest_idx, request.outcome_window_bars, chosen_direction)
                quality_score = max(20.0, self._quality_score("exploratory_match", move, adverse, {"retest_zone": retest_row}, False, None) - 10.0)
                setup_id = md5(f"exploratory-{source_time.isoformat()}-{chosen_direction}-{zone_low}-{zone_high}".encode()).hexdigest()[:10]
                trade_plan = self._build_trade_plan(chosen_direction, float(retest_row["close"]), zone_low, zone_high, execution_model="exploratory_retest")
                results.append(
                    SetupResult(
                        setup_id=setup_id,
                        htf_time=source_time.isoformat(),
                        source_type=source_node.type,
                        source_direction=chosen_direction,
                        session_label=self._session_label(source_time),
                        zone_low=round(zone_low, 3),
                        zone_high=round(zone_high, 3),
                        entry_reference_price=round(float(retest_row["close"]), 3),
                        source_open=round(float(row["open"]), 3),
                        source_high=round(float(row["high"]), 3),
                        source_low=round(float(row["low"]), 3),
                        source_close=round(float(row["close"]), 3),
                        source_range_points=round(float(row["high"]) - float(row["low"]), 2),
                        ltf_retest_time=self._ts(retest_row),
                        premium_discount_state=self._premium_discount_state(future, retest_idx, chosen_direction, request.source_lookback),
                        outcome_label="exploratory_match",
                        outcome_move_points=move,
                        adverse_move_points=adverse,
                        matched_steps_count=1,
                        quality_score=quality_score,
                        quality_label=self._quality_label(quality_score),
                        confirmation_chain=["retest only"],
                        tags=["exploratory", source_node.type, chosen_direction],
                        chart_focus=self._chart_trade_focus({
                            "start_time": pd.to_datetime(future.loc[max(0, retest_idx - 18), "timestamp"], utc=True).isoformat(),
                            "end_time": self._ts(retest_row),
                            "source_time": source_time.isoformat(),
                            "retest_time": self._ts(retest_row),
                            "zone_low": round(zone_low, 3),
                            "zone_high": round(zone_high, 3),
                            "source_open": round(float(row["open"]), 3),
                            "source_low": round(float(row["low"]), 3),
                            "source_high": round(float(row["high"]), 3),
                            "source_close": round(float(row["close"]), 3),
                            "annotation_title": self._annotation_title(source_node.type, chosen_direction),
                        }, trade_plan),
                        explanation=["Source + zone + retest matched, but the full confirmation chain did not complete."] + explanation,
                    )
                )
        self._last_total_candidates = max(self._last_total_candidates, total_candidates)
        results.sort(key=lambda item: (item.quality_score, item.outcome_move_points), reverse=True)
        return results, {"total_candidates": total_candidates, "returned": len(results)}

    def _prepare(self, df: pd.DataFrame, lookback: int) -> pd.DataFrame:
        work = df.copy().reset_index(drop=True)
        work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True)
        work["rolling_high"] = work["high"].shift(1).rolling(lookback).max()
        work["rolling_low"] = work["low"].shift(1).rolling(lookback).min()
        work["body_size"] = (work["close"] - work["open"]).abs()
        work["range_size"] = (work["high"] - work["low"]).clip(lower=0.0001)
        work["body_ratio"] = work["body_size"] / work["range_size"]
        work["avg_body"] = work["body_size"].rolling(max(3, lookback)).mean().bfill()
        return work

    def _filter_sessions(self, df: pd.DataFrame, sessions: list[str]) -> pd.DataFrame:
        if not sessions:
            return df
        session_hours = {"London": range(7, 12), "New York": range(12, 17), "Asia": range(0, 7)}
        allowed = set()
        for name in sessions:
            allowed.update(session_hours.get(name, []))
        if not allowed:
            return df
        work = df.copy()
        work["hour_utc"] = work["timestamp"].dt.hour
        return work[work["hour_utc"].isin(allowed)].drop(columns=["hour_utc"])

    def _find_source(self, sequence: list[StrategyNode]) -> StrategyNode | None:
        for node in sequence:
            if node.type in SOURCE_TYPES:
                return node
        return None

    def _steps_after_retest(self, sequence: list[StrategyNode]) -> list[StrategyNode]:
        seen_retest = False
        result: list[StrategyNode] = []
        for node in sequence:
            if node.type == "retest_zone":
                seen_retest = True
                continue
            if seen_retest and node.type in {"liquidity_sweep", "mss", "fvg", "displacement", "breaker", "equal_highs_lows", "premium_discount"}:
                result.append(node)
        return result

    def _is_source_match(self, source_node: StrategyNode, direction: str, htf: pd.DataFrame, idx: int, require_displacement: bool) -> bool:
        row = htf.loc[idx]
        bullish_body = row["close"] > row["open"]
        bearish_body = row["close"] < row["open"]
        if source_node.type == "bos":
            if direction == "bullish" and not bullish_body:
                return False
            if direction == "bearish" and not bearish_body:
                return False
            return self._breaks_structure(direction, row) and (not require_displacement or self._is_displacement_row(row))
        if source_node.type == "mss":
            return self._breaks_structure(direction, row) and (not require_displacement or self._is_displacement_row(row))
        if source_node.type == "order_block":
            if idx + 3 >= len(htf):
                return False
            future = htf.loc[idx + 1 : idx + 3]
            if direction == "bullish" and bullish_body:
                return False
            if direction == "bearish" and bearish_body:
                return False
            displaced = future["close"].max() > row["high"] if direction == "bullish" else future["close"].min() < row["low"]
            return bool(displaced)
        if source_node.type == "breaker":
            return self._is_breaker_source(htf, idx, direction, require_displacement)
        return False

    def _is_breaker_source(self, htf: pd.DataFrame, idx: int, direction: str, require_displacement: bool) -> bool:
        if idx < 1:
            return False
        row = htf.loc[idx]
        prev = htf.loc[idx - 1]
        cond = self._breaks_structure(direction, row)
        if direction == "bullish":
            cond = cond and prev["close"] < prev["open"]
        else:
            cond = cond and prev["close"] > prev["open"]
        if require_displacement:
            cond = cond and self._is_displacement_row(row)
        return bool(cond)

    def _breaks_structure(self, direction: str, row: pd.Series) -> bool:
        if direction == "bullish":
            return pd.notna(row.get("rolling_high")) and row["close"] > row["rolling_high"]
        return pd.notna(row.get("rolling_low")) and row["close"] < row["rolling_low"]

    def _build_zone(self, row: pd.Series, direction: str, mode: str) -> tuple[float, float]:
        low = float(row["low"])
        high = float(row["high"])
        body_low = float(min(row["open"], row["close"]))
        body_high = float(max(row["open"], row["close"]))
        if direction == "bullish":
            if mode == "full_wick":
                return low, body_high
            if mode == "half_wick":
                return low, round((low + body_low) / 2, 5)
            return low, body_low
        if mode == "full_wick":
            return body_low, high
        if mode == "half_wick":
            return round((high + body_high) / 2, 5), high
        return body_high, high

    def _find_retest(self, df: pd.DataFrame, zone_low: float, zone_high: float, window: int, constraints: dict, direction: str) -> int | None:
        reclaim_mode = constraints.get("reclaim_mode")
        for idx, row in df.head(window).iterrows():
            touched = float(row["high"]) >= zone_low and float(row["low"]) <= zone_high
            if not touched:
                continue
            if reclaim_mode == "sweep_below_close_above":
                if float(row["low"]) < zone_low and float(row["close"]) > zone_low:
                    return int(idx)
                continue
            if reclaim_mode == "sweep_above_close_below":
                if float(row["high"]) > zone_high and float(row["close"]) < zone_high:
                    return int(idx)
                continue
            return int(idx)
        return None

    def _find_step(self, df: pd.DataFrame, step: StrategyNode, direction: str, request: ResearchRunRequest) -> int | None:
        if step.type == "liquidity_sweep":
            return self._find_sweep(df, direction, request.mss_lookback, step.params)
        if step.type == "mss":
            return self._find_mss(df, direction, request.mss_lookback)
        if step.type == "fvg":
            return self._find_fvg(df, direction)
        if step.type == "displacement":
            return self._find_displacement(df, direction)
        if step.type == "equal_highs_lows":
            return self._find_equal_levels(df, direction, step.params)
        if step.type == "premium_discount":
            return self._find_premium_discount(df, direction, request.source_lookback)
        if step.type == "breaker":
            return self._find_breaker_confirmation(df, direction, request.mss_lookback)
        return None

    def _find_sweep(self, df: pd.DataFrame, direction: str, lookback: int, params: dict | None = None) -> int | None:
        work = self._prepare(df, lookback)
        reclaim_mode = (params or {}).get("reclaim_mode")
        for idx, row in work.iterrows():
            if direction == "bullish":
                hit = pd.notna(row["rolling_low"]) and row["low"] < row["rolling_low"]
                if not hit:
                    continue
                if reclaim_mode == "sweep_below_close_above":
                    if row["close"] > row["rolling_low"]:
                        return int(idx)
                elif row["close"] > row["rolling_low"]:
                    return int(idx)
            else:
                hit = pd.notna(row["rolling_high"]) and row["high"] > row["rolling_high"]
                if not hit:
                    continue
                if reclaim_mode == "sweep_above_close_below":
                    if row["close"] < row["rolling_high"]:
                        return int(idx)
                elif row["close"] < row["rolling_high"]:
                    return int(idx)
        return None

    def _find_mss(self, df: pd.DataFrame, direction: str, lookback: int) -> int | None:
        work = self._prepare(df, lookback)
        for idx, row in work.iterrows():
            if direction == "bullish" and pd.notna(row["rolling_high"]) and row["close"] > row["rolling_high"]:
                return int(idx)
            if direction == "bearish" and pd.notna(row["rolling_low"]) and row["close"] < row["rolling_low"]:
                return int(idx)
        return None

    def _find_fvg(self, df: pd.DataFrame, direction: str) -> int | None:
        work = df.copy().reset_index(drop=True)
        for idx in range(2, len(work)):
            r1 = work.loc[idx - 2]
            r3 = work.loc[idx]
            if direction == "bullish" and float(r3["low"]) > float(r1["high"]):
                return int(idx)
            if direction == "bearish" and float(r3["high"]) < float(r1["low"]):
                return int(idx)
        return None

    def _find_displacement(self, df: pd.DataFrame, direction: str) -> int | None:
        work = self._prepare(df, 6)
        for idx, row in work.iterrows():
            if not self._is_displacement_row(row):
                continue
            if direction == "bullish" and row["close"] > row["open"]:
                return int(idx)
            if direction == "bearish" and row["close"] < row["open"]:
                return int(idx)
        return None

    def _find_equal_levels(self, df: pd.DataFrame, direction: str, params: dict | None = None) -> int | None:
        work = df.copy().reset_index(drop=True)
        if len(work) < 3:
            return None
        tolerance = max(0.35, float((work["high"] - work["low"]).tail(10).mean()) * 0.12)
        side = (params or {}).get("side")
        for idx in range(1, len(work)):
            prev = work.loc[idx - 1]
            row = work.loc[idx]
            if direction == "bullish" and side != "highs":
                if abs(float(row["low"]) - float(prev["low"])) <= tolerance:
                    return int(idx)
            if direction == "bearish" and side != "lows":
                if abs(float(row["high"]) - float(prev["high"])) <= tolerance:
                    return int(idx)
            if side == "lows" and abs(float(row["low"]) - float(prev["low"])) <= tolerance:
                return int(idx)
            if side == "highs" and abs(float(row["high"]) - float(prev["high"])) <= tolerance:
                return int(idx)
        return None

    def _find_premium_discount(self, df: pd.DataFrame, direction: str, lookback: int) -> int | None:
        for idx in range(len(df)):
            state = self._premium_discount_state(df, idx, direction, lookback)
            if state == ("discount" if direction == "bullish" else "premium"):
                return int(idx)
        return None

    def _find_breaker_confirmation(self, df: pd.DataFrame, direction: str, lookback: int) -> int | None:
        sweep_idx = self._find_sweep(df, direction, lookback)
        if sweep_idx is None:
            return None
        post = df.loc[sweep_idx:].reset_index(drop=True)
        mss_idx = self._find_mss(post, direction, lookback)
        return None if mss_idx is None else sweep_idx + mss_idx

    def _premium_discount_state(self, df: pd.DataFrame, idx: int, direction: str, lookback: int) -> str | None:
        start = max(0, idx - max(lookback, 6))
        segment = df.loc[start:idx]
        if segment.empty:
            return None
        high = float(segment["high"].max())
        low = float(segment["low"].min())
        midpoint = low + ((high - low) / 2.0)
        close = float(df.loc[idx, "close"])
        if close < midpoint:
            return "discount"
        if close > midpoint:
            return "premium"
        return "equilibrium"

    def _evaluate_outcome(self, ltf: pd.DataFrame, start_idx: int, window: int, direction: str):
        start_row = ltf.loc[start_idx]
        entry = float(start_row["close"])
        future = ltf.loc[start_idx : min(len(ltf) - 1, start_idx + window)]
        if future.empty:
            return ("unclear", 0.0, 0.0, ["No outcome window available"])
        max_high = float(future["high"].max())
        min_low = float(future["low"].min())
        move = round(max_high - entry, 2) if direction == "bullish" else round(entry - min_low, 2)
        adverse = round(entry - min_low, 2) if direction == "bullish" else round(max_high - entry, 2)
        rr = round(move / adverse, 2) if adverse > 0 else 99.0
        if move >= max(4.0, adverse * 2.0):
            label = "strong_reaction"
        elif move > adverse:
            label = "usable_reaction"
        elif adverse >= max(3.0, move * 1.5):
            label = "failed_fast"
        else:
            label = "unclear"
        explanation = [f"Reference price: {round(entry, 3)}", f"Best move: {move}", f"Worst adverse move: {adverse}", f"Move/adverse ratio: {rr}"]
        return label, move, adverse, explanation

    def _quality_score(self, label: str, move: float, adverse: float, matched: dict[str, pd.Series], require_pd: bool, pd_state: str | None) -> float:
        score = 42.0
        score += {"strong_reaction": 34.0, "usable_reaction": 18.0, "unclear": 4.0, "failed_fast": -16.0, "exploratory_match": 0.0}.get(label, 0.0)
        score += min(18.0, move)
        score -= min(18.0, adverse * 1.2)
        for bonus_step, bonus in [("retest_zone", 5.0), ("liquidity_sweep", 6.0), ("equal_highs_lows", 5.0), ("displacement", 7.0), ("breaker", 7.0), ("mss", 8.0), ("fvg", 8.0)]:
            if bonus_step in matched:
                score += bonus
        if require_pd and pd_state in {"discount", "premium"}:
            score += 5.0
        return round(max(0.0, min(100.0, score)), 1)

    def _quality_label(self, score: float) -> str:
        if score >= 82:
            return "A"
        if score >= 68:
            return "B"
        if score >= 52:
            return "C"
        return "D"

    def _augment_explanation(self, explanation: list[str], source_type: str, direction: str, zone_low: float, zone_high: float, matched: dict[str, pd.Series], premium_discount_state: str | None, missing_steps: list[str]) -> list[str]:
        lines = [
            f"Source: {source_type} ({direction})",
            f"Zone: {round(zone_low,3)} → {round(zone_high,3)}",
            f"Verification levels: open={round(zone_high if direction == 'bullish' else zone_low, 3)} / wick edge={round(zone_low if direction == 'bullish' else zone_high, 3)}",
        ]
        if premium_discount_state:
            lines.append(f"PD state: {premium_discount_state}")
        for key in ["retest_zone", "liquidity_sweep", "equal_highs_lows", "displacement", "breaker", "mss", "fvg"]:
            row = matched.get(key)
            if row is not None:
                lines.append(f"{key} at {pd.to_datetime(row['timestamp'], utc=True).isoformat()}")
        if missing_steps:
            lines.append("Missing: " + ", ".join(missing_steps))
        return lines + explanation

    def _build_stats(self, results: list[SetupResult]) -> dict:
        labels = Counter(item.outcome_label for item in results)
        qualities = Counter(item.quality_label for item in results)
        sessions = Counter(item.session_label for item in results)
        source_types = Counter(item.source_type for item in results)
        avg_move = round(sum(item.outcome_move_points for item in results) / len(results), 2) if results else 0.0
        avg_adverse = round(sum(item.adverse_move_points for item in results) / len(results), 2) if results else 0.0
        avg_quality = round(sum(item.quality_score for item in results) / len(results), 1) if results else 0.0
        best = results[0].model_dump() if results else None
        trades_count = len(results)
        wins = [item for item in results if item.outcome_move_points > item.adverse_move_points]
        losses = [item for item in results if item.outcome_move_points <= item.adverse_move_points]
        win_rate = round((len(wins) / trades_count) * 100, 2) if trades_count else 0.0
        r_values = [round((item.outcome_move_points / max(item.adverse_move_points, 0.01)), 2) for item in results]
        avg_r = round(sum(r_values) / len(r_values), 2) if r_values else 0.0
        gross_profit = sum(item.outcome_move_points for item in wins)
        gross_loss = sum(item.adverse_move_points for item in losses)
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (round(gross_profit, 2) if gross_profit > 0 else 0.0)
        equity = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for item in results:
            equity += item.outcome_move_points - item.adverse_move_points
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
        return {
            "labels": dict(labels),
            "quality_buckets": dict(qualities),
            "sessions": dict(sessions),
            "source_types": dict(source_types),
            "avg_move_points": avg_move,
            "avg_adverse_points": avg_adverse,
            "avg_quality_score": avg_quality,
            "trades_count": trades_count,
            "win_rate": win_rate,
            "average_r": avg_r,
            "profit_factor": profit_factor,
            "max_drawdown": round(max_drawdown, 2),
            "best_setup": best,
        }

    def _build_scope(self, strategy, htf: pd.DataFrame, ltf: pd.DataFrame) -> dict:
        constraints = strategy.constraints or {}
        return {
            "backtest_label": constraints.get("backtest_label", "latest_available"),
            "backtest_start": constraints.get("backtest_start"),
            "backtest_end": constraints.get("backtest_end"),
            "primary_timeframe": strategy.primary_timeframe,
            "execution_timeframe": strategy.execution_timeframe,
            "sessions": constraints.get("sessions") or ["all"],
            "htf_bars": int(len(htf)),
            "ltf_bars": int(len(ltf)),
            "scan_policy": constraints.get("scan_policy", "focused"),
        }

    def _build_warnings(self, results: list[SetupResult], scope: dict) -> list[str]:
        warnings: list[str] = []
        if scope.get("htf_bars", 0) < 50 or scope.get("ltf_bars", 0) < 100:
            warnings.append("Data sample is small; backtest statistics may be unstable.")
        if len(results) < 3:
            warnings.append("Very few setups were found; treat the result as exploratory.")
        return warnings

    def _build_highlights(self, results: list[SetupResult], stats: dict, diagnostics: dict) -> list[str]:
        if not results:
            return ["لم تظهر فرص مكتملة ضمن الفلاتر الحالية."]
        best = results[0]
        lines = [f"أفضل نتيجة: {best.quality_label} score {best.quality_score} مع {best.outcome_label} وحركة {best.outcome_move_points} نقطة."]
        if stats.get("sessions"):
            top_session = max(stats["sessions"].items(), key=lambda kv: kv[1])[0]
            lines.append(f"أكثر جلسة ظهر فيها التطابق: {top_session}.")
        drop_off = diagnostics.get("drop_off") or {}
        if drop_off:
            top_drop = max(drop_off.items(), key=lambda kv: kv[1])
            lines.append(f"أكبر سبب إسقاط: {top_drop[0]} ({top_drop[1]}).")
        return lines

    def _human_summary(self, strategy, results: list[SetupResult], stats: dict, diagnostics: dict, scope: dict) -> str:
        if not results:
            return f"فحصت {scope['htf_bars']} شمعة على {strategy.primary_timeframe} و {scope['ltf_bars']} شمعة على {strategy.execution_timeframe} ضمن {scope['backtest_label']}. لم أجد سلسلة مطابقة كاملة للفلاتر الحالية."
        best = results[0]
        return (
            f"تم فحص {scope['backtest_label']} بطريقة focused. ظهر {len(results)} setup مطابق من أصل {self._last_total_candidates} مرشحين. "
            f"أفضل setup كان {best.source_type} {best.source_direction}، جودته {best.quality_label} ({best.quality_score})، "
            f"وأعطى حركة {best.outcome_move_points} مقابل adverse {best.adverse_move_points}."
        )

    def _session_label(self, ts: pd.Timestamp) -> str:
        hour = pd.to_datetime(ts, utc=True).hour
        if 7 <= hour < 12:
            return "London"
        if 12 <= hour < 17:
            return "New York"
        return "Asia/Other"

    def _ts(self, row: pd.Series | None) -> str | None:
        if row is None:
            return None
        return pd.to_datetime(row["timestamp"], utc=True).isoformat()


    def _annotation_title(self, source_type: str, direction: str) -> str:
        return f"{source_type.upper()} {'bullish' if direction == 'bullish' else 'bearish'} zone"

    def _is_displacement_row(self, row: pd.Series) -> bool:
        return bool(float(row.get("body_ratio", 0.0)) >= 0.6 and float(row.get("body_size", 0.0)) >= float(row.get("avg_body", 0.0)) * 1.35)
