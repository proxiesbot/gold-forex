#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/../bot"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt

if [ -f ../.env ]; then
  set -a
  . ../.env
  set +a
fi

python main.py
