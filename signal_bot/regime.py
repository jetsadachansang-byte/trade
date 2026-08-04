"""LEVEL 2 - market regime detection.

Every regime here is measured off price and range behaviour, never
asserted. Each detector returns evidence, the strongest evidence names the
regime, and the confidence is how far ahead of the runner-up it finished -
so a marginal call reports itself as marginal instead of pretending.

Regimes the spec lists that need data this system does not have (real
order flow for institutional buying/selling, tick volume for genuine
Wyckoff phase work on FX) are deliberately absent rather than faked.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import smc as S

# --- regime vocabulary -------------------------------------------------
STRONG_BULL, WEAK_BULL = "Strong Bull", "Weak Bull"
STRONG_BEAR, WEAK_BEAR = "Strong Bear", "Weak Bear"
RANGE, COMPRESSION, EXPANSION = "Range", "Compression", "Expansion"
LIQUIDITY_HUNT = "Liquidity Hunt"
TREND_EXHAUSTION = "Trend Exhaustion"
MEAN_REVERSION = "Mean Reversion"
BREAKOUT_TRUE, BREAKOUT_FALSE = "True Breakout", "False Breakout"
NEWS_DRIVEN = "News Driven"

# Regimes that behave like a trend, for downstream strategy selection.
TRENDING = {STRONG_BULL, WEAK_BULL, STRONG_BEAR, WEAK_BEAR,
            EXPANSION, BREAKOUT_TRUE}
RANGING = {RANGE, COMPRESSION, MEAN_REVERSION, BREAKOUT_FALSE}


@dataclass
class Regime:
    """What kind of market this is, and how sure we are."""
    name: str = RANGE
    confidence: float = 0.0        # 0-100, gap between winner and runner-up
    volatility: str = "normal"     # low / normal / high
    direction: int = 0             # +1 up, -1 down, 0 none
    evidence: list = field(default_factory=list)
    scores: dict = field(default_factory=dict)
    runner_up: str = ""

    @property
    def is_trending(self) -> bool:
        return self.name in TRENDING

    @property
    def is_ranging(self) -> bool:
        return self.name in RANGING


def _slope(series: pd.Series, bars: int = 48) -> float:
    """Normalised slope of a series - how hard it is travelling, not where."""
    window = series.iloc[-bars:]
    if len(window) < 3:
        return 0.0
    x = np.arange(len(window), dtype=float)
    y = window.to_numpy(dtype=float)
    slope = float(np.polyfit(x, y, 1)[0])
    scale = float(np.mean(np.abs(y))) or 1.0
    return slope / scale * 1000.0


def _directional_strength(df: pd.DataFrame, bars: int = 48) -> float:
    """0..1 - how much of the range was actually travelled in one direction.

    A market that covers ground without retracing scores high; one that
    thrashes inside the same band scores low. This is the trend/range
    discriminator, and it needs no indicator to compute.

    The window is deliberately wide: measured over twenty bars, one leg of
    an oscillation looks exactly like a trend, and a ranging market was
    being reported as Strong Bull because of it.
    """
    window = df.iloc[-bars:]
    if len(window) < 3:
        return 0.0
    net = abs(float(window["close"].iloc[-1]) - float(window["close"].iloc[0]))
    path = float(window["close"].diff().abs().sum())
    return 0.0 if path <= 0 else min(1.0, net / path)


def _volatility_state(df: pd.DataFrame, atr: float) -> tuple:
    """(label, ratio) of current range against its own recent normal."""
    spans = df["high"].sub(df["low"]).rolling(50).mean()
    if len(spans) < 51 or not atr:
        return "normal", 1.0
    baseline = float(spans.iloc[-2])
    if baseline <= 0:
        return "normal", 1.0
    ratio = atr / baseline
    label = "high" if ratio > 1.4 else "low" if ratio < 0.6 else "normal"
    return label, round(ratio, 2)


def detect(df: pd.DataFrame, structure: S.Structure, sm: S.SmartMoney,
           atr: float, news_active: bool = False) -> Regime:
    """Classify the market this chart is in right now."""
    reg = Regime()
    if len(df) < 30:
        reg.evidence.append("แท่งเทียนไม่พอสำหรับระบุสภาพตลาด")
        return reg

    strength = _directional_strength(df)
    slope = _slope(df["close"])
    vol_label, vol_ratio = _volatility_state(df, atr)
    reg.volatility = vol_label

    trend_dir = (1 if structure.trend == S.UPTREND
                 else -1 if structure.trend == S.DOWNTREND else 0)
    if trend_dir == 0:
        trend_dir = 1 if slope > 0.4 else -1 if slope < -0.4 else 0
    reg.direction = trend_dir

    swept = sm.sweep_bull or sm.sweep_bear
    pooled = sm.equal_highs or sm.equal_lows
    at_edge = sm.range_pos > 0.8 or sm.range_pos < 0.2

    # --- evidence for each candidate regime --------------------------
    s: dict = {}
    up, down = trend_dir > 0, trend_dir < 0

    s[STRONG_BULL] = (strength * 60 + max(0.0, slope) * 12) if up else 0.0
    s[STRONG_BEAR] = (strength * 60 + max(0.0, -slope) * 12) if down else 0.0
    s[WEAK_BULL] = (35 + strength * 20) if up and strength < 0.45 else 0.0
    s[WEAK_BEAR] = (35 + strength * 20) if down and strength < 0.45 else 0.0

    s[RANGE] = (1.0 - strength) * 70 + (15 if not structure.recent_bos else 0)
    s[COMPRESSION] = (1.0 - strength) * 40 + (45 if vol_label == "low" else 0)
    s[EXPANSION] = strength * 30 + (50 if vol_label == "high" else 0)

    s[LIQUIDITY_HUNT] = (45 if swept else 0) + (25 if pooled else 0) + \
                        (15 if structure.recent_choch else 0)
    s[TREND_EXHAUSTION] = ((30 if at_edge else 0) +
                           (30 if structure.recent_choch else 0) +
                           (20 if trend_dir and strength < 0.3 else 0))
    s[MEAN_REVERSION] = ((1.0 - strength) * 45 +
                         (30 if at_edge and not structure.recent_bos else 0))
    s[BREAKOUT_TRUE] = (45 if structure.recent_bos and strength > 0.5 else 0) + \
                       (20 if vol_label == "high" else 0)
    s[BREAKOUT_FALSE] = (40 if swept and structure.recent_choch else 0) + \
                        (25 if at_edge and strength < 0.35 else 0)
    s[NEWS_DRIVEN] = (60 if news_active else 0) + (20 if vol_label == "high" else 0)

    reg.scores = {k: round(v, 1) for k, v in s.items() if v > 0}
    ranked = sorted(s.items(), key=lambda kv: -kv[1])
    reg.name = ranked[0][0]
    reg.runner_up = ranked[1][0] if len(ranked) > 1 else ""

    top, second = ranked[0][1], (ranked[1][1] if len(ranked) > 1 else 0.0)
    if top <= 0:
        reg.name, reg.confidence = RANGE, 0.0
    else:
        # confident only when the winner is clearly ahead of the next idea
        reg.confidence = round(min(100.0, (top - second) / top * 100.0 * 0.6
                                   + min(top, 100.0) * 0.4), 1)

    reg.evidence = [
        f"ทิศทางเดินทางจริง {strength:.0%} ของระยะที่แกว่ง",
        f"ความชัน {slope:+.2f} · ความผันผวน {vol_label} ({vol_ratio}x ปกติ)",
        f"ตำแหน่งในกรอบ {sm.range_pos:.2f}"
        + (" · ชิดขอบกรอบ" if at_edge else ""),
    ]
    if structure.recent_bos:
        reg.evidence.append("มี BOS ล่าสุด")
    if structure.recent_choch:
        reg.evidence.append("มี CHoCH ล่าสุด")
    if swept:
        reg.evidence.append("เพิ่งกวาดสภาพคล่อง")
    if news_active:
        reg.evidence.append("อยู่ในช่วงข่าวแรง")
    return reg
