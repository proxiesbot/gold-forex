#!/usr/bin/env python3
"""
XAU AI Research Bot — Mobile Server (ZERO external dependencies)
=================================================================
Uses ONLY Python standard library. No pip install needed at all.

- http.server for the web server
- csv module for data reading
- json for API responses
- No fastapi, no pydantic, no pandas, no numpy, no compilation

Usage:
    python3 mobile/lite_server.py
    # Open http://localhost:8000 in your browser
"""
import csv
import hashlib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("xau-lite")

# ─── Paths ───
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
HISTORICAL_DIR = DATA_DIR / "historical"
PATTERNS_DIR = DATA_DIR / "patterns"
STATIC_DIR = PROJECT_ROOT / "backend" / "app" / "static"
TEMPLATE_DIR = PROJECT_ROOT / "backend" / "app" / "templates"
PATTERNS_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════
# Data & Pattern Engine (pure Python)
# ═══════════════════════════════════════════════

def read_csv(filepath, max_rows=10000):
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


def find_swings(candles, lookback=5):
    swings = []
    for i in range(lookback, len(candles) - lookback):
        high = candles[i]["high"]
        low = candles[i]["low"]
        w_highs = [candles[j]["high"] for j in range(i - lookback, i + lookback + 1)]
        w_lows = [candles[j]["low"] for j in range(i - lookback, i + lookback + 1)]
        if high == max(w_highs) and high > candles[i-1]["high"] and high > candles[i+1]["high"]:
            swings.append({"type": "high", "price": high, "idx": i, "timestamp": candles[i]["timestamp"],
                           "low": low, "open": candles[i]["open"], "close": candles[i]["close"]})
        if low == min(w_lows) and low < candles[i-1]["low"] and low < candles[i+1]["low"]:
            swings.append({"type": "low", "price": low, "idx": i, "timestamp": candles[i]["timestamp"],
                           "high": high, "open": candles[i]["open"], "close": candles[i]["close"]})
    swings.sort(key=lambda x: x["idx"])
    return swings


def score_match(candidate, target_seq):
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


def find_matches(candles, pattern, max_matches=50):
    swings = find_swings(candles, lookback=5)
    target_seq = pattern.get("swing_sequence", [])
    if len(swings) < len(target_seq):
        return []
    matches = []
    for start in range(len(swings) - len(target_seq) + 1):
        cand = swings[start: start + len(target_seq)]
        sc = score_match(cand, target_seq)
        if sc >= 50.0:
            last = cand[-1]
            if last["type"] == "low":
                zl, zh = last["price"], last.get("high", last["price"] * 1.003)
            else:
                zh, zl = last["price"], last.get("low", last["price"] * 0.997)
            freshness, touches = "fresh", 0
            for c in candles[last["idx"]+1:]:
                if c["high"] >= zl and c["low"] <= zh:
                    touches += 1
                    d = pattern.get("direction", "")
                    if (d == "bullish" and c["close"] < zl) or (d == "bearish" and c["close"] > zh):
                        freshness = "broken"; break
            if touches > 0 and freshness != "broken":
                freshness = "tested"
            mid = hashlib.md5(f"{last['timestamp']}-{zl}".encode()).hexdigest()[:10]
            matches.append({"match_id": mid, "direction": pattern.get("direction", "either"),
                "timestamp": last["timestamp"], "zone_low": round(zl, 3), "zone_high": round(zh, 3),
                "zone_mid": round((zl+zh)/2, 3), "freshness": freshness, "touches_after": touches,
                "similarity_score": round(sc, 1),
                "swing_points": [{"type": s["type"], "price": s["price"], "timestamp": s["timestamp"]} for s in cand]})
        if len(matches) >= max_matches:
            break
    matches.sort(key=lambda m: m["similarity_score"], reverse=True)
    return matches


def teach_pattern(name, direction, description, annotations):
    seq = []
    prev_p = None
    for ann in annotations:
        t = ann.get("type", "").lower()
        if t not in ("high", "low"):
            continue
        rel = "any"
        if prev_p is not None:
            rel = "higher" if ann["price"] > prev_p else "lower"
        seq.append({"type": t, "relation": rel, "label": ann.get("label", "")})
        prev_p = ann["price"]
    pid = f"learned_{name.lower().replace(' ', '_')}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    pattern = {"pattern_id": pid, "name": name, "description": description, "direction": direction,
               "swing_sequence": seq, "examples": [{"annotations": annotations}],
               "created_at": datetime.now(timezone.utc).isoformat(), "tags": ["learned", direction]}
    path = PATTERNS_DIR / f"{pid}.json"
    path.write_text(json.dumps(pattern, ensure_ascii=False, indent=2), encoding="utf-8")
    return pattern


def list_patterns():
    results = []
    for p in sorted(PATTERNS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            results.append({"pattern_id": d["pattern_id"], "name": d["name"], "direction": d.get("direction", ""),
                "description": d.get("description", "")[:100], "swing_count": len(d.get("swing_sequence", [])),
                "examples_count": len(d.get("examples", []))})
        except Exception:
            continue
    return results


def get_data_files():
    if not HISTORICAL_DIR.exists():
        return []
    return [{"file": f.name, "rows": sum(1 for _ in open(f)) - 1} for f in HISTORICAL_DIR.glob("*.csv")]


# ═══════════════════════════════════════════════
# HTTP Server (stdlib only)
# ═══════════════════════════════════════════════

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.info(f"{self.address_string()} {fmt % args}")

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, filepath, content_type):
        if not filepath.exists():
            self.send_error(404)
            return
        body = filepath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length))

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/" or path == "":
            tmpl = TEMPLATE_DIR / "index.html"
            if tmpl.exists():
                html = tmpl.read_text(encoding="utf-8")
                html = html.replace("{{ chart_symbol }}", "XAUUSD").replace("{{ app_version }}", "2.0-mobile").replace("{{ ollama_model }}", "none")
                self._html(html)
            else:
                self._html("<h1>XAU AI Lite</h1><p>Server running. No template found.</p>")
        elif path == "/static/app.css":
            self._file(STATIC_DIR / "app.css", "text/css")
        elif path == "/static/app.js":
            self._file(STATIC_DIR / "app.js", "application/javascript")
        elif path == "/health":
            self._json({"ok": True, "status": "healthy", "mode": "mobile-lite"})
        elif path == "/ready":
            self._json({"ok": True, "status": "ready"})
        elif path == "/version":
            self._json({"app": "xau-ai-lite", "version": "2.0-mobile", "env": "termux"})
        elif path == "/api/local-data/status":
            self._json({"ok": True, "datasets": get_data_files()})
        elif path == "/api/capabilities":
            self._json({"schools": [{"key": "learned", "label": "Pattern Teacher"}], "strategies": []})
        elif path == "/api/system/status":
            self._json({"ok": True, "env": "mobile", "storage": {"type": "json"}, "market_data": {"providers": ["local_csv"]}, "auth": {"enabled": False}, "templates": {"count": 0}})
        elif path == "/api/templates":
            self._json({"items": []})
        elif path == "/api/strategy/list":
            self._json({"items": []})
        elif path == "/api/strategy-templates":
            self._json({"items": []})
        elif path == "/api/research/runs":
            self._json({"items": []})
        elif path == "/api/preferences":
            self._json({"default_symbol": "XAUUSD"})
        elif path == "/api/patterns/list":
            self._json({"ok": True, "patterns": list_patterns()})
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_body()

        if path == "/api/chart/context":
            tf = body.get("timeframe", "1h")
            tf_map = {"1h": "XAUUSD_1H.csv", "4h": "XAUUSD_4H.csv", "15m": "XAUUSD_15m.csv", "5m": "XAUUSD_5m.csv"}
            fp = HISTORICAL_DIR / tf_map.get(tf, "XAUUSD_1H.csv")
            candles = read_csv(fp, 5000)
            start, end = body.get("start_time", ""), body.get("end_time", "")
            if start:
                candles = [c for c in candles if c["timestamp"] >= start]
            if end:
                candles = [c for c in candles if c["timestamp"] <= end]
            items = []
            for c in candles:
                try:
                    ts = c["timestamp"].replace("+00:00", "Z")
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    items.append({"time": int(dt.timestamp()), "open": round(c["open"], 3), "high": round(c["high"], 3), "low": round(c["low"], 3), "close": round(c["close"], 3)})
                except Exception:
                    continue
            self._json({"items": items})

        elif path == "/api/patterns/teach":
            name = body.get("name", "")
            if not name:
                self._json({"ok": False, "error": "name required"}, 400); return
            annotations = body.get("annotations", [])
            if len(annotations) < 2:
                self._json({"ok": False, "error": "2+ points needed"}, 400); return
            p = teach_pattern(name, body.get("direction", "either"), body.get("description", ""), annotations)
            self._json({"ok": True, "pattern_id": p["pattern_id"], "name": p["name"],
                "direction": p["direction"], "swing_count": len(p["swing_sequence"]),
                "examples_count": 1, "message": f"تم حفظ النمط '{p['name']}' بنجاح."})

        elif path == "/api/patterns/search":
            pid = body.get("pattern_id", "")
            fp = PATTERNS_DIR / f"{pid}.json"
            if not fp.exists():
                self._json({"ok": False, "error": "Pattern not found"}, 404); return
            pattern = json.loads(fp.read_text(encoding="utf-8"))
            htf = next((t for t in pattern.get("tags", []) if t in ("4h", "1h", "30m", "15m")), "1h")
            tf_map = {"1h": "XAUUSD_1H.csv", "4h": "XAUUSD_4H.csv", "15m": "XAUUSD_15m.csv", "5m": "XAUUSD_5m.csv"}
            candles = read_csv(HISTORICAL_DIR / tf_map.get(htf, "XAUUSD_1H.csv"), 3000)
            matches = find_matches(candles, pattern, int(body.get("max_matches", 50)))
            fresh = [m for m in matches if m["freshness"] == "fresh"]
            tested = [m for m in matches if m["freshness"] == "tested"]
            broken = [m for m in matches if m["freshness"] == "broken"]
            self._json({"ok": True, "pattern_name": pattern["name"], "total_matches": len(matches),
                "fresh": len(fresh), "tested": len(tested), "broken": len(broken), "matches": matches,
                "stats": {}, "summary": f"تم اكتشاف {len(matches)} تطابق ({len(fresh)} طازج)"})

        elif path == "/api/strategy/interpret":
            self._json({"ok": False, "error": "Use Pattern Teacher tab"}, 501)
        elif path == "/api/research/run":
            self._json({"ok": False, "error": "Use Pattern Teacher tab"}, 501)
        elif path == "/api/presets/optimize":
            self._json({"ok": False, "error": "Not in lite mode"}, 501)
        else:
            self._json({"ok": False, "error": "Not found"}, 404)

    def do_DELETE(self):
        path = urlparse(self.path).path
        m = re.match(r"/api/patterns/(.+)", path)
        if m:
            pid = m.group(1)
            fp = PATTERNS_DIR / f"{pid}.json"
            if fp.exists():
                fp.unlink()
                self._json({"ok": True, "message": "تم الحذف"})
            else:
                self._json({"ok": False, "message": "غير موجود"}, 404)
        else:
            self.send_error(404)


def main():
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")

    print(f"""
  ╔═══════════════════════════════════════════╗
  ║  XAU AI Research Bot — Mobile Edition    ║
  ║  ZERO pip install needed!                ║
  ╠═══════════════════════════════════════════╣
  ║  http://localhost:{port}                  ║
  ╚═══════════════════════════════════════════╝
""")
    data = get_data_files()
    if data:
        for d in data:
            print(f"  ✓ {d['file']} ({d['rows']} rows)")
    else:
        print("  ⚠ No data files found in data/historical/")
        print("  Copy CSV files from a PC or download them.")
    print()

    server = HTTPServer((host, port), Handler)
    logger.info(f"Server running on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
