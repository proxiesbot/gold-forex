"""Local CSV data provider for offline backtesting.

Reads historical OHLCV data from CSV files in data/historical/ directory.
No API key needed - works completely offline.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from app.config import settings
from app.services.market_data import BaseMarketDataProvider, MarketDataError

logger = logging.getLogger(__name__)

# Maps (symbol, timeframe) -> CSV filename
CSV_FILE_MAP: dict[tuple[str, str], str] = {
    ("XAUUSD", "1h"): "XAUUSD_1H.csv",
    ("XAUUSD", "4h"): "XAUUSD_4H.csv",
    ("XAUUSD", "15m"): "XAUUSD_15m.csv",
    ("XAUUSD", "5m"): "XAUUSD_5m.csv",
    ("GOLD", "1h"): "XAUUSD_1H.csv",
    ("GOLD", "4h"): "XAUUSD_4H.csv",
    ("GOLD", "15m"): "XAUUSD_15m.csv",
    ("GOLD", "5m"): "XAUUSD_5m.csv",
}


class LocalCSVProvider(BaseMarketDataProvider):
    """Reads OHLCV candles from local CSV files for offline backtesting."""

    name = "local_csv"
    requires_api_key = False

    def __init__(self, data_dir: Path | None = None) -> None:
        super().__init__("")
        self._data_dir = data_dir or (settings.data_dir / "historical")

    def is_enabled(self) -> bool:
        return self._data_dir.exists()

    def normalize_symbol(self, symbol: str) -> str | None:
        return (symbol or "XAUUSD").upper()

    def supports(self, symbol: str, timeframe: str) -> bool:
        key = (self.normalize_symbol(symbol), timeframe)
        if key not in CSV_FILE_MAP:
            return False
        csv_path = self._data_dir / CSV_FILE_MAP[key]
        return csv_path.exists()

    def available_datasets(self) -> list[dict[str, str]]:
        """Return list of available (symbol, timeframe) pairs with row counts."""
        results = []
        for (symbol, tf), filename in CSV_FILE_MAP.items():
            csv_path = self._data_dir / filename
            if csv_path.exists():
                try:
                    row_count = sum(1 for _ in open(csv_path)) - 1  # minus header
                    results.append({"symbol": symbol, "timeframe": tf, "file": filename, "rows": row_count})
                except Exception:
                    results.append({"symbol": symbol, "timeframe": tf, "file": filename, "rows": 0})
        return results

    def fetch(self, symbol: str, timeframe: str, periods: int) -> pd.DataFrame:
        key = (self.normalize_symbol(symbol), timeframe)
        filename = CSV_FILE_MAP.get(key)
        if not filename:
            raise MarketDataError(f"local_csv: no CSV mapping for symbol={symbol}, timeframe={timeframe}")

        csv_path = self._data_dir / filename
        if not csv_path.exists():
            raise MarketDataError(f"local_csv: file not found: {csv_path}")

        try:
            df = pd.read_csv(csv_path, parse_dates=["timestamp"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
            df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

            if "volume" not in df.columns:
                df["volume"] = 0.0

            # Return last N periods
            result = df[["timestamp", "open", "high", "low", "close", "volume"]].tail(periods).reset_index(drop=True)

            if result.empty:
                raise MarketDataError(f"local_csv: no data in {csv_path}")

            logger.info("local_csv_loaded file=%s rows=%d requested=%d", filename, len(result), periods)
            return result

        except MarketDataError:
            raise
        except Exception as exc:
            raise MarketDataError(f"local_csv: failed to read {csv_path}") from exc

    def fetch_range(self, symbol: str, timeframe: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        """Fetch data within a specific date range (useful for backtesting windows)."""
        key = (self.normalize_symbol(symbol), timeframe)
        filename = CSV_FILE_MAP.get(key)
        if not filename:
            raise MarketDataError(f"local_csv: no CSV mapping for symbol={symbol}, timeframe={timeframe}")

        csv_path = self._data_dir / filename
        if not csv_path.exists():
            raise MarketDataError(f"local_csv: file not found: {csv_path}")

        df = pd.read_csv(csv_path, parse_dates=["timestamp"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

        if start:
            df = df[df["timestamp"] >= pd.to_datetime(start, utc=True)]
        if end:
            df = df[df["timestamp"] <= pd.to_datetime(end, utc=True)]

        if "volume" not in df.columns:
            df["volume"] = 0.0

        return df[["timestamp", "open", "high", "low", "close", "volume"]].reset_index(drop=True)
