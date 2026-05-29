#!/usr/bin/env bash
# ═══════════════════════════════════════════════
# XAU AI — Mobile Quick Start (Termux / Linux)
# One command: bash start.sh
# ═══════════════════════════════════════════════
set -e
cd "$(dirname "$0")/.."

echo "╔══════════════════════════════════════╗"
echo "║   XAU AI — تثبيت خفيف للموبايل     ║"
echo "╚══════════════════════════════════════╝"
echo ""

# Only install lightweight packages (no pandas/numpy)
echo "⏳ تثبيت المكتبات (بدون pandas - سريع)..."
pip install --quiet fastapi uvicorn jinja2 2>/dev/null || pip install fastapi uvicorn jinja2

# Download data if not present
if [ ! -f "data/historical/XAUUSD_1H.csv" ]; then
    echo "⏳ تحميل بيانات الذهب..."
    pip install --quiet yfinance 2>/dev/null || pip install yfinance
    python3 scripts/seed_historical_data.py
fi

echo ""
echo "🚀 تشغيل السيرفر..."
echo "   افتح: http://localhost:8000"
echo ""

python3 mobile/lite_server.py
