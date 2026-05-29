#!/usr/bin/env bash
# ═══════════════════════════════════════════════
# XAU AI — Mobile (Termux) - ZERO dependencies
# Just: bash start.sh
# ═══════════════════════════════════════════════
set -e
cd "$(dirname "$0")/.."

echo ""
echo "  XAU AI Research Bot — Mobile"
echo "  ============================="
echo "  No pip install needed!"
echo ""

# Download data if not present
if [ ! -f "data/historical/XAUUSD_1H.csv" ]; then
    echo "  Need historical data first."
    echo "  Run on a PC: python scripts/seed_historical_data.py"
    echo "  Then copy data/historical/ folder to your phone."
    echo ""
    echo "  Or download manually:"
    echo "  curl -o data/historical/XAUUSD_1H.csv https://raw.githubusercontent.com/proxiesbot/gold-forex/fix/project-restructure-and-fixes/data/historical/XAUUSD_1H.csv"
fi

echo "  Starting server on http://localhost:8000"
echo ""

python3 mobile/lite_server.py
