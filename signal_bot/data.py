"""Market data loading for the CapitalGuard signal bot.

Two sources are supported so the bot works with or without a signup:

  1. Twelve Data  - needs a free API key (TWELVEDATA_API_KEY). Reliable
                    from cloud IPs, 800 requests/day on the free plan.
  2. Yahoo Finance - no key at all, used automatically when no key is
                    set. Unofficial endpoint, occasionally rate limited.

Every loader returns the same shape: a DataFrame indexed by UTC
timestamp, ascending (oldest first), with columns
open / high / low / close / volume.
"""
from __future__ import annotations

import os
import time
from typing import Optional

import pandas as pd
import requests

# --- symbol translation ------------------------------------------------
# Internal names are the familiar FX/metal tickers. Each provider needs
# its own spelling.
TWELVEDATA_SYMBOLS = {
    "XAUUSD": "XAU/USD",
    "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD", "USDJPY": "USD/JPY",
    "USDCHF": "USD/CHF", "AUDUSD": "AUD/USD", "NZDUSD": "NZD/USD",
    "USDCAD": "USD/CAD",
    "EURJPY": "EUR/JPY", "GBPJPY": "GBP/JPY", "EURGBP": "EUR/GBP",
    "AUDJPY": "AUD/JPY", "CADJPY": "CAD/JPY", "CHFJPY": "CHF/JPY",
}

YAHOO_SYMBOLS = {
    "XAUUSD": "GC=F",           # COMEX gold futures - tracks spot closely
    "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "USDJPY=X",
    "USDCHF": "USDCHF=X", "AUDUSD": "AUDUSD=X", "NZDUSD": "NZDUSD=X",
    "USDCAD": "USDCAD=X",
    "EURJPY": "EURJPY=X", "GBPJPY": "GBPJPY=X", "EURGBP": "EURGBP=X",
    "AUDJPY": "AUDJPY=X", "CADJPY": "CADJPY=X", "CHFJPY": "CHFJPY=X",
}

# --- timeframe translation --------------------------------------------
TWELVEDATA_INTERVALS = {"M15": "15min", "H1": "1h", "H4": "4h", "D1": "1day"}
YAHOO_INTERVALS = {"M15": "15m", "H1": "1h", "H4": "1h", "D1": "1d"}
# Yahoo has no native 4h bars, so H4 is resampled from 1h.
YAHOO_PERIODS = {"M15": "5d", "H1": "60d", "H4": "60d", "D1": "1y"}

_TD_URL = "https://api.twelvedata.com/time_series"
_YF_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"

# how many bars each timeframe needs for the analysis to be meaningful
MIN_BARS = 120


class DataError(RuntimeError):
    """Raised when a symbol/timeframe cannot be loaded from any source."""


def _from_twelvedata(symbol: str, timeframe: str, api_key: str,
                     outputsize: int = 300) -> pd.DataFrame:
    """Load OHLC from Twelve Data."""
    td_symbol = TWELVEDATA_SYMBOLS.get(symbol)
    if td_symbol is None:
        raise DataError(f"{symbol}: not mapped for Twelve Data")

    resp = requests.get(_TD_URL, timeout=20, params={
        "symbol": td_symbol,
        "interval": TWELVEDATA_INTERVALS[timeframe],
        "outputsize": outputsize,
        "apikey": api_key,
        "format": "JSON",
    })
    payload = resp.json() if resp.ok else {}
    if payload.get("status") == "error":
        raise DataError(f"{symbol} {timeframe}: {payload.get('message')}")
    values = payload.get("values")
    if not values:
        raise DataError(f"{symbol} {timeframe}: empty response")

    df = pd.DataFrame(values)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.set_index("datetime").sort_index()
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["volume"] = pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0.0)
    return df[["open", "high", "low", "close", "volume"]].dropna()


def _from_yahoo(symbol: str, timeframe: str) -> pd.DataFrame:
    """Load OHLC from Yahoo Finance's chart endpoint (no API key)."""
    yf_symbol = YAHOO_SYMBOLS.get(symbol)
    if yf_symbol is None:
        raise DataError(f"{symbol}: not mapped for Yahoo Finance")

    resp = requests.get(
        _YF_URL.format(sym=yf_symbol), timeout=20,
        headers={"User-Agent": "Mozilla/5.0 (compatible; CapitalGuard/1.0)"},
        params={"interval": YAHOO_INTERVALS[timeframe],
                "range": YAHOO_PERIODS[timeframe]},
    )
    if not resp.ok:
        raise DataError(f"{symbol} {timeframe}: HTTP {resp.status_code}")

    chart = resp.json().get("chart", {})
    if chart.get("error"):
        raise DataError(f"{symbol} {timeframe}: {chart['error']}")
    results = chart.get("result")
    if not results:
        raise DataError(f"{symbol} {timeframe}: empty response")

    result = results[0]
    quote = result["indicators"]["quote"][0]
    df = pd.DataFrame({
        "open": quote["open"], "high": quote["high"],
        "low": quote["low"], "close": quote["close"],
        "volume": quote.get("volume") or [0] * len(quote["open"]),
    }, index=pd.to_datetime(result["timestamp"], unit="s", utc=True))
    df = df.dropna().sort_index()

    # Yahoo has no 4-hour bars - build them from the hourly series
    if timeframe == "H4":
        df = df.resample("4h").agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum",
        }).dropna()
    return df


def load(symbol: str, timeframe: str, api_key: Optional[str] = None) -> pd.DataFrame:
    """Load one symbol/timeframe, preferring Twelve Data when a key exists.

    Falls back to Yahoo Finance on any failure so a single flaky provider
    does not stop the whole scan.
    """
    api_key = api_key if api_key is not None else os.getenv("TWELVEDATA_API_KEY", "")
    errors = []

    if api_key:
        try:
            df = _from_twelvedata(symbol, timeframe, api_key)
            if len(df) >= MIN_BARS:
                return df
            errors.append(f"twelvedata returned only {len(df)} bars")
        except Exception as exc:            # noqa: BLE001 - provider fallback
            errors.append(f"twelvedata: {exc}")

    try:
        df = _from_yahoo(symbol, timeframe)
        if len(df) >= MIN_BARS:
            return df
        errors.append(f"yahoo returned only {len(df)} bars")
    except Exception as exc:                # noqa: BLE001 - provider fallback
        errors.append(f"yahoo: {exc}")

    raise DataError(f"{symbol} {timeframe}: " + "; ".join(errors))


def load_multi(symbol: str, timeframes: list[str],
               api_key: Optional[str] = None,
               pause: float = 0.0) -> dict[str, pd.DataFrame]:
    """Load several timeframes for one symbol.

    `pause` sleeps between requests to respect the Twelve Data free-plan
    limit of 8 requests per minute.
    """
    out: dict[str, pd.DataFrame] = {}
    for tf in timeframes:
        out[tf] = load(symbol, tf, api_key)
        if pause:
            time.sleep(pause)
    return out


def current_price(df: pd.DataFrame) -> float:
    """Latest close available (the forming bar)."""
    return float(df["close"].iloc[-1])
