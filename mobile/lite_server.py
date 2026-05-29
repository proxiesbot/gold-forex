#!/usr/bin/env python3
"""
XAU AI Research Bot — Mobile Lite Server
=========================================
Single-file server that works WITHOUT pandas/numpy.
Designed for Termux / mobile environments.

Requirements: pip install fastapi uvicorn jinja2
That's it. No pandas, no numpy, no compilation needed.

Usage:
    python lite_server.py
    # Open http://localhost:8000
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any

# ─── Try fastapi, fallback to built-in http server ───
try:
    from fastapi import FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("lite")

# ─── Paths ───
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
HISTORICAL_DIR = DATA_DIR / "historical"
PATTERNS_DIR = DATA_DIR / "patterns"
PATTERNS_DIR.mkdir(parents=True, exist_ok=True)

# ─── CSV Reader (replaces pandas) ───

def read_csv_data(filepath: Path, max_rows: int = 10000) -> list[dict]:
    """Read OHLCV CSV into list of dicts. No pandas needed."""
    if not filepath.exists():
        return []
    rows = []
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i >= max_rows:
                break
            try:
                rows.append({
                    "timestamp": row["timestamp"],
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume", 0)),
                })
            except (ValueError, KeyError):
                continue
    return rows


def get_available_data() -> list[dict]:
    """List available CSV files."""
    if not HISTORICAL_DIR.exists():
        return []
    results = []
    for f in HISTORICAL_DIR.glob("*.csv"):
        rows = sum(1 for _ in open(f)) - 1
        results.append({"file": f.name, "rows": rows})
    return results


# ─── Swing Detection (replaces pandas-based detector) ───

def find_swings(candles: list[dict], lookback: int = 5) -> list[dict]:
    """Find swing highs and lows from candle data."""
    swings = []
    for i in range(lookback, len(candles) - lookback):
        high = candles[i]["high"]
        low = candles[i]["low"]

        window_highs = [candles[j]["high"] for j in range(i - lookback, i + lookback + 1)]
        window_lows = [candles[j]["low"] for j in range(i - lookback, i + lookback + 1)]

        if high == max(window_highs) and high > candles[i-1]["high"] and high > candles[i+1]["high"]:
            swings.append({
                "type": "high", "price": high, "idx": i,
                "timestamp": candles[i]["timestamp"],
                "low": low, "open": candles[i]["open"], "close": candles[i]["close"],
            })

        if low == min(window_lows) and low < candles[i-1]["low"] and low < candles[i+1]["low"]:
            swings.append({
                "type": "low", "price": low, "idx": i,
                "timestamp": candles[i]["timestamp"],
                "high": high, "open": candles[i]["open"], "close": candles[i]["close"],
            })

    swings.sort(key=lambda x: x["idx"])
    return swings


# ─── Pattern Matching (replaces LearnedPatternMatcher) ───

def score_match(candidate: list[dict], target_seq: list[dict]) -> float:
    """Score how well candidate swings match target pattern."""
    if len(candidate) != len(target_seq):
        return 0.0
    score = 60.0
    for i, (swing, target) in enumerate(zip(candidate, target_seq)):
        if swing["type"] != target["type"]:
            return 0.0
        if i > 0 and target.get("relation") in ("higher", "lower"):
            prev = candidate[i - 1]
            if target["relation"] == "higher" and swing["price"] <= prev["price"]:
                score -= 20.0
            elif target["relation"] == "lower" and swing["price"] >= prev["price"]:
                score -= 20.0
            else:
                score += 10.0
    return max(0.0, min(100.0, score))


def find_pattern_matches(candles: list[dict], pattern: dict, max_matches: int = 50) -> list[dict]:
    """Find all matches of a learned pattern in candle data."""
    swings = find_swings(candles, lookback=5)
    target_seq = pattern.get("swing_sequence", [])
    if len(swings) < len(target_seq):
        return []

    matches = []
    for start in range(len(swings) - len(target_seq) + 1):
        candidate = swings[start: start + len(target_seq)]
        score = score_match(candidate, target_seq)
        if score >= 50.0:
            last = candidate[-1]
            zone_low = last["price"]
            zone_high = last.get("high", last["price"] * 1.003) if last["type"] == "low" else last["price"]
            if last["type"] == "high":
                zone_high = last["price"]
                zone_low = last.get("low", last["price"] * 0.997)

            # Check freshness
            freshness = "fresh"
            touches = 0
            for c in candles[last["idx"]+1:]:
                if c["high"] >= zone_low and c["low"] <= zone_high:
                    touches += 1
                    if (pattern.get("direction") == "bullish" and c["close"] < zone_low) or \
                       (pattern.get("direction") == "bearish" and c["close"] > zone_high):
                        freshness = "broken"
                        break
            if touches > 0 and freshness != "broken":
                freshness = "tested"

            match_id = hashlib.md5(f"{last['timestamp']}-{zone_low}".encode()).hexdigest()[:10]
            matches.append({
                "match_id": match_id,
                "direction": pattern.get("direction", "either"),
                "timestamp": last["timestamp"],
                "zone_low": round(zone_low, 3),
                "zone_high": round(zone_high, 3),
                "zone_mid": round((zone_low + zone_high) / 2, 3),
                "freshness": freshness,
                "touches_after": touches,
                "similarity_score": round(score, 1),
                "swing_points": [{"type": s["type"], "price": s["price"], "timestamp": s["timestamp"]} for s in candidate],
            })
        if len(matches) >= max_matches:
            break

    matches.sort(key=lambda m: m["similarity_score"], reverse=True)
    return matches


# ─── Pattern Store ───

def save_pattern(pattern: dict) -> dict:
    """Save a learned pattern to disk."""
    path = PATTERNS_DIR / f"{pattern['pattern_id']}.json"
    path.write_text(json.dumps(pattern, ensure_ascii=False, indent=2), encoding="utf-8")
    return pattern


def load_pattern(pattern_id: str) -> dict | None:
    """Load a pattern from disk."""
    path = PATTERNS_DIR / f"{pattern_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_patterns() -> list[dict]:
    """List all saved patterns."""
    results = []
    for path in sorted(PATTERNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            results.append({
                "pattern_id": data["pattern_id"],
                "name": data["name"],
                "direction": data.get("direction", "either"),
                "description": data.get("description", "")[:120],
                "swing_count": len(data.get("swing_sequence", [])),
                "examples_count": len(data.get("examples", [])),
            })
        except Exception:
            continue
    return results


def teach_pattern(name: str, direction: str, description: str, annotations: list[dict]) -> dict:
    """Learn a pattern from user annotations."""
    swing_sequence = []
    prev_price = None
    for ann in annotations:
        swing_type = ann.get("type", "").lower()
        if swing_type not in ("high", "low"):
            continue
        relation = "any"
        if prev_price is not None:
            relation = "higher" if ann["price"] > prev_price else "lower"
        swing_sequence.append({"type": swing_type, "relation": relation, "label": ann.get("label", "")})
        prev_price = ann["price"]

    pattern_id = f"learned_{name.lower().replace(' ', '_')}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    pattern = {
        "pattern_id": pattern_id,
        "name": name,
        "description": description,
        "direction": direction,
        "swing_sequence": swing_sequence,
        "zone_position": "last",
        "zone_from": "swing_low" if direction == "bullish" else "swing_high",
        "examples": [{"annotations": annotations, "notes": description}],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tags": ["learned", direction],
    }
    return save_pattern(pattern)


# ─── FastAPI Server ───

if HAS_FASTAPI:
    app = FastAPI(title="XAU AI Lite", version="2.0-lite")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    # Serve static files from the full backend if available
    STATIC_DIR = PROJECT_ROOT / "backend" / "app" / "static"
    TEMPLATE_DIR = PROJECT_ROOT / "backend" / "app" / "templates"
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        tmpl = TEMPLATE_DIR / "index.html" if TEMPLATE_DIR.exists() else None
        if tmpl and tmpl.exists():
            from jinja2 import Template
            html = Template(tmpl.read_text(encoding="utf-8")).render(
                chart_symbol="XAUUSD", app_version="2.0-lite", ollama_model="none"
            )
            return HTMLResponse(html)
        return HTMLResponse("<h1>XAU AI Lite Running</h1><p>No template found</p>")

    @app.get("/health")
    async def health():
        return {"ok": True, "status": "healthy", "app": "xau-ai-lite", "mode": "mobile"}

    @app.get("/ready")
    async def ready():
        return {"ok": True, "status": "ready", "data_dir": str(DATA_DIR)}

    @app.get("/version")
    async def version():
        return {"app": "xau-ai-lite", "version": "2.0-lite", "env": "mobile", "storage": "json"}

    @app.get("/api/local-data/status")
    async def local_data_status():
        return {"ok": True, "datasets": get_available_data(), "data_dir": str(HISTORICAL_DIR)}

    @app.get("/api/capabilities")
    async def capabilities():
        return {
            "schools": [{"key": "learned", "label": "User-taught patterns", "description": "You teach, bot searches"}],
            "strategies": [{"key": "pattern_teach", "school": "learned", "label": "Pattern Teacher", "aliases": []}],
        }

    @app.get("/api/system/status")
    async def system_status():
        return {
            "ok": True, "env": "mobile-lite",
            "storage": {"type": "json", "patterns_count": len(list_patterns())},
            "market_data": {"providers": ["local_csv"], "cache_enabled": False},
        }

    @app.get("/api/templates")
    async def templates_list():
        return {"items": []}

    @app.get("/api/strategy/list")
    async def strategy_list():
        return {"items": []}

    @app.get("/api/strategy-templates")
    async def strategy_templates():
        return {"items": []}

    @app.get("/api/research/runs")
    async def runs_list():
        return {"items": []}

    @app.get("/api/preferences")
    async def preferences():
        return {"default_symbol": "XAUUSD", "default_primary_timeframe": "1h"}

    @app.post("/api/chart/context")
    async def chart_context(request: Request):
        body = await request.json()
        tf = body.get("timeframe", "1h")
        tf_map = {"1h": "XAUUSD_1H.csv", "4h": "XAUUSD_4H.csv", "15m": "XAUUSD_15m.csv", "5m": "XAUUSD_5m.csv", "30m": "XAUUSD_1H.csv"}
        filename = tf_map.get(tf, "XAUUSD_1H.csv")
        filepath = HISTORICAL_DIR / filename
        candles = read_csv_data(filepath, max_rows=5000)

        # Filter by date range
        start = body.get("start_time", "")
        end = body.get("end_time", "")
        if start:
            candles = [c for c in candles if c["timestamp"] >= start]
        if end:
            candles = [c for c in candles if c["timestamp"] <= end]

        # Convert to chart format
        items = []
        for c in candles:
            try:
                ts = datetime.fromisoformat(c["timestamp"].replace("+00:00", "+00:00").replace("Z", "+00:00"))
                items.append({"time": int(ts.timestamp()), "open": round(c["open"], 3), "high": round(c["high"], 3), "low": round(c["low"], 3), "close": round(c["close"], 3)})
            except Exception:
                continue
        return {"items": items}

    @app.post("/api/patterns/teach")
    async def api_teach(request: Request):
        body = await request.json()
        name = body.get("name", "")
        direction = body.get("direction", "either")
        description = body.get("description", "")
        annotations = body.get("annotations", [])
        if not name:
            return JSONResponse({"ok": False, "error": "name is required"}, 400)
        if len(annotations) < 2:
            return JSONResponse({"ok": False, "error": "At least 2 points needed"}, 400)
        pattern = teach_pattern(name, direction, description, annotations)
        return {
            "ok": True,
            "pattern_id": pattern["pattern_id"],
            "name": pattern["name"],
            "direction": pattern["direction"],
            "swing_count": len(pattern["swing_sequence"]),
            "examples_count": 1,
            "message": f"تم حفظ النمط '{pattern['name']}' بنجاح. يمكنك الآن البحث عنه.",
        }

    @app.get("/api/patterns/list")
    async def api_patterns_list():
        return {"ok": True, "patterns": list_patterns()}

    @app.post("/api/patterns/search")
    async def api_search(request: Request):
        body = await request.json()
        pattern_id = body.get("pattern_id", "")
        max_matches = int(body.get("max_matches", 50))
        pattern = load_pattern(pattern_id)
        if not pattern:
            return JSONResponse({"ok": False, "error": "Pattern not found"}, 404)

        # Load HTF data
        htf = body.get("htf") or next((t for t in pattern.get("tags", []) if t in ("4h", "1h", "30m", "15m")), "1h")
        tf_map = {"1h": "XAUUSD_1H.csv", "4h": "XAUUSD_4H.csv", "15m": "XAUUSD_15m.csv", "5m": "XAUUSD_5m.csv"}
        filepath = HISTORICAL_DIR / tf_map.get(htf, "XAUUSD_1H.csv")
        candles = read_csv_data(filepath, max_rows=3000)
        if not candles:
            return JSONResponse({"ok": False, "error": f"No data for {htf}"}, 400)

        matches = find_pattern_matches(candles, pattern, max_matches)
        fresh = [m for m in matches if m["freshness"] == "fresh"]
        tested = [m for m in matches if m["freshness"] == "tested"]
        broken = [m for m in matches if m["freshness"] == "broken"]

        summary = f"تم اكتشاف {len(matches)} تطابق لنمط '{pattern['name']}' ({len(fresh)} طازج، {len(tested)} مُختبر، {len(broken)} مكسور)"
        return {
            "ok": True,
            "pattern_name": pattern["name"],
            "total_matches": len(matches),
            "fresh": len(fresh),
            "tested": len(tested),
            "broken": len(broken),
            "matches": matches,
            "stats": {"win_rate": None, "avg_rr": None},
            "summary": summary,
        }

    @app.delete("/api/patterns/{pattern_id}")
    async def api_delete_pattern(pattern_id: str):
        path = PATTERNS_DIR / f"{pattern_id}.json"
        if path.exists():
            path.unlink()
            return {"ok": True, "message": "تم الحذف"}
        return {"ok": False, "message": "غير موجود"}

    # Catch-all stubs for endpoints the frontend expects
    @app.post("/api/strategy/interpret")
    async def stub_interpret(request: Request):
        return JSONResponse({"ok": False, "error": "Not available in lite mode"}, 501)

    @app.post("/api/research/run")
    async def stub_run(request: Request):
        return JSONResponse({"ok": False, "error": "Use Pattern Teacher in lite mode"}, 501)

    @app.post("/api/presets/optimize")
    async def stub_optimize(request: Request):
        return JSONResponse({"ok": False, "error": "Not available in lite mode"}, 501)


def main():
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")

    print(f"""
╔══════════════════════════════════════════════╗
║   XAU AI Research Bot — Mobile Lite         ║
║   No pandas • No numpy • No compilation    ║
╠══════════════════════════════════════════════╣
║   Open: http://localhost:{port}              ║
║   Or:   http://{host}:{port}           ║
╚══════════════════════════════════════════════╝
    """)

    data = get_available_data()
    if data:
        print(f"  Data: {len(data)} files available")
        for d in data:
            print(f"    - {d['file']} ({d['rows']} rows)")
    else:
        print("  ⚠️  No historical data found!")
        print(f"  Put CSV files in: {HISTORICAL_DIR}")
    print()

    if HAS_FASTAPI:
        import uvicorn
        uvicorn.run(app, host=host, port=port)
    else:
        print("ERROR: fastapi not installed. Run: pip install fastapi uvicorn jinja2")
        print("These install in seconds - no compilation needed!")


if __name__ == "__main__":
    main()
