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
from datetime import datetime, timezone
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

# Each symbol maps to the Yahoo tickers to try, in order of preference.
# Yahoo's coverage of metals is inconsistent - XAUUSD=X returned HTTP 404
# on every timeframe, which silently killed gold entirely - so the loader
# walks the list instead of betting on one spelling.
YAHOO_SYMBOLS = {
    # Gold is spot only. The COMEX future carries a basis of several
    # dollars against spot, which is exactly the "prices look wrong"
    # mismatch reported earlier, so it is not in this list.
    "XAUUSD": ("XAUUSD=X", "XAU=X"),
    "EURUSD": ("EURUSD=X",), "GBPUSD": ("GBPUSD=X",), "USDJPY": ("USDJPY=X",),
    "USDCHF": ("USDCHF=X",), "AUDUSD": ("AUDUSD=X",), "NZDUSD": ("NZDUSD=X",),
    "USDCAD": ("USDCAD=X",),
    "EURJPY": ("EURJPY=X",), "GBPJPY": ("GBPJPY=X",), "EURGBP": ("EURGBP=X",),
    "AUDJPY": ("AUDJPY=X",), "CADJPY": ("CADJPY=X",), "CHFJPY": ("CHFJPY=X",),
    # Indices come from the E-mini futures rather than the cash index.
    # A CFD desk prices NAS100/US30 off the future, and the cash index
    # only prints during the US session - overnight its chart is a flat
    # line, which is not what the reader is looking at. The cash index is
    # kept as a fallback for when the future cannot be reached.
    "NAS100": ("NQ=F", "^NDX"),
    "US30": ("YM=F", "^DJI"),
    # Crypto is its own market: one ticker, and it never closes.
    "BTCUSD": ("BTC-USD",),
}

# Instruments that trade around the clock, weekends included. The FX
# market-closed rules must not be applied to them.
ALWAYS_OPEN = frozenset({"BTCUSD"})

# Decimal places for display, by instrument. Five for FX majors is right
# and would be absurd on an index quoted in thousands.
DIGITS = {
    "XAUUSD": 2, "NAS100": 2, "US30": 2, "BTCUSD": 2,
}


def digits_for(symbol: str) -> int:
    """How many decimals this instrument is quoted to."""
    if symbol in DIGITS:
        return DIGITS[symbol]
    return 2 if "JPY" in symbol else 5


def always_open(symbol: str) -> bool:
    return symbol in ALWAYS_OPEN

# Non-spot stand-ins, tried only when explicitly enabled
# (ALLOW_GOLD_FUTURES). Off by default: no data is better than data from a
# different market presented as if it were the board.
PROXY_FALLBACKS = {"XAUUSD": ("GC=F",)}

# Tickers whose prices are not the spot market. If one is ever used, the
# fact travels with the frame and is stated in every message.
PROXY_SYMBOLS = {"GC=F": "ราคา COMEX futures ไม่ใช่ spot — อาจต่างจากกระดาน 2-10 จุด"}

# --- timeframe translation --------------------------------------------
TWELVEDATA_INTERVALS = {
    "M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min",
    "H1": "1h", "H4": "4h", "D1": "1day", "W1": "1week",
}
YAHOO_INTERVALS = {
    "M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m",
    "H1": "1h", "H4": "1h", "D1": "1d", "W1": "1wk",
}
# Yahoo has no native 4h bars, so H4 is resampled from its hourly series.
YAHOO_PERIODS = {
    "M1": "5d", "M5": "5d", "M15": "5d", "M30": "1mo",
    "H1": "60d", "H4": "60d", "D1": "1y", "W1": "5y",
}
RESAMPLE_RULE = {"H4": "4h"}      # timeframe -> pandas rule, when derived

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
    # Spot FX and metals carry no exchange volume, so Twelve Data omits the
    # column entirely for them. df.get() would hand back the scalar default
    # rather than a Series, which is not something fillna can be called on.
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    else:
        df["volume"] = 0.0
    return df[["open", "high", "low", "close", "volume"]].dropna()


def _yahoo_one(yf_symbol: str, symbol: str, timeframe: str) -> pd.DataFrame:
    """Fetch a single Yahoo ticker. Raises DataError on anything unusable."""
    resp = requests.get(
        _YF_URL.format(sym=yf_symbol), timeout=20,
        headers={"User-Agent": "Mozilla/5.0 (compatible; CapitalGuard/1.0)"},
        params={"interval": YAHOO_INTERVALS[timeframe],
                "range": YAHOO_PERIODS[timeframe]},
    )
    if not resp.ok:
        raise DataError(f"{yf_symbol}: HTTP {resp.status_code}")

    chart = resp.json().get("chart", {})
    if chart.get("error"):
        raise DataError(f"{yf_symbol}: {chart['error']}")
    results = chart.get("result")
    if not results:
        raise DataError(f"{yf_symbol}: empty response")

    result = results[0]
    quote = result["indicators"]["quote"][0]
    df = pd.DataFrame({
        "open": quote["open"], "high": quote["high"],
        "low": quote["low"], "close": quote["close"],
        "volume": quote.get("volume") or [0] * len(quote["open"]),
    }, index=pd.to_datetime(result["timestamp"], unit="s", utc=True))
    # Volume is missing on some instruments - a cash index reports nulls
    # rather than zeros. Left as NaN it would take the whole bar down with
    # it on the dropna below, deleting real OHLC over a field nothing here
    # gates on. Only a genuine price gap should cost a bar.
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    df = df.dropna().sort_index()

    # Yahoo has no 4-hour bars - build them from the hourly series
    rule = RESAMPLE_RULE.get(timeframe)
    if rule:
        df = df.resample(rule).agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum",
        }).dropna()
    return df


def _from_yahoo(symbol: str, timeframe: str,
                allow_proxy: bool = False) -> pd.DataFrame:
    """Load OHLC from Yahoo Finance's chart endpoint (no API key).

    Tries each mapped ticker in preference order and returns the first that
    yields enough bars, tagging the frame with the ticker that supplied it
    so a non-spot fallback can be disclosed downstream.

    Non-spot stand-ins are only tried when `allow_proxy` is set; otherwise
    a symbol whose spot tickers all fail simply has no data this run.
    """
    candidates = tuple(YAHOO_SYMBOLS.get(symbol, ()))
    if allow_proxy:
        candidates += tuple(PROXY_FALLBACKS.get(symbol, ()))
    if not candidates:
        raise DataError(f"{symbol}: not mapped for Yahoo Finance")

    errors = []
    for yf_symbol in candidates:
        try:
            df = _yahoo_one(yf_symbol, symbol, timeframe)
        except Exception as exc:            # noqa: BLE001 - try the next ticker
            errors.append(str(exc))
            continue
        if len(df) < MIN_BARS:
            errors.append(f"{yf_symbol}: only {len(df)} bars")
            continue
        df.attrs["yahoo_symbol"] = yf_symbol
        df.attrs["proxy_note"] = PROXY_SYMBOLS.get(yf_symbol, "")
        return df

    raise DataError(f"{symbol} {timeframe}: " + "; ".join(errors))


def load(symbol: str, timeframe: str, api_key: Optional[str] = None,
         allow_proxy: bool = False) -> pd.DataFrame:
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
                # Twelve Data's XAU/USD is spot, so no disclosure needed
                df.attrs["yahoo_symbol"] = TWELVEDATA_SYMBOLS.get(symbol, symbol)
                df.attrs["proxy_note"] = ""
                return df
            errors.append(f"twelvedata returned only {len(df)} bars")
        except Exception as exc:            # noqa: BLE001 - provider fallback
            errors.append(f"twelvedata: {exc}")

    try:
        df = _from_yahoo(symbol, timeframe, allow_proxy)
        if len(df) >= MIN_BARS:
            return df
        errors.append(f"yahoo returned only {len(df)} bars")
    except Exception as exc:                # noqa: BLE001 - provider fallback
        errors.append(f"yahoo: {exc}")

    raise DataError(f"{symbol} {timeframe}: " + "; ".join(errors))


class Cache:
    """Per-run cache of loaded series.

    Profiles overlap heavily - H1 is used by all three - so without this
    a single scan would re-download the same candles several times.
    """

    def __init__(self, api_key: Optional[str] = None, pause: float = 0.0,
                 allow_proxy: bool = False, key_symbols=None):
        self.api_key = api_key
        self.pause = pause
        self.allow_proxy = allow_proxy
        # Which symbols may spend the Twelve Data key. Its free plan allows
        # 800 requests a day; letting all eight symbols use it would need
        # roughly 16,000, so the key is reserved for the symbols Yahoo
        # cannot serve (gold spot). None = no restriction.
        self.key_symbols = set(key_symbols) if key_symbols is not None else None
        self._store: dict[tuple[str, str], pd.DataFrame] = {}
        self._errors: dict[tuple[str, str], str] = {}
        self.sources: dict[str, str] = {}       # symbol -> ticker actually used

    def get(self, symbol: str, timeframe: str) -> pd.DataFrame:
        key = (symbol, timeframe)
        if key in self._store:
            return self._store[key]
        if key in self._errors:                 # don't retry a known failure
            raise DataError(self._errors[key])
        may_use_key = (self.key_symbols is None or symbol in self.key_symbols)
        try:
            df = load(symbol, timeframe,
                      self.api_key if may_use_key else "", self.allow_proxy)
        except DataError as exc:
            self._errors[key] = str(exc)
            raise
        # record which ticker served this symbol so the run log can prove
        # whether gold came from spot or from a stand-in
        src = df.attrs.get("yahoo_symbol", "")
        if src and symbol not in self.sources:
            self.sources[symbol] = src
        self._store[key] = df
        if self.pause:
            time.sleep(self.pause)
        return df

    def frames(self, symbol: str, timeframes: list[str]) -> dict[str, pd.DataFrame]:
        """Load every timeframe a profile needs, reusing anything cached."""
        return {tf: self.get(symbol, tf) for tf in timeframes}

    def stats(self) -> str:
        return f"{len(self._store)} series cached, {len(self._errors)} failed"

    def source_report(self) -> str:
        """One line naming the ticker behind each symbol, proxies flagged."""
        parts = []
        for sym, src in sorted(self.sources.items()):
            parts.append(f"{sym}={src}" + (" [PROXY]" if src in PROXY_SYMBOLS else ""))
        return " ".join(parts)


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


# how stale a feed may be, per timeframe, before it is worth flagging.
# Free feeds are typically delayed a little; well beyond this means the
# series has actually stopped updating.
_STALE_LIMIT_MIN = {"M1": 15, "M5": 30, "M15": 60, "M30": 120,
                    "H1": 180, "H4": 600, "D1": 2880, "W1": 20160}


def freshness(df: pd.DataFrame, timeframe: str) -> tuple:
    """(age_in_minutes, is_stale) of the most recent bar.

    Lets the briefing state plainly how current the quotes are instead of
    presenting delayed data as if it were live.
    """
    if df.empty:
        return float("inf"), True
    last = df.index[-1].to_pydatetime()
    age = (datetime.now(timezone.utc) - last).total_seconds() / 60.0
    return age, age > _STALE_LIMIT_MIN.get(timeframe, 120)
