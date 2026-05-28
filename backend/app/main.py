from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
import sqlite3

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings, setup_logging
from app.engine.scanner import ResearchScanner
import pandas as pd
from app.errors import AppError, InvalidRequestError, MarketDataUnavailableError, RunNotFoundError, StrategyNotFoundError, TemplateNotFoundError
from app.models import ChartContextRequest, ResearchRunRequest, ResearchRunResponse, StrategyCorrectRequest, StrategyInterpretRequest, StrategyInterpretResponse, StrategySaveRequest, StrategySaveResponse, StrategySpec, StrategyTemplateExampleSaveRequest, StrategyTemplateSaveRequest, StrategyTemplateScanRequest
from app.repositories.database import db
from app.repositories.preference_store import PreferenceStore
from app.repositories.run_store import ResearchRunStore
from app.repositories.strategy_store import StrategyStore
from app.repositories.template_store import StrategyTemplateStore
from app.services.llm_parser import StrategyInterpreter
from app.strategy_registry import all_schools, all_strategies
from app.services.market_data import MarketDataError

setup_logging(settings.app_log_level)
logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title=settings.app_name, version=settings.app_version)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
strategy_store = StrategyStore()
template_store = StrategyTemplateStore()
preference_store = PreferenceStore()
run_store = ResearchRunStore()
interpreter = StrategyInterpreter(preference_store=preference_store)
scanner = ResearchScanner()

if settings.app_cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.app_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def add_request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request_complete request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        (time.perf_counter() - started) * 1000,
    )
    return response


def require_api_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    if not settings.should_require_auth(request.method):
        return
    expected = (settings.app_api_key or "").strip()
    if not expected:
        return
    supplied = (x_api_key or request.query_params.get("api_key") or "").strip()
    if supplied != expected:
        raise InvalidRequestError("Authentication required", {"code_hint": "Provide X-API-Key header"})


@app.exception_handler(AppError)
async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    error = InvalidRequestError("Invalid request", {"fields": exc.errors()})
    return JSONResponse(status_code=error.status_code, content=error.to_dict())


@app.exception_handler(HTTPException)
async def handle_http_exception(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "INVALID_REQUEST", "message": str(exc.detail), "details": {}}
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": detail})


@app.exception_handler(Exception)
async def handle_unexpected_exception(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception type=%s", exc.__class__.__name__)
    return JSONResponse(status_code=500, content={"ok": False, "error": {"code": "INTERNAL_ERROR", "message": "Unexpected internal error", "details": {}}})


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "chart_symbol": settings.app_chart_symbol, "ollama_model": settings.app_ollama_model, "app_version": settings.app_version})


@app.get("/health")
async def health():
    return {"ok": True, "status": "healthy", "app": settings.app_name}


@app.get("/ready")
async def ready():
    settings.ensure_data_dirs()
    test_path = settings.runs_dir / ".write_test"
    try:
        test_path.write_text("ok", encoding="utf-8")
        test_path.unlink(missing_ok=True)
        with sqlite3.connect(settings.database_path) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:
        raise InvalidRequestError("Storage is not ready", {"data_dir": str(settings.data_dir), "db_path": str(settings.database_path), "reason": exc.__class__.__name__})
    return {"ok": True, "status": "ready", "data_dir": str(settings.data_dir), "db_path": str(settings.database_path)}


@app.get("/version")
async def version():
    return {"app": settings.app_name, "version": settings.app_version, "env": settings.app_env, "storage": "sqlite"}


@app.get("/api/templates")
async def templates_api():
    return {"items": interpreter.templates()}


@app.get("/api/strategy-templates")
async def list_strategy_templates():
    return {"items": template_store.list_templates()}


@app.get("/api/strategy-templates/{template_id}")
async def get_strategy_template(template_id: str):
    try:
        return template_store.get_template(template_id)
    except FileNotFoundError as exc:
        raise TemplateNotFoundError(template_id) from exc


@app.post("/api/strategy-templates/save")
async def save_strategy_template(payload: StrategyTemplateSaveRequest, _auth: None = Depends(require_api_key)):
    saved = template_store.save_template(payload)
    preference_store.learn_from_strategy(payload.strategy, note=f"Saved strategy template: {payload.name}")
    return {"saved": saved}


@app.post("/api/strategy-templates/{template_id}/examples")
async def add_strategy_template_example(template_id: str, payload: StrategyTemplateExampleSaveRequest, _auth: None = Depends(require_api_key)):
    try:
        detail = template_store.add_example(template_id, payload)
        return {"saved": detail}
    except FileNotFoundError as exc:
        raise TemplateNotFoundError(template_id) from exc


@app.post("/api/strategy-templates/{template_id}/scan", response_model=ResearchRunResponse)
async def scan_strategy_template(template_id: str, payload: StrategyTemplateScanRequest, _auth: None = Depends(require_api_key)):
    try:
        template = template_store.get_template(template_id)
    except FileNotFoundError as exc:
        raise TemplateNotFoundError(template_id) from exc
    strategy = template.strategy.model_copy(deep=True)
    if payload.symbol:
        strategy.symbol = payload.symbol
        strategy.market_label = payload.symbol if payload.symbol != "XAUUSD" else "Gold"
    request = ResearchRunRequest(
        strategy=strategy,
        backtest_range=payload.backtest_range,
        source_lookback=payload.source_lookback,
        zone_retest_window_bars=payload.zone_retest_window_bars,
        ltf_confirmation_window_bars=payload.ltf_confirmation_window_bars,
        mss_lookback=payload.mss_lookback,
        outcome_window_bars=payload.outcome_window_bars,
        max_setups=payload.max_setups,
    )
    response = scanner.run(request)
    response.strategy_summary = f"{template.name}: {response.strategy_summary}"
    response.highlights = [f"Template: {template.name}", f"Examples stored: {len(template.examples)}", *response.highlights]
    summary = run_store.save_run(request, response)
    response.run_id = summary.id
    return response




@app.get("/api/system/status")
async def system_status():
    return {
        "ok": True,
        "env": settings.app_env,
        "storage": {"type": "sqlite", "db_path": str(settings.database_path), "runs_count": run_store.count_runs(), "schema_version": db.stats()["schema_version"]},
        "market_data": {"providers": scanner.market.provider_names(), "cache_enabled": settings.market_data_cache_enabled, "cache_entries": db.stats()["cache_entries"]},
        "auth": {"enabled": settings.api_auth_enabled(), "required_for_write": settings.app_require_auth_for_write},
        "templates": {"count": len(template_store.list_templates())},
    }


@app.get("/api/capabilities")
async def capabilities_api():
    return {"schools": all_schools(), "strategies": all_strategies()}

@app.post("/api/strategy/interpret", response_model=StrategyInterpretResponse)
async def interpret_strategy(payload: StrategyInterpretRequest, _auth: None = Depends(require_api_key)):
    return await interpreter.interpret(payload.text)


@app.post("/api/strategy/correct", response_model=StrategyInterpretResponse)
async def correct_strategy(payload: StrategyCorrectRequest, _auth: None = Depends(require_api_key)):
    return await interpreter.correct(payload)


@app.get("/api/preferences")
async def get_preferences():
    return preference_store.get()


@app.post("/api/preferences/reset")
async def reset_preferences(_auth: None = Depends(require_api_key)):
    return preference_store.reset()


@app.get("/api/strategy/list")
async def list_strategies():
    return {"items": strategy_store.list_strategies()}


@app.get("/api/strategy/{strategy_id}")
async def get_strategy(strategy_id: str):
    try:
        return strategy_store.get_strategy(strategy_id)
    except FileNotFoundError as exc:
        raise StrategyNotFoundError(strategy_id) from exc


@app.post("/api/strategy/save", response_model=StrategySaveResponse)
async def save_strategy(payload: StrategySaveRequest, _auth: None = Depends(require_api_key)):
    saved = strategy_store.save_strategy(payload)
    preference_store.learn_from_strategy(payload.strategy, note=f"Saved strategy: {payload.name}")
    return StrategySaveResponse(saved=saved)


@app.post("/api/research/run", response_model=ResearchRunResponse)
async def run_research(payload: ResearchRunRequest, _auth: None = Depends(require_api_key)):
    response = scanner.run(payload)
    summary = run_store.save_run(payload, response)
    response.run_id = summary.id
    return response


@app.get("/api/research/runs")
async def list_runs():
    return {"items": run_store.list_runs()}


@app.get("/api/research/runs/{run_id}")
async def get_run(run_id: str):
    try:
        return run_store.get_run(run_id)
    except FileNotFoundError as exc:
        raise RunNotFoundError(run_id) from exc


@app.post("/api/chart/context")
async def chart_context(payload: ChartContextRequest, _auth: None = Depends(require_api_key)):
    constraints = {"backtest_start": payload.start_time, "backtest_end": payload.end_time}
    try:
        df = scanner.market.fetch(payload.symbol, payload.timeframe, periods=10000)
    except MarketDataError as exc:
        raise MarketDataUnavailableError(details={"reason": str(exc)}) from exc
    df = scanner.market.trim_to_window(df, constraints)
    items = []
    for _, row in df.iterrows():
        items.append({"time": int(row["timestamp"].timestamp()), "open": round(float(row["open"]), 3), "high": round(float(row["high"]), 3), "low": round(float(row["low"]), 3), "close": round(float(row["close"]), 3)})
    return {"items": items}


@app.get("/api/local-data/status")
async def local_data_status():
    """Show available local historical data files for offline backtesting."""
    from app.services.local_data_provider import LocalCSVProvider
    provider = LocalCSVProvider()
    datasets = provider.available_datasets()
    return {
        "ok": True,
        "enabled": provider.is_enabled(),
        "data_dir": str(provider._data_dir),
        "datasets": datasets,
        "note": "Local CSV data is used automatically as first priority when available.",
    }


@app.post("/api/backtest/local", response_model=ResearchRunResponse)
async def run_local_backtest(payload: ResearchRunRequest, _auth: None = Depends(require_api_key)):
    """Run backtest using local historical data only (no API keys needed)."""
    from app.services.local_data_provider import LocalCSVProvider
    provider = LocalCSVProvider()
    if not provider.is_enabled():
        raise MarketDataUnavailableError(details={"reason": "No local historical data found. Place CSV files in data/historical/"})
    symbol = payload.strategy.symbol or settings.app_default_symbol
    htf = payload.strategy.primary_timeframe
    ltf = payload.strategy.execution_timeframe
    if not provider.supports(symbol, htf):
        raise MarketDataUnavailableError(details={"reason": f"No local data for {symbol} {htf}. Available: {[d['timeframe'] for d in provider.available_datasets()]}"})
    if not provider.supports(symbol, ltf):
        raise MarketDataUnavailableError(details={"reason": f"No local data for {symbol} {ltf}. Available: {[d['timeframe'] for d in provider.available_datasets()]}"})
    response = scanner.run(payload)
    summary = run_store.save_run(payload, response)
    response.run_id = summary.id
    return response


# ─── QM & Custom Patterns API ─────────────────────────────────────────────


@app.post("/api/patterns/qm/scan", response_model=ResearchRunResponse)
async def scan_qm_levels(payload: ResearchRunRequest, _auth: None = Depends(require_api_key)):
    """
    Phase 1+2: Detect all QM levels on HTF, then analyze LTF reactions.

    Usage:
    - Set strategy_key="qm" in payload.strategy
    - Set primary_timeframe (e.g. "30m") for QM detection
    - Set execution_timeframe (e.g. "5m") for LTF reaction analysis
    - direction: "bullish", "bearish", or "either"
    """
    payload.strategy.strategy_key = "qm"
    response = scanner.run(payload)
    summary = run_store.save_run(payload, response)
    response.run_id = summary.id
    return response


@app.post("/api/patterns/teach")
async def teach_pattern(payload: dict, _auth: None = Depends(require_api_key)):
    """
    Teach the bot a new pattern from chart annotations + description.

    Body: {
        "name": "QM Bullish",
        "direction": "bullish",
        "description": "شرح النمط بالعربي أو الإنجليزي",
        "htf": "30m",
        "ltf": "5m",
        "annotations": [
            {"type": "high", "price": 3350.5, "timestamp": "2026-05-01T10:00:00", "label": "left shoulder"},
            {"type": "low", "price": 3320.0, "timestamp": "2026-05-01T14:00:00", "label": "head"},
            {"type": "low", "price": 3335.0, "timestamp": "2026-05-02T10:00:00", "label": "zone", "is_zone": true}
        ]
    }
    """
    from app.engine.pattern_detector import LearnedPatternMatcher
    from app.engine.custom_patterns import PatternStore
    from dataclasses import asdict

    name = payload.get("name", "")
    direction = payload.get("direction", "either")
    description = payload.get("description", "")
    annotations = payload.get("annotations", [])

    if not name:
        raise InvalidRequestError("name is required")
    if len(annotations) < 2:
        raise InvalidRequestError("At least 2 swing points are required")
    if not description:
        raise InvalidRequestError("description is required")

    matcher = LearnedPatternMatcher()
    pattern = matcher.learn_from_example(annotations, description, direction, name)
    pattern.tags.extend([payload.get("htf", ""), payload.get("ltf", "")])

    store = PatternStore()
    saved = store.save(pattern)

    return {
        "ok": True,
        "pattern_id": saved.pattern_id,
        "name": saved.name,
        "direction": saved.direction,
        "swing_count": len(saved.swing_sequence),
        "examples_count": len(saved.examples),
        "message": f"تم حفظ النمط '{saved.name}' بنجاح. يمكنك الآن البحث عنه في البيانات.",
    }


@app.post("/api/patterns/{pattern_id}/examples")
async def add_pattern_example(pattern_id: str, payload: dict, _auth: None = Depends(require_api_key)):
    """Add another teaching example to an existing pattern."""
    from app.engine.pattern_detector import PatternExample, SwingPoint
    from app.engine.custom_patterns import PatternStore

    annotations = payload.get("annotations", [])
    notes = payload.get("notes", "")

    if len(annotations) < 2:
        raise InvalidRequestError("At least 2 swing points needed")

    swing_points = [
        SwingPoint(type=a["type"], price=a["price"], timestamp=a.get("timestamp", ""), label=a.get("label", ""))
        for a in annotations if a.get("type") in ("high", "low")
    ]

    zone_ann = next((a for a in annotations if a.get("is_zone")), None)
    zone_low = zone_ann["price"] if zone_ann else annotations[-1].get("price", 0)
    zone_high = zone_ann.get("zone_high", zone_low * 1.002) if zone_ann else zone_low * 1.002

    example = PatternExample(
        example_id=f"ex_{pattern_id[:6]}_{len(annotations)}",
        swing_points=swing_points,
        zone_low=zone_low,
        zone_high=zone_high,
        direction=payload.get("direction", "either"),
        notes=notes,
    )

    store = PatternStore()
    updated = store.add_example(pattern_id, example)
    if not updated:
        raise InvalidRequestError(f"Pattern '{pattern_id}' not found")

    return {"ok": True, "examples_count": len(updated.examples), "message": "تم إضافة المثال بنجاح"}


@app.get("/api/patterns/list")
async def list_patterns():
    """List all saved learned patterns."""
    from app.engine.custom_patterns import PatternStore
    store = PatternStore()
    return {"ok": True, "patterns": store.list_all()}


@app.post("/api/patterns/search")
async def search_pattern(payload: dict, _auth: None = Depends(require_api_key)):
    """
    Search for pattern matches in historical data.

    Body: {
        "pattern_id": "learned_qm_bullish_...",
        "max_matches": 50,
        "include_ltf": true
    }
    """
    from app.engine.pattern_detector import LearnedPatternMatcher
    from app.engine.custom_patterns import PatternStore
    from dataclasses import asdict

    pattern_id = payload.get("pattern_id", "")
    max_matches = int(payload.get("max_matches", 50))
    include_ltf = payload.get("include_ltf", False)

    if not pattern_id:
        raise InvalidRequestError("pattern_id is required")

    store = PatternStore()
    pattern = store.get(pattern_id)
    if not pattern:
        raise InvalidRequestError(f"Pattern '{pattern_id}' not found")

    # Determine timeframes from pattern tags or defaults
    htf = next((t for t in pattern.tags if t in ("4h", "1h", "30m", "15m", "5m")), "1h")
    ltf = next((t for t in pattern.tags if t in ("15m", "5m", "3m") and t != htf), "15m")

    # Fetch market data
    symbol = "XAUUSD"
    try:
        htf_df = scanner.market.fetch(symbol, htf, periods=2000)
    except MarketDataError as exc:
        raise MarketDataUnavailableError(details={"reason": str(exc)}) from exc

    htf_df["timestamp"] = pd.to_datetime(htf_df["timestamp"], utc=True)
    htf_df = htf_df.sort_values("timestamp").reset_index(drop=True)

    matcher = LearnedPatternMatcher(swing_lookback=5)
    matches = matcher.find_matches(pattern, htf_df, max_matches)

    # Optionally analyze LTF
    reactions_data = []
    if include_ltf and matches:
        try:
            ltf_df = scanner.market.fetch(symbol, ltf, periods=5000)
            ltf_df["timestamp"] = pd.to_datetime(ltf_df["timestamp"], utc=True)
            ltf_df = ltf_df.sort_values("timestamp").reset_index(drop=True)
            reactions = matcher.analyze_ltf_reactions(matches, ltf_df, outcome_window=30)
            reactions_data = [asdict(r) for r in reactions]
        except MarketDataError:
            pass  # LTF analysis is optional

    # Build response
    fresh = [m for m in matches if m.freshness == "fresh"]
    tested = [m for m in matches if m.freshness == "tested"]
    broken = [m for m in matches if m.freshness == "broken"]

    matches_data = []
    reaction_map = {r["match_id"]: r for r in reactions_data}
    for m in matches:
        md = asdict(m)
        md["reaction"] = reaction_map.get(m.match_id)
        matches_data.append(md)

    wins = [r for r in reactions_data if r.get("outcome") == "win"]
    losses = [r for r in reactions_data if r.get("outcome") == "loss"]

    stats = {
        "win_rate": round(len(wins) / len(reactions_data) * 100, 1) if reactions_data else None,
        "avg_rr": round(sum(r.get("rr_ratio", 0) or 0 for r in reactions_data) / len(reactions_data), 2) if reactions_data else None,
    }

    summary = f"تم اكتشاف {len(matches)} تطابق لنمط '{pattern.name}' ({len(fresh)} طازج، {len(tested)} مُختبر، {len(broken)} مكسور)"
    if reactions_data:
        summary += f" | win rate: {stats['win_rate']}%"

    return {
        "ok": True,
        "pattern_name": pattern.name,
        "total_matches": len(matches),
        "fresh": len(fresh),
        "tested": len(tested),
        "broken": len(broken),
        "matches": matches_data[:max_matches],
        "stats": stats,
        "summary": summary,
    }


@app.delete("/api/patterns/{pattern_id}")
async def delete_pattern(pattern_id: str, _auth: None = Depends(require_api_key)):
    """Delete a learned pattern."""
    from app.engine.custom_patterns import PatternStore
    store = PatternStore()
    deleted = store.delete(pattern_id)
    return {"ok": deleted, "message": "تم الحذف" if deleted else "النمط غير موجود"}


@app.post("/api/presets/optimize")
async def optimize_preset(payload: dict, _auth: None = Depends(require_api_key)):
    strategy_payload = payload.get("strategy")
    if not strategy_payload:
        raise InvalidRequestError("strategy is required")
    strategy = StrategySpec.model_validate(strategy_payload)
    backtest_range = payload.get("backtest_range")
    variant_pairs = payload.get("variant_pairs") or [["30m", "5m"], ["15m", "5m"], ["1h", "15m"], ["4h", "15m"]]
    evaluated = []
    for primary_tf, execution_tf in variant_pairs:
        trial = strategy.model_copy(deep=True)
        trial.primary_timeframe = primary_tf
        trial.execution_timeframe = execution_tf
        trial.timeframe = primary_tf
        tf_map = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}
        trial.constraints["source_timeframe_minutes"] = tf_map.get(primary_tf, 60)
        trial.constraints["execution_timeframe_minutes"] = tf_map.get(execution_tf, 15)
        trial.constraints["max_source_candidates"] = 140 if primary_tf in {"15m", "30m"} else 90
        request = ResearchRunRequest(strategy=trial, backtest_range=backtest_range)
        response = scanner.run(request)
        score = float(response.stats.get("avg_quality_score") or 0) + float(response.total_qualified or 0) * 4
        evaluated.append({"primary_timeframe": primary_tf, "execution_timeframe": execution_tf, "score": round(score, 2), "total_qualified": response.total_qualified, "avg_quality_score": response.stats.get("avg_quality_score", 0), "summary": response.human_summary or response.strategy_summary, "response": response.model_dump()})
    evaluated.sort(key=lambda x: (x["score"], x["avg_quality_score"], x["total_qualified"]), reverse=True)
    return {"best": evaluated[0] if evaluated else None, "variants": evaluated}
