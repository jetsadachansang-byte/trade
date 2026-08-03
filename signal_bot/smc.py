"""Smart Money Concepts analysis, ported from the MQL5 modules.

Everything here operates on OHLC DataFrames indexed by UTC timestamp,
ascending. The last row is the forming bar, so analysis uses `.iloc[-2]`
as the most recent CLOSED bar - the same convention as the MQL5 version.

Detectors are deliberately rule-based and conservative:
  structure  - swing highs/lows, HH/HL vs LH/LL, BOS, CHoCH
  liquidity  - equal highs/lows (BSL/SSL pools), liquidity sweeps
  order block- last opposite candle before an impulse, quality scored
  fvg        - 3-candle imbalance with mitigation state
  zones      - premium / discount position inside the dealing range
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

UPTREND, DOWNTREND, SIDEWAYS = "UP", "DOWN", "SIDE"


# ----------------------------------------------------------------------
# Market structure
# ----------------------------------------------------------------------
@dataclass
class Structure:
    """Swing structure of one timeframe."""
    last_high: float = 0.0
    last_low: float = 0.0
    prev_high: float = 0.0
    prev_low: float = 0.0
    bias: int = 0                 # +1 bullish, -1 bearish, 0 unclear
    trend: str = SIDEWAYS         # HH/HL vs LH/LL classification
    recent_bos: bool = False      # structure break in the recent window
    recent_choch: bool = False    # direction flip (MSS)

    @property
    def direction(self) -> int:
        """Trend as a signed integer, falling back to the bias."""
        if self.trend == UPTREND:
            return 1
        if self.trend == DOWNTREND:
            return -1
        return self.bias


def _swing_points(df: pd.DataFrame, k: int) -> tuple[list[int], list[int]]:
    """Indices of swing highs and lows confirmed by k bars either side."""
    high, low = df["high"].to_numpy(), df["low"].to_numpy()
    n = len(df)
    highs, lows = [], []
    for i in range(k, n - k - 1):          # skip the unconfirmed right edge
        window_h = high[i - k:i + k + 1]
        window_l = low[i - k:i + k + 1]
        if high[i] == window_h.max() and (window_h == high[i]).sum() == 1:
            highs.append(i)
        if low[i] == window_l.min() and (window_l == low[i]).sum() == 1:
            lows.append(i)
    return highs, lows


def analyse_structure(df: pd.DataFrame, swing_bars: int = 3,
                      lookback: int = 80) -> Structure:
    """Full structure read of one timeframe."""
    st = Structure()
    if len(df) < swing_bars * 2 + 10:
        return st

    highs, lows = _swing_points(df, swing_bars)
    if len(highs) < 2 or len(lows) < 2:
        return st

    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    st.last_high, st.prev_high = h[highs[-1]], h[highs[-2]]
    st.last_low, st.prev_low = l[lows[-1]], l[lows[-2]]

    # HH/HL vs LH/LL classification
    if st.last_high > st.prev_high and st.last_low > st.prev_low:
        st.trend = UPTREND
    elif st.last_high < st.prev_high and st.last_low < st.prev_low:
        st.trend = DOWNTREND
    else:
        st.trend = SIDEWAYS

    # Most recent structure break: a close through a swing formed before it
    last_closed = len(df) - 2
    start = max(swing_bars + 1, last_closed - lookback)
    bos_dir, bos_bars_ago = 0, -1
    for i in range(last_closed, start, -1):
        for idx in reversed(highs):
            if idx < i and c[i] > h[idx] >= c[i - 1]:
                bos_dir, bos_bars_ago = 1, last_closed - i
                break
        if bos_dir:
            break
        for idx in reversed(lows):
            if idx < i and c[i] < l[idx] <= c[i - 1]:
                bos_dir, bos_bars_ago = -1, last_closed - i
                break
        if bos_dir:
            break

    st.bias = bos_dir if bos_dir else (
        1 if st.trend == UPTREND else -1 if st.trend == DOWNTREND else 0)
    st.recent_bos = 0 <= bos_bars_ago <= 10

    # CHoCH: the break opposes the swing pattern that preceded it
    if st.recent_bos:
        was_down = st.last_high < st.prev_high
        was_up = st.last_low > st.prev_low
        st.recent_choch = (bos_dir == 1 and was_down) or (bos_dir == -1 and was_up)
    return st


# ----------------------------------------------------------------------
# Smart money: liquidity, order blocks, fair value gaps, zones
# ----------------------------------------------------------------------
@dataclass
class OrderBlock:
    valid: bool = False
    top: float = 0.0
    bottom: float = 0.0
    quality: float = 0.0          # 0-100
    bars_ago: int = 0
    touches: int = 0
    mitigating: bool = False


@dataclass
class SmartMoney:
    equal_highs: bool = False
    equal_lows: bool = False
    bsl_above: bool = False       # untapped buy-side liquidity above price
    ssl_below: bool = False       # untapped sell-side liquidity below price
    sweep_bull: bool = False      # sell-side grabbed and reclaimed
    sweep_bear: bool = False      # buy-side grabbed and rejected
    ob_bull: OrderBlock = field(default_factory=OrderBlock)
    ob_bear: OrderBlock = field(default_factory=OrderBlock)
    fvg_bull: bool = False
    fvg_bear: bool = False
    fvg_bull_mitigated: bool = False
    fvg_bear_mitigated: bool = False
    range_pos: float = 0.5        # 0 = range low, 1 = range high


def atr(df: pd.DataFrame, period: int = 14) -> float:
    """Wilder-style ATR of the last closed bar."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)
    series = tr.ewm(alpha=1 / period, adjust=False).mean()
    return float(series.iloc[-2])


def analyse_smart_money(df: pd.DataFrame, st: Structure, atr_value: float,
                        window: int = 30) -> SmartMoney:
    """Detect liquidity, order blocks, FVGs and the premium/discount zone."""
    smc = SmartMoney()
    if atr_value <= 0 or len(df) < window + 10:
        return smc

    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy();  c = df["close"].to_numpy()
    v = df["volume"].to_numpy()
    last = len(df) - 2                 # last closed bar
    start = max(3, last - window)
    price = c[last]
    avg_vol = v[start:last + 1].mean() if v[start:last + 1].size else 0.0

    # --- premium / discount inside the dealing range ------------------
    if st.last_high > st.last_low > 0:
        smc.range_pos = float(np.clip(
            (price - st.last_low) / (st.last_high - st.last_low), 0.0, 1.0))

    # --- liquidity pools: equal highs / equal lows --------------------
    tol = 0.15 * atr_value

    def _local_high(i: int) -> bool:
        return h[i] > h[i - 1] and h[i] > h[i - 2] and h[i] > h[i + 1] and h[i] > h[i + 2]

    def _local_low(i: int) -> bool:
        return l[i] < l[i - 1] and l[i] < l[i - 2] and l[i] < l[i + 1] and l[i] < l[i + 2]

    highs = [i for i in range(start, last - 1) if _local_high(i)]
    lows = [i for i in range(start, last - 1) if _local_low(i)]
    for a in range(len(highs)):
        for b in range(a + 1, len(highs)):
            if abs(h[highs[a]] - h[highs[b]]) <= tol:
                smc.equal_highs = True
                if max(h[highs[a]], h[highs[b]]) > price:
                    smc.bsl_above = True
                break
        if smc.equal_highs:
            break
    for a in range(len(lows)):
        for b in range(a + 1, len(lows)):
            if abs(l[lows[a]] - l[lows[b]]) <= tol:
                smc.equal_lows = True
                if min(l[lows[a]], l[lows[b]]) < price:
                    smc.ssl_below = True
                break
        if smc.equal_lows:
            break

    # --- liquidity sweeps ---------------------------------------------
    sweep_bull_bar = sweep_bear_bar = -1
    for i in range(start, last + 1):
        if sweep_bull_bar < 0:
            for level in (st.last_low, st.prev_low):
                if level > 0 and l[i] < level < c[i]:
                    smc.sweep_bull, sweep_bull_bar = True, i
                    break
        if sweep_bear_bar < 0:
            for level in (st.last_high, st.prev_high):
                if level > 0 and h[i] > level > c[i]:
                    smc.sweep_bear, sweep_bear_bar = True, i
                    break

    # --- order blocks with quality scoring -----------------------------
    def _score_ob(i: int, bullish: bool, touches: int, sweep_bar: int) -> float:
        bars_ago = last - i
        q = 20.0 if bars_ago <= 10 else 12.0 if bars_ago <= 20 else 5.0
        q += 20.0 if touches <= 1 else 10.0 if touches == 2 else 0.0
        if avg_vol > 0:
            q += 20.0 if v[i] >= 1.2 * avg_vol else 10.0 if v[i] >= 0.8 * avg_vol else 0.0
        else:
            q += 10.0                      # no volume data: neutral credit
        in_zone = smc.range_pos <= 0.5 if bullish else smc.range_pos >= 0.5
        q += 20.0 if in_zone else 5.0
        q += 20.0 if sweep_bar >= 0 and abs(i - sweep_bar) <= 3 else 8.0
        return q

    for i in range(last - 3, start - 1, -1):        # bullish (demand) OB
        if c[i] >= o[i]:
            continue
        if not any(c[i + k] > h[i] + 0.5 * atr_value
                   for k in range(1, 4) if i + k <= last):
            continue
        bottom, top = l[i], h[i]
        touches, invalid = 0, False
        for j in range(i + 3, last + 1):
            if c[j] < bottom:
                invalid = True
                break
            if l[j] <= top:
                touches += 1
        if invalid:
            continue
        smc.ob_bull = OrderBlock(
            valid=True, top=top, bottom=bottom, bars_ago=last - i, touches=touches,
            mitigating=bool(l[last] <= top + 0.2 * atr_value and price >= bottom),
            quality=_score_ob(i, True, touches, sweep_bull_bar))
        break

    for i in range(last - 3, start - 1, -1):        # bearish (supply) OB
        if c[i] <= o[i]:
            continue
        if not any(c[i + k] < l[i] - 0.5 * atr_value
                   for k in range(1, 4) if i + k <= last):
            continue
        bottom, top = l[i], h[i]
        touches, invalid = 0, False
        for j in range(i + 3, last + 1):
            if c[j] > top:
                invalid = True
                break
            if h[j] >= bottom:
                touches += 1
        if invalid:
            continue
        smc.ob_bear = OrderBlock(
            valid=True, top=top, bottom=bottom, bars_ago=last - i, touches=touches,
            mitigating=bool(h[last] >= bottom - 0.2 * atr_value and price <= top),
            quality=_score_ob(i, False, touches, sweep_bear_bar))
        break

    # --- fair value gaps with mitigation state -------------------------
    for i in range(start, last + 1):                # bullish FVG
        gap_bottom, gap_top = h[i - 2], l[i]
        if gap_top <= gap_bottom or gap_top - gap_bottom < 0.2 * atr_value:
            continue
        invalidated = any(c[j] < gap_bottom for j in range(i + 1, last + 1))
        if invalidated or price <= gap_bottom:
            continue
        smc.fvg_bull = True
        smc.fvg_bull_mitigated = any(l[j] <= gap_top for j in range(i + 1, last + 1))
        break

    for i in range(start, last + 1):                # bearish FVG
        gap_top, gap_bottom = l[i - 2], h[i]
        if gap_top <= gap_bottom or gap_top - gap_bottom < 0.2 * atr_value:
            continue
        invalidated = any(c[j] > gap_top for j in range(i + 1, last + 1))
        if invalidated or price >= gap_top:
            continue
        smc.fvg_bear = True
        smc.fvg_bear_mitigated = any(h[j] >= gap_bottom for j in range(i + 1, last + 1))
        break

    return smc


# ----------------------------------------------------------------------
# Indicators - confirmation only, never a reason to trade
# ----------------------------------------------------------------------
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def macd(series: pd.Series) -> tuple[float, float]:
    """MACD main and signal at the last closed bar."""
    main = ema(series, 12) - ema(series, 26)
    signal = main.ewm(span=9, adjust=False).mean()
    return float(main.iloc[-2]), float(signal.iloc[-2])


def ema_trend(df: pd.DataFrame) -> int:
    """EMA-stack direction of the last closed bar: +1, -1 or 0."""
    close = df["close"]
    if len(close) < 200:
        return 0
    c = float(close.iloc[-2])
    e20 = float(ema(close, 20).iloc[-2])
    e50 = float(ema(close, 50).iloc[-2])
    e200 = float(ema(close, 200).iloc[-2])
    if c > e20 > e50 > e200:
        return 1
    if c < e20 < e50 < e200:
        return -1
    if c > e50 > e200:
        return 1
    if c < e50 < e200:
        return -1
    return 0


def volume_ratio(df: pd.DataFrame, bars: int = 20) -> float:
    """Last closed bar's volume against its recent average."""
    vol = df["volume"].iloc[-(bars + 2):-1]
    if vol.empty or vol.mean() == 0:
        return 1.0
    return float(vol.iloc[-1] / vol.mean())
