"""Chart images from chart-img.com, to go with the written analysis.

The analysis says where the levels are; a picture says what the shape
looks like. They answer different halves of the same question, so the
image is sent alongside the text rather than instead of it - the text is
still complete on its own, and a run where the image cannot be fetched
loses a picture, never an analysis.

The API renders TradingView charts. Two things it needs that the rest of
the bot does not have:

  - a TradingView symbol. "XAUUSD" is not one; "OANDA:XAUUSD" is. The
    exchange prefix decides whose book the chart shows, so it is mapped
    explicitly per instrument rather than guessed, and every entry can be
    overridden from the environment when a different venue reads better.
  - an interval in TradingView's spelling, which is not the bot's.

The key lives in the environment (CHART_IMG_KEY) and is never written to
a file, logged, or put in a URL - it travels in a header, so it does not
end up in anybody's access log.
"""
from __future__ import annotations

import os

import requests

API_URL = "https://api.chart-img.com/v2/tradingview/advanced-chart"

# Internal symbol -> TradingView symbol. OANDA carries every FX pair, the
# metals and the index CFDs the bot follows, which keeps one venue behind
# almost everything and the charts consistent with each other. Crypto is
# quoted against real dollars rather than a stablecoin so the picture
# matches the BTC-USD series the analysis is computed from.
TV_SYMBOLS = {
    "XAUUSD": "OANDA:XAUUSD",
    "NAS100": "OANDA:NAS100USD",
    "US30": "OANDA:US30USD",
    "BTCUSD": "COINBASE:BTCUSD",
    "EURUSD": "OANDA:EURUSD", "GBPUSD": "OANDA:GBPUSD",
    "USDJPY": "OANDA:USDJPY", "USDCHF": "OANDA:USDCHF",
    "AUDUSD": "OANDA:AUDUSD", "NZDUSD": "OANDA:NZDUSD",
    "USDCAD": "OANDA:USDCAD",
    "EURJPY": "OANDA:EURJPY", "GBPJPY": "OANDA:GBPJPY",
    "EURGBP": "OANDA:EURGBP", "AUDJPY": "OANDA:AUDJPY",
    "CADJPY": "OANDA:CADJPY", "CHFJPY": "OANDA:CHFJPY",
}

# The bot's timeframe names in TradingView's spelling.
TV_INTERVALS = {
    "M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m",
    "H1": "1h", "H4": "4h", "D1": "1D", "W1": "1W",
}


class ChartError(RuntimeError):
    """The image could not be fetched. Never fatal - the text still goes."""


def tv_symbol(symbol: str) -> str:
    """The TradingView name for one instrument, overridable per symbol.

    CHART_SYMBOL_XAUUSD=FOREXCOM:XAUUSD switches venue without a code
    change, which matters because the "right" exchange is a matter of
    which board the reader actually trades on.
    """
    override = os.getenv(f"CHART_SYMBOL_{symbol.upper()}", "").strip()
    if override:
        return override
    return TV_SYMBOLS.get(symbol, symbol)


def fetch(symbol: str, timeframe: str, api_key: str, *,
          theme: str = "dark", width: int = 800, height: int = 500,
          timeout: int = 25) -> bytes:
    """One chart image as PNG bytes, or ChartError with the reason.

    The key goes in a header rather than the query string on purpose: a
    URL is logged by every hop it passes through, and a chart URL is the
    kind of thing that ends up pasted into a chat.
    """
    if not api_key:
        raise ChartError("ไม่ได้ตั้งค่า CHART_IMG_KEY")
    interval = TV_INTERVALS.get(timeframe)
    if interval is None:
        raise ChartError(f"ไม่รู้จักไทม์เฟรม {timeframe}")

    params = {
        "symbol": tv_symbol(symbol),
        "interval": interval,
        "theme": theme,
        "width": width,
        "height": height,
    }
    studies = [s.strip() for s in
               os.getenv("CHART_STUDIES", "").split(",") if s.strip()]
    if studies:
        params["studies"] = studies

    try:
        resp = requests.get(API_URL, params=params, timeout=timeout,
                            headers={"x-api-key": api_key,
                                     "accept": "image/png"})
    except Exception as exc:            # noqa: BLE001 - degraded, not fatal
        raise ChartError(f"ต่อ chart-img ไม่ได้: {exc}") from exc

    if resp.status_code == 200 and resp.content[:8].startswith(b"\x89PNG"):
        return resp.content
    if resp.status_code == 200:
        # 200 with something that is not an image means the service
        # answered with an explanation rather than a chart.
        raise ChartError(f"ตอบกลับไม่ใช่รูปภาพ: {_detail(resp)[:120]}")
    raise ChartError(f"HTTP {resp.status_code}: {_detail(resp)[:120]}")


def _detail(resp) -> str:
    try:
        payload = resp.json()
    except ValueError:
        return (resp.text or "")[:200]
    for key in ("message", "error", "detail"):
        if isinstance(payload, dict) and payload.get(key):
            return str(payload[key])
    return str(payload)[:200]


def quota_exhausted(reason: str) -> bool:
    """Is this failure the daily allowance rather than a broken call?

    Worth telling apart: a bad symbol is a bug to fix, a spent quota is a
    plan to upgrade or a list to trim, and retrying the second one
    sixteen more times in the same run helps nobody.
    """
    low = (reason or "").lower()
    return ("429" in low or "402" in low or "quota" in low
            or "limit" in low or "exceed" in low)
