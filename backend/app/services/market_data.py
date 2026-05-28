from __future__ import annotations

import hashlib
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from time import perf_counter, sleep
from typing import Any

import httpx
import pandas as pd

from app.config import settings
from app.repositories.database import db

try:
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None

logger = logging.getLogger(__name__)

INTERVAL_MAP = {"1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "60m", "4h": "60m"}
PERIOD_HINTS = {"1m": "7d", "3m": "7d", "5m": "60d", "15m": "60d", "30m": "60d", "1h": "730d", "4h": "730d"}
TWELVE_INTERVAL_MAP = {"1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h"}
ALPHA_INTERVAL_MAP = {"1m": "1min", "3m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "60min", "4h": "60min"}

SYMBOL_MAP = {
    "XAUUSD": {"yfinance": "GC=F", "twelvedata": "XAU/USD", "alphavantage": ("XAU", "USD")},
    "GOLD": {"yfinance": "GC=F", "twelvedata": "XAU/USD", "alphavantage": ("XAU", "USD")},
    "GC=F": {"yfinance": "GC=F", "twelvedata": "XAU/USD", "alphavantage": ("XAU", "USD")},
    "EURUSD": {"yfinance": "EURUSD=X", "twelvedata": "EUR/USD", "alphavantage": ("EUR", "USD")},
    "GBPUSD": {"yfinance": "GBPUSD=X", "twelvedata": "GBP/USD", "alphavantage": ("GBP", "USD")},
    "BTCUSD": {"yfinance": "BTC-USD", "twelvedata": "BTC/USD", "alphavantage": None},
}


class MarketDataError(Exception):
    """Raised when real market data cannot be fetched or validated."""


@dataclass
class ProviderFailure:
    provider: str
    reason: str


@dataclass
class MarketDataFetchReport:
    symbol: str
    timeframe: str
    periods: int
    provider: str | None = None
    cache_hit: bool = False
    failures: list[ProviderFailure] = field(default_factory=list)
    candles: int = 0
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["failures"] = [asdict(item) for item in self.failures]
        payload["duration_ms"] = round(float(self.duration_ms), 2)
        return payload


class BaseMarketDataProvider(ABC):
    """Abstract market data provider."""

    name: str = "base"
    requires_api_key: bool = False

    def __init__(self, api_key: str = "") -> None:
        self.api_key = (api_key or "").strip()

    def is_enabled(self) -> bool:
        return (not self.requires_api_key) or bool(self.api_key)

    def supports(self, symbol: str, timeframe: str) -> bool:
        return timeframe in INTERVAL_MAP and self.normalize_symbol(symbol) is not None

    @abstractmethod
    def normalize_symbol(self, symbol: str) -> Any:
        raise NotImplementedError

    @abstractmethod
    def fetch(self, symbol: str, timeframe: str, periods: int) -> pd.DataFrame:
        raise NotImplementedError

    def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        attempts = max(1, settings.market_data_retry_attempts + 1)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                with httpx.Client(timeout=settings.market_data_http_timeout_seconds) as client:
                    response = client.get(url, params=params)
                    if response.status_code in {429, 500, 502, 503, 504}:
                        raise MarketDataError(f"{self.name}: upstream temporary failure ({response.status_code})")
                    response.raise_for_status()
                    return response.json()
            except Exception as exc:  # pragma: no cover - network timing varies
                last_error = exc
                if attempt >= attempts:
                    break
                sleep(max(0.05, settings.market_data_retry_backoff_seconds) * attempt)
        if isinstance(last_error, MarketDataError):
            raise last_error
        raise MarketDataError(f"{self.name}: request failed") from last_error

    def _validate_frame(self, df: pd.DataFrame, symbol: str, timeframe: str, periods: int) -> pd.DataFrame:
        if df is None or df.empty:
            raise MarketDataError(f"{self.name}: no market data returned for symbol={symbol}, timeframe={timeframe}")
        work = df.copy()
        if "timestamp" not in work.columns:
            raise MarketDataError(f"{self.name}: timestamp column missing for symbol={symbol}, timeframe={timeframe}")
        work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True, errors="coerce")
        work = work.dropna(subset=["timestamp"]).sort_values("timestamp").drop_duplicates(subset=["timestamp"])
        required = ["open", "high", "low", "close"]
        missing = [col for col in required if col not in work.columns]
        if missing:
            raise MarketDataError(f"{self.name}: market data missing required columns: {', '.join(missing)}")
        if "volume" not in work.columns:
            work["volume"] = 0.0
        cols = ["timestamp", "open", "high", "low", "close", "volume"]
        for col in cols[1:]:
            work[col] = pd.to_numeric(work[col], errors="coerce")
        work = work.dropna(subset=["open", "high", "low", "close"])
        work = work[(work["high"] >= work["low"]) & (work["open"] > 0) & (work["high"] > 0) & (work["low"] > 0) & (work["close"] > 0)]
        result = work[cols].tail(periods).reset_index(drop=True)
        if result.empty:
            raise MarketDataError(f"{self.name}: market data became empty after validation for symbol={symbol}, timeframe={timeframe}")
        return result


class YahooFinanceProvider(BaseMarketDataProvider):
    name = "yfinance"

    def normalize_symbol(self, symbol: str) -> str | None:
        mapped = SYMBOL_MAP.get((symbol or "XAUUSD").upper(), {})
        return mapped.get("yfinance") or symbol

    def fetch(self, symbol: str, timeframe: str, periods: int) -> pd.DataFrame:
        if yf is None:
            raise MarketDataError("yfinance: package is not installed")
        interval = INTERVAL_MAP.get(timeframe)
        if not interval:
            raise MarketDataError(f"yfinance: unsupported timeframe {timeframe}")
        market_symbol = self.normalize_symbol(symbol)
        try:
            fetch_periods = periods * 4 if timeframe == "4h" else periods
            ticker = yf.Ticker(market_symbol)
            history = ticker.history(period=PERIOD_HINTS.get(timeframe, "60d"), interval=interval, auto_adjust=False)
            raw = history.reset_index()
            if raw.empty:
                raise MarketDataError(f"yfinance: no market data returned for symbol={symbol}, timeframe={timeframe}")
            time_col = "Datetime" if "Datetime" in raw.columns else "Date"
            df = raw.rename(columns=str.lower).rename(columns={time_col.lower(): "timestamp"})
            validated = self._validate_frame(df, symbol, timeframe, max(fetch_periods, periods))
            if timeframe == "4h":
                validated = self._resample_to_4h(validated, periods)
            return validated
        except MarketDataError:
            raise
        except Exception as exc:  # pragma: no cover
            raise MarketDataError(f"yfinance: request failed for symbol={symbol}, timeframe={timeframe}") from exc

    def _resample_to_4h(self, df: pd.DataFrame, periods: int) -> pd.DataFrame:
        work = df.set_index("timestamp").sort_index()
        agg = work.resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
        agg = agg.reset_index()
        return self._validate_frame(agg, "resampled", "4h", periods)


class TwelveDataProvider(BaseMarketDataProvider):
    name = "twelvedata"
    requires_api_key = True
    base_url = "https://api.twelvedata.com/time_series"

    def normalize_symbol(self, symbol: str) -> str | None:
        mapped = SYMBOL_MAP.get((symbol or "XAUUSD").upper(), {})
        return mapped.get("twelvedata") or symbol

    def fetch(self, symbol: str, timeframe: str, periods: int) -> pd.DataFrame:
        interval = TWELVE_INTERVAL_MAP.get(timeframe)
        if not interval:
            raise MarketDataError(f"twelvedata: unsupported timeframe {timeframe}")
        params = {
            "symbol": self.normalize_symbol(symbol),
            "interval": interval,
            "outputsize": max(30, min(periods, 5000)),
            "timezone": "UTC",
            "apikey": self.api_key,
            "format": "JSON",
        }
        try:
            payload = self._get_json(self.base_url, params)
            values = payload.get("values") or []
            if not values:
                message = payload.get("message") or payload.get("status") or "no data"
                raise MarketDataError(f"twelvedata: {message}")
            df = pd.DataFrame(values).rename(columns={"datetime": "timestamp"})
            return self._validate_frame(df, symbol, timeframe, periods)
        except MarketDataError:
            raise
        except Exception as exc:  # pragma: no cover
            raise MarketDataError(f"twelvedata: request failed for symbol={symbol}, timeframe={timeframe}") from exc


class AlphaVantageProvider(BaseMarketDataProvider):
    name = "alphavantage"
    requires_api_key = True
    base_url = "https://www.alphavantage.co/query"

    def normalize_symbol(self, symbol: str) -> tuple[str, str] | None:
        mapped = SYMBOL_MAP.get((symbol or "XAUUSD").upper(), {})
        value = mapped.get("alphavantage")
        if value:
            return value
        text = (symbol or "XAUUSD").replace("/", "").upper()
        if len(text) == 6:
            return text[:3], text[3:]
        return None

    def supports(self, symbol: str, timeframe: str) -> bool:
        return super().supports(symbol, timeframe) and self.normalize_symbol(symbol) is not None

    def fetch(self, symbol: str, timeframe: str, periods: int) -> pd.DataFrame:
        normalized = self.normalize_symbol(symbol)
        if normalized is None:
            raise MarketDataError(f"alphavantage: unsupported symbol {symbol}")
        interval = ALPHA_INTERVAL_MAP.get(timeframe)
        if not interval:
            raise MarketDataError(f"alphavantage: unsupported timeframe {timeframe}")
        from_symbol, to_symbol = normalized
        params = {
            "function": "FX_INTRADAY",
            "from_symbol": from_symbol,
            "to_symbol": to_symbol,
            "interval": interval,
            "outputsize": "full",
            "apikey": self.api_key,
        }
        try:
            payload = self._get_json(self.base_url, params)
            series_key = next((key for key in payload.keys() if key.startswith("Time Series FX")), None)
            if not series_key:
                message = payload.get("Error Message") or payload.get("Note") or payload.get("Information") or "no data"
                raise MarketDataError(f"alphavantage: {message}")
            records = [
                {
                    "timestamp": stamp,
                    "open": values.get("1. open"),
                    "high": values.get("2. high"),
                    "low": values.get("3. low"),
                    "close": values.get("4. close"),
                    "volume": 0,
                }
                for stamp, values in (payload.get(series_key) or {}).items()
            ]
            df = pd.DataFrame(records)
            validated = self._validate_frame(df, symbol, timeframe, max(periods * (4 if timeframe == "4h" else 1), periods))
            if timeframe == "4h":
                validated = self._resample_to_4h(validated, periods)
            return validated.tail(periods).reset_index(drop=True)
        except MarketDataError:
            raise
        except Exception as exc:  # pragma: no cover
            raise MarketDataError(f"alphavantage: request failed for symbol={symbol}, timeframe={timeframe}") from exc

    def _resample_to_4h(self, df: pd.DataFrame, periods: int) -> pd.DataFrame:
        work = df.set_index("timestamp").sort_index()
        agg = work.resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
        agg = agg.reset_index()
        return self._validate_frame(agg, "resampled", "4h", periods)


class MarketDataService:
    """Fetch and cache real market candles using multiple providers."""

    def __init__(self) -> None:
        self.providers = self._build_providers()
        self.last_fetch_report = MarketDataFetchReport(symbol="", timeframe="", periods=0)

    def _build_providers(self) -> list[BaseMarketDataProvider]:
        available: dict[str, BaseMarketDataProvider] = {
            "twelvedata": TwelveDataProvider(settings.twelvedata_api_key),
            "alphavantage": AlphaVantageProvider(settings.alphavantage_api_key),
            "yfinance": YahooFinanceProvider(),
        }
        ordered_names = [name.strip().lower() for name in settings.market_data_provider_order.split(",") if name.strip()]
        if not ordered_names:
            ordered_names = ["twelvedata", "alphavantage", "yfinance"]
        providers: list[BaseMarketDataProvider] = []
        for name in ordered_names:
            provider = available.get(name)
            if provider is None:
                logger.warning("market_data_unknown_provider provider=%s", name)
                continue
            providers.append(provider)
        if "yfinance" not in [provider.name for provider in providers]:
            providers.append(available["yfinance"])
        return providers

    def normalize_symbol(self, symbol: str, provider_name: str = "yfinance") -> str:
        provider = next((item for item in self.providers if item.name == provider_name), None) or YahooFinanceProvider()
        value = provider.normalize_symbol(symbol)
        if isinstance(value, tuple):
            return "/".join(value)
        return value or symbol

    def provider_names(self) -> list[str]:
        return [provider.name for provider in self.providers]

    def cache_stats(self) -> dict[str, int]:
        return db.stats()

    def get_last_fetch_report(self) -> dict[str, Any]:
        return self.last_fetch_report.to_dict()

    def fetch(self, symbol: str, timeframe: str, periods: int = 800) -> pd.DataFrame:
        if timeframe not in INTERVAL_MAP:
            raise MarketDataError(f"Unsupported timeframe: {timeframe}")
        started = perf_counter()
        report = MarketDataFetchReport(symbol=symbol, timeframe=timeframe, periods=periods)
        cached = self._read_cache(symbol, timeframe, periods)
        if cached is not None:
            report.provider = cached[1]
            report.cache_hit = True
            report.candles = len(cached[0])
            report.duration_ms = (perf_counter() - started) * 1000
            self.last_fetch_report = report
            return cached[0]

        for provider in self.providers:
            if not provider.is_enabled():
                report.failures.append(ProviderFailure(provider.name, "provider not configured"))
                continue
            if not provider.supports(symbol, timeframe):
                report.failures.append(ProviderFailure(provider.name, f"unsupported symbol/timeframe: {symbol} {timeframe}"))
                continue
            try:
                frame = self._fetch_from_provider(provider, symbol, timeframe, periods)
                report.provider = provider.name
                report.candles = len(frame)
                report.duration_ms = (perf_counter() - started) * 1000
                self._write_cache(symbol, timeframe, periods, provider.name, frame)
                self.last_fetch_report = report
                logger.info(
                    "market_data_provider_success provider=%s symbol=%s timeframe=%s candles=%s cache_hit=%s",
                    provider.name,
                    symbol,
                    timeframe,
                    len(frame),
                    False,
                )
                return frame
            except MarketDataError as exc:
                report.failures.append(ProviderFailure(provider.name, str(exc)))
                logger.warning("market_data_provider_failed provider=%s symbol=%s timeframe=%s reason=%s", provider.name, symbol, timeframe, exc)
        report.duration_ms = (perf_counter() - started) * 1000
        self.last_fetch_report = report
        details = {item.provider: item.reason for item in report.failures}
        raise MarketDataError(f"Unable to fetch market data for symbol={symbol}, timeframe={timeframe}, providers_tried={details}")


    def _fetch_from_provider(self, provider: BaseMarketDataProvider, symbol: str, timeframe: str, periods: int) -> pd.DataFrame:
        if timeframe != "3m":
            return provider.fetch(symbol, timeframe, periods)
        base = provider.fetch(symbol, "1m", max(periods * 4, periods + 12))
        return self._resample_frame(base, "3min", periods, provider.name, symbol, timeframe)

    def _resample_frame(self, df: pd.DataFrame, rule: str, periods: int, provider_name: str, symbol: str, timeframe: str) -> pd.DataFrame:
        work = df.copy()
        work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True, errors="coerce")
        work = work.dropna(subset=["timestamp"]).sort_values("timestamp").set_index("timestamp")
        agg = work.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()
        validated = next((p for p in self.providers if p.name == provider_name), None)
        if validated is None:
            validated = YahooFinanceProvider()
        return validated._validate_frame(agg, symbol, timeframe, periods)

    def trim_to_window(self, df: pd.DataFrame, constraints: dict | None) -> pd.DataFrame:
        if df.empty:
            return df
        work = df.copy()
        if constraints:
            start = constraints.get("backtest_start")
            end = constraints.get("backtest_end")
            if start:
                work = work[work["timestamp"] >= pd.to_datetime(start, utc=True)]
            if end:
                work = work[work["timestamp"] <= pd.to_datetime(end, utc=True)]
        return work.reset_index(drop=True)

    def _cache_key(self, symbol: str, timeframe: str, periods: int) -> str:
        raw = f"{symbol.upper()}|{timeframe}|{periods}|{settings.market_data_provider_order}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cache_ttl_seconds(self, timeframe: str) -> int:
        return settings.market_data_cache_ttl_daily_seconds if timeframe.endswith("d") else settings.market_data_cache_ttl_intraday_seconds

    def _read_cache(self, symbol: str, timeframe: str, periods: int) -> tuple[pd.DataFrame, str] | None:
        if not settings.market_data_cache_enabled:
            return None
        cache_key = self._cache_key(symbol, timeframe, periods)
        now = datetime.now(timezone.utc).isoformat()
        with db.connect() as conn:
            row = conn.execute(
                "SELECT payload, provider, expires_at FROM market_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if not row:
                return None
            if row["expires_at"] <= now:
                conn.execute("DELETE FROM market_cache WHERE cache_key = ?", (cache_key,))
                return None
        payload = json.loads(row["payload"])
        frame = pd.DataFrame(payload)
        if frame.empty:
            return None
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
        return frame, str(row["provider"])

    def _write_cache(self, symbol: str, timeframe: str, periods: int, provider: str, df: pd.DataFrame) -> None:
        if not settings.market_data_cache_enabled:
            return
        cache_key = self._cache_key(symbol, timeframe, periods)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=max(30, self._cache_ttl_seconds(timeframe)))
        payload_df = df.copy()
        payload_df["timestamp"] = payload_df["timestamp"].astype(str)
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO market_cache (cache_key, provider, symbol, timeframe, periods, cached_at, expires_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    provider = excluded.provider,
                    symbol = excluded.symbol,
                    timeframe = excluded.timeframe,
                    periods = excluded.periods,
                    cached_at = excluded.cached_at,
                    expires_at = excluded.expires_at,
                    payload = excluded.payload
                """,
                (
                    cache_key,
                    provider,
                    symbol.upper(),
                    timeframe,
                    periods,
                    now.isoformat(),
                    expires_at.isoformat(),
                    json.dumps(payload_df.to_dict(orient="records")),
                ),
            )
            conn.execute("DELETE FROM market_cache WHERE expires_at <= ?", (now.isoformat(),))
