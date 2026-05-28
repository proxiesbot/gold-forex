#!/usr/bin/env python3
"""Download and save XAUUSD historical data for offline backtesting.

Usage:
    python scripts/seed_historical_data.py

This creates CSV files in data/historical/ that the app uses automatically
for backtesting without needing API keys.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    print("ERROR: yfinance and pandas required. Install with: pip install yfinance pandas")
    sys.exit(1)


DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "historical"


def download_1h() -> pd.DataFrame:
    """Download 1H gold futures data (max ~730 days from yfinance)."""
    print("Downloading XAUUSD 1H data (up to 730 days)...")
    ticker = yf.Ticker("GC=F")
    hist = ticker.history(period="730d", interval="60m", auto_adjust=False)
    raw = hist.reset_index()
    time_col = "Datetime" if "Datetime" in raw.columns else "Date"
    df = raw.rename(columns=str.lower).rename(columns={time_col.lower(): "timestamp"})
    df = df[["timestamp", "open", "high", "low", "close", "volume"]].dropna()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    print(f"  -> {len(df)} candles ({df.timestamp.min().date()} to {df.timestamp.max().date()})")
    return df


def resample_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Resample 1H data to 4H."""
    print("Resampling to 4H...")
    work = df_1h.set_index("timestamp").sort_index()
    df_4h = work.resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    df_4h = df_4h.reset_index()
    print(f"  -> {len(df_4h)} candles")
    return df_4h


def download_15m() -> pd.DataFrame:
    """Download 15m data (max 60 days from yfinance)."""
    print("Downloading XAUUSD 15m data (up to 60 days)...")
    ticker = yf.Ticker("GC=F")
    hist = ticker.history(period="60d", interval="15m", auto_adjust=False)
    raw = hist.reset_index()
    time_col = "Datetime" if "Datetime" in raw.columns else "Date"
    df = raw.rename(columns=str.lower).rename(columns={time_col.lower(): "timestamp"})
    df = df[["timestamp", "open", "high", "low", "close", "volume"]].dropna()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    print(f"  -> {len(df)} candles ({df.timestamp.min().date()} to {df.timestamp.max().date()})")
    return df


def download_5m() -> pd.DataFrame:
    """Download 5m data (max 60 days from yfinance)."""
    print("Downloading XAUUSD 5m data (up to 60 days)...")
    ticker = yf.Ticker("GC=F")
    hist = ticker.history(period="60d", interval="5m", auto_adjust=False)
    raw = hist.reset_index()
    time_col = "Datetime" if "Datetime" in raw.columns else "Date"
    df = raw.rename(columns=str.lower).rename(columns={time_col.lower(): "timestamp"})
    df = df[["timestamp", "open", "high", "low", "close", "volume"]].dropna()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    print(f"  -> {len(df)} candles ({df.timestamp.min().date()} to {df.timestamp.max().date()})")
    return df


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Saving data to: {DATA_DIR}\n")

    # 1H
    df_1h = download_1h()
    df_1h.to_csv(DATA_DIR / "XAUUSD_1H.csv", index=False)

    # 4H (resampled from 1H)
    df_4h = resample_4h(df_1h)
    df_4h.to_csv(DATA_DIR / "XAUUSD_4H.csv", index=False)

    # 15m
    df_15m = download_15m()
    df_15m.to_csv(DATA_DIR / "XAUUSD_15m.csv", index=False)

    # 5m
    df_5m = download_5m()
    df_5m.to_csv(DATA_DIR / "XAUUSD_5m.csv", index=False)

    print(f"\nDone! All files saved to {DATA_DIR}")
    print("\nAvailable datasets:")
    for f in sorted(DATA_DIR.glob("*.csv")):
        size_mb = f.stat().st_size / (1024 * 1024)
        print(f"  {f.name} ({size_mb:.1f} MB)")
    print("\nThe app will automatically use these for backtesting (no API keys needed).")


if __name__ == "__main__":
    main()
