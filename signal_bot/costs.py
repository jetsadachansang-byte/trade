"""What it costs to be in the market, and how tight a stop may honestly be.

The free OHLC feeds carry no bid/ask, so the bot had no idea what a trade
costs to open. Stop distance came from ATR alone, and on a one-minute
chart of a quiet pair that produced stops smaller than the spread: a
CADJPY signal went out with a one-point stop against a spread perhaps
twenty-five times wider, and its three take profits rounded to the same
price. A stop inside the spread is not a tight stop, it is a losing trade
that has already happened.

Two floors are enforced here, and a stop has to clear both.

The first is measured: the median full range of a recent candle on the
timeframe being entered. A stop closer than one ordinary candle is taken
out by one ordinary candle, whatever the structure says.

The second is a cost estimate. Spread cannot be measured without a feed
that quotes it, so these are typical retail values under normal
conditions - stated as estimates, widened when volatility is high, and
overridable, but never presented as a measured spread. Getting this
roughly right beats ignoring it exactly.
"""
from __future__ import annotations

import os

import pandas as pd

# Typical retail spread in price units, under normal conditions. Not
# measured - see the module docstring. Real spreads widen around news, at
# the daily rollover, and in thin hours.
TYPICAL_SPREAD = {
    "XAUUSD": 0.25,
    "EURUSD": 0.00015, "GBPUSD": 0.00020, "AUDUSD": 0.00018,
    "NZDUSD": 0.00025, "USDCAD": 0.00022, "USDCHF": 0.00020,
    "EURGBP": 0.00020,
    "USDJPY": 0.015, "EURJPY": 0.020, "GBPJPY": 0.030,
    "AUDJPY": 0.025, "CADJPY": 0.030, "CHFJPY": 0.035,
    # Index CFDs are quoted in points; crypto spreads are far wider than
    # anything in FX and move with the book.
    "NAS100": 1.5, "US30": 3.0, "BTCUSD": 12.0,
}

# Spreads do not stay put when the market moves fast.
VOL_WIDENING = {"high": 2.0, "normal": 1.0, "low": 0.9}


def spread_of(symbol: str, volatility: str = "normal") -> float:
    """Estimated spread for one symbol, in price units.

    An env override (SPREAD_XAUUSD=0.30) wins, so a broker with a very
    different book can be configured without a code change.
    """
    override = os.getenv(f"SPREAD_{symbol.upper()}", "").strip()
    if override:
        try:
            base = float(override)
        except ValueError:
            base = 0.0
        if base > 0:
            return base * VOL_WIDENING.get(volatility, 1.0)

    if symbol in TYPICAL_SPREAD:
        base = TYPICAL_SPREAD[symbol]
    elif "JPY" in symbol:
        base = 0.030
    else:
        base = 0.00025
    return base * VOL_WIDENING.get(volatility, 1.0)


def candle_noise(df: pd.DataFrame, bars: int = 50) -> float:
    """The median full range of a recent candle on this timeframe.

    Median rather than mean: one news spike should not decide how wide
    every stop has to be for the next hour.
    """
    if df is None or len(df) < 10:
        return 0.0
    spans = (df["high"] - df["low"]).iloc[-bars:-1].dropna()
    if spans.empty:
        return 0.0
    return float(spans.median())


def stop_floor(symbol: str, entry_df: pd.DataFrame, volatility: str = "normal",
               spread_multiple: float = 6.0, noise_multiple: float = 1.2,
               digits: int = 5) -> tuple:
    """(floor, why) - the smallest stop distance that is not self-defeating.

    `spread_multiple` is what the stop must be worth in spreads. At the
    default of six the round trip costs about a third of the risk, which is
    survivable; at two it is most of it. Raising it is the conservative
    direction, and a floor the style cannot carry means the instrument is
    simply not tradable on that timeframe right now.
    """
    spread = spread_of(symbol, volatility)
    noise = candle_noise(entry_df)
    point = 10.0 ** -digits

    by_cost = spread * spread_multiple
    by_noise = noise * noise_multiple
    # A ladder of three targets cannot survive rounding to the instrument's
    # own resolution if the whole risk is only a few ticks wide. Ten ticks
    # is enough for the levels to stay apart; the spread floor above is what
    # actually decides the distance on any instrument that matters.
    by_resolution = point * 10

    floor = max(by_cost, by_noise, by_resolution)
    driver = ("สเปรด" if floor == by_cost else
              "ความกว้างของแท่งเทียน" if floor == by_noise else
              "ความละเอียดราคา")
    why = (f"SL ขั้นต่ำจาก{driver}: สเปรดประมาณ {spread:.5g} × {spread_multiple:.0f} = "
           f"{by_cost:.5g} · แท่งเทียนกลาง {noise:.5g} × {noise_multiple:.1f} = "
           f"{by_noise:.5g}")
    return floor, why


def ladder_ok(entry: float, sl: float, tps, direction: int) -> bool:
    """Are the three targets still in order after rounding to the tick?"""
    if direction > 0:
        return sl < entry < tps[0] < tps[1] < tps[2]
    return sl > entry > tps[0] > tps[1] > tps[2]


def space_ladder(entry: float, tps, direction: int, digits: int) -> tuple:
    """Push targets apart until rounding cannot collapse them together.

    Rounding a ladder to the instrument's resolution is what turned three
    distinct take profits into the same price. Each level is nudged out by
    at least one tick from the one before, so a signal always carries three
    targets that are actually different prices.
    """
    point = 10.0 ** -digits
    step = point if direction > 0 else -point
    # Seed from the entry, not from the first target: rounding can put TP1
    # on the entry price itself, which is a target of zero.
    # Plain floats, not numpy scalars: these end up in the state file and
    # json.dumps refuses a numpy float64, which would break every save.
    out, previous = [], round(float(entry), digits)
    for tp in tps:
        nxt = round(float(tp), digits)
        if (direction > 0 and nxt <= previous) or (direction < 0 and nxt >= previous):
            nxt = round(previous + step, digits)
        out.append(nxt)
        previous = nxt
    return tuple(out)
