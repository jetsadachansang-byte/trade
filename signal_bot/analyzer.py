"""The SMC decision pipeline: turns market data into a signal candidate.

Mirrors the MQL5 SymbolAnalyst: direction comes from market structure
(D1/H4/H1), never from indicators, and every pipeline step must pass
before a candidate is produced. The confidence score uses the same
weights as the MQL5 ScoringEngine:

    Market Structure 25 | Liquidity 20 | BOS/CHoCH 20 |
    Order Block 15 | FVG 10 | Volume 5 | Indicator confirmation 5
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from . import smc as S
from .config import Settings


@dataclass
class Candidate:
    """A fully specified signal, ready to be sent."""
    symbol: str
    tier: int
    direction: int                 # +1 buy, -1 sell
    entry: float
    entry_low: float
    entry_high: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    rr: float
    score: float
    timeframe: str
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)

    @property
    def side(self) -> str:
        return "BUY" if self.direction > 0 else "SELL"


@dataclass
class Rejection:
    """Why a symbol produced no signal - shown in the status report."""
    symbol: str
    stage: str
    detail: str


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _decide_direction(d1: S.Structure, h4: S.Structure, h1: S.Structure) -> int:
    """Direction from structure only. H4 and H1 must agree, D1 must not oppose."""
    dir_h4, dir_h1 = h4.direction, h1.direction
    if dir_h4 == 0 or dir_h1 == 0 or dir_h4 != dir_h1:
        return 0
    dir_d1 = 1 if d1.trend == S.UPTREND else -1 if d1.trend == S.DOWNTREND else 0
    if dir_d1 != 0 and dir_d1 != dir_h4:
        return 0
    return dir_h4


def _tf_support(st: S.Structure, direction: int) -> float:
    """How strongly one timeframe supports the direction (0..1)."""
    want = S.UPTREND if direction > 0 else S.DOWNTREND
    against = S.DOWNTREND if direction > 0 else S.UPTREND
    if st.trend == want:
        return 1.0
    if st.trend == against:
        return 0.0
    return 0.5 if st.bias == direction else 0.0


def _score_structure(direction: int, d1, h4, h1, entry) -> float:
    return _clamp(20 * _tf_support(d1, direction) + 30 * _tf_support(h4, direction)
                  + 30 * _tf_support(h1, direction) + 20 * _tf_support(entry, direction))


def _score_liquidity(direction: int, sm: S.SmartMoney) -> float:
    score = 0.0
    if direction > 0:
        score += 50 if sm.sweep_bull else 0
        score += 25 if sm.bsl_above else 0
        score += 25 if sm.range_pos <= 0.5 else 0
    else:
        score += 50 if sm.sweep_bear else 0
        score += 25 if sm.ssl_below else 0
        score += 25 if sm.range_pos >= 0.5 else 0
    return _clamp(score)


def _score_bos(direction: int, entry: S.Structure) -> float:
    score = 0.0
    if entry.bias == direction and entry.recent_bos:
        score += 60
    if entry.bias == direction and entry.recent_choch:
        score += 40
    return _clamp(score)


def _score_ob(ob: S.OrderBlock) -> float:
    if not ob.valid:
        return 0.0
    return _clamp(ob.quality if ob.mitigating else ob.quality * 0.5)


def _score_fvg(direction: int, sm: S.SmartMoney) -> float:
    exists = sm.fvg_bull if direction > 0 else sm.fvg_bear
    mitigated = sm.fvg_bull_mitigated if direction > 0 else sm.fvg_bear_mitigated
    return _clamp((50 if exists else 0) + (50 if mitigated else 0))


def _score_volume(direction: int, entry_df: pd.DataFrame) -> float:
    ratio = S.volume_ratio(entry_df)
    if ratio >= 1.5:
        score = 70.0
    elif ratio >= 1.1:
        score = 55.0
    elif ratio >= 0.8:
        score = 35.0
    else:
        score = 15.0
    # a rising close in the trade direction adds a little confirmation
    closes = entry_df["close"]
    if len(closes) > 6:
        slope = float(closes.iloc[-2] - closes.iloc[-6])
        if (slope > 0) == (direction > 0):
            score += 30.0
    return _clamp(score)


def _score_indicator(direction: int, entry_df: pd.DataFrame, h1_df: pd.DataFrame) -> float:
    score = 0.0
    if S.ema_trend(entry_df) == direction:
        score += 30
    r = float(S.rsi(entry_df["close"]).iloc[-2])
    if direction > 0 and 50 < r < 70:
        score += 35
    if direction < 0 and 30 < r < 50:
        score += 35
    main, signal = S.macd(h1_df["close"])
    if (main > signal) == (direction > 0):
        score += 35
    return _clamp(score)


def analyse(symbol: str, tier: int, frames: dict[str, pd.DataFrame],
            cfg: Settings) -> tuple[Optional[Candidate], Optional[Rejection]]:
    """Run the full SMC pipeline for one symbol.

    Returns (candidate, None) when every step passes and the score clears
    the tier threshold, otherwise (None, rejection).
    """
    entry_tf = cfg.entry_timeframe
    d1_df, h4_df, h1_df = frames["D1"], frames["H4"], frames["H1"]
    entry_df = frames[entry_tf]

    def reject(stage: str, detail: str):
        return None, Rejection(symbol, stage, detail)

    # --- structure on every timeframe ---------------------------------
    st_d1 = S.analyse_structure(d1_df, cfg.swing_bars, cfg.struct_lookback)
    st_h4 = S.analyse_structure(h4_df, cfg.swing_bars, cfg.struct_lookback)
    st_h1 = S.analyse_structure(h1_df, cfg.swing_bars, cfg.struct_lookback)
    st_entry = S.analyse_structure(entry_df, cfg.swing_bars, cfg.struct_lookback)

    # STEP 1: direction from structure
    direction = _decide_direction(st_d1, st_h4, st_h1)
    if direction == 0:
        return reject("1-structure", "โครงสร้าง D1/H4/H1 ไม่ตรงกัน")
    is_buy = direction > 0

    # STEP 2: liquidity map
    atr_value = S.atr(entry_df, cfg.atr_period)
    if atr_value <= 0:
        return reject("2-liquidity", "คำนวณ ATR ไม่ได้")
    sm = S.analyse_smart_money(entry_df, st_entry, atr_value, cfg.smc_window)
    if cfg.require_liquidity_target and not (sm.bsl_above if is_buy else sm.ssl_below):
        return reject("2-liquidity", "ไม่มี liquidity pool ฝั่งกำไร")

    # STEP 3+4: BOS / CHoCH
    if cfg.require_bos_choch:
        if not (st_entry.bias == direction and (st_entry.recent_bos or st_entry.recent_choch)):
            return reject("3-bos", "ยังไม่มี BOS/CHoCH ในทิศทางเทรด")

    # STEP 5: order block
    ob = sm.ob_bull if is_buy else sm.ob_bear
    if cfg.require_order_block:
        if not ob.valid:
            return reject("5-orderblock", "ไม่พบ order block")
        if ob.quality < cfg.min_ob_quality:
            return reject("5-orderblock", f"OB คุณภาพ {ob.quality:.0f} < {cfg.min_ob_quality:.0f}")

    # STEP 6: fair value gap
    if cfg.require_fvg and not (sm.fvg_bull if is_buy else sm.fvg_bear):
        return reject("6-fvg", "ไม่พบ FVG ในทิศทางเทรด")

    # STEP 7: liquidity sweep must already have happened
    if cfg.require_sweep and not (sm.sweep_bull if is_buy else sm.sweep_bear):
        return reject("7-sweep", "ยังไม่เกิด liquidity sweep")

    # STEP 8: premium / discount (+ optional OTE)
    if cfg.require_premium_discount:
        if is_buy and sm.range_pos > cfg.discount_max:
            return reject("8-zone", f"ราคาอยู่โซน premium ({sm.range_pos:.2f}) ไม่ซื้อ")
        if not is_buy and sm.range_pos < 1 - cfg.discount_max:
            return reject("8-zone", f"ราคาอยู่โซน discount ({sm.range_pos:.2f}) ไม่ขาย")
    in_ote = (0.21 <= sm.range_pos <= 0.38) if is_buy else (0.62 <= sm.range_pos <= 0.79)
    if cfg.require_ote and not in_ote:
        return reject("8-ote", "ไม่อยู่ในโซน OTE")

    # STEP 9: mitigation
    if cfg.require_mitigation:
        mitigating = (ob.valid and ob.mitigating) or (
            sm.fvg_bull_mitigated if is_buy else sm.fvg_bear_mitigated)
        if not mitigating:
            return reject("9-mitigation", "ราคายังไม่กลับมา mitigate OB/FVG")

    # --- SL / TP plan --------------------------------------------------
    price = float(entry_df["close"].iloc[-1])
    buffer = atr_value * 0.3
    if is_buy:
        sl_level = st_entry.last_low - buffer if st_entry.last_low > 0 else 0.0
        sl_dist = price - sl_level if sl_level > 0 else 0.0
    else:
        sl_level = st_entry.last_high + buffer if st_entry.last_high > 0 else 0.0
        sl_dist = sl_level - price if sl_level > 0 else 0.0
    if sl_dist <= 0:
        sl_dist = atr_value * cfg.atr_mult_sl
    sl_dist = max(sl_dist, atr_value * cfg.min_sl_atr)
    sl_dist = min(sl_dist, atr_value * cfg.max_sl_atr)

    # --- confidence score ----------------------------------------------
    parts = {
        "structure": _score_structure(direction, st_d1, st_h4, st_h1, st_entry),
        "liquidity": _score_liquidity(direction, sm),
        "bos_choch": _score_bos(direction, st_entry),
        "orderblock": _score_ob(ob),
        "fvg": _score_fvg(direction, sm),
        "volume": _score_volume(direction, entry_df),
        "indicator": _score_indicator(direction, entry_df, h1_df),
    }
    weights = cfg.weights
    total = sum(parts[k] * weights[k] for k in parts) / sum(weights.values())

    threshold = cfg.score_threshold + (cfg.tier3_extra if tier == 3 else 0.0)
    if total < threshold:
        return reject("10-score", f"คะแนน {total:.1f} < {threshold:.0f}")

    # --- build the candidate -------------------------------------------
    digits = 2 if "JPY" in symbol or symbol == "XAUUSD" else 5
    def r(x: float) -> float:
        return round(x, digits)

    if ob.valid and ob.mitigating:
        zone_low, zone_high = ob.bottom, ob.top
    else:
        zone_low, zone_high = price - atr_value * 0.2, price + atr_value * 0.2

    cand = Candidate(
        symbol=symbol, tier=tier, direction=direction,
        entry=r(price), entry_low=r(zone_low), entry_high=r(zone_high),
        sl=r(price - sl_dist if is_buy else price + sl_dist),
        tp1=r(price + sl_dist * cfg.tp1_r if is_buy else price - sl_dist * cfg.tp1_r),
        tp2=r(price + sl_dist * cfg.tp2_r if is_buy else price - sl_dist * cfg.tp2_r),
        tp3=r(price + sl_dist * cfg.tp3_r if is_buy else price - sl_dist * cfg.tp3_r),
        rr=cfg.tp2_r, score=round(total, 1), timeframe=entry_tf, scores=parts,
    )

    bias = f"D1:{st_d1.trend} H4:{st_h4.trend} H1:{st_h1.trend}"
    cand.reasons.append(f"Market Structure: {bias}")
    if st_entry.recent_bos:
        cand.reasons.append("BOS ✔")
    if st_entry.recent_choch:
        cand.reasons.append("CHoCH ✔")
    if sm.sweep_bull if is_buy else sm.sweep_bear:
        cand.reasons.append("Liquidity Sweep ✔")
    if ob.valid:
        cand.reasons.append(f"Order Block ✔ (คุณภาพ {ob.quality:.0f}/100)")
    if sm.fvg_bull if is_buy else sm.fvg_bear:
        mit = sm.fvg_bull_mitigated if is_buy else sm.fvg_bear_mitigated
        cand.reasons.append("Fair Value Gap ✔" + (" (mitigated)" if mit else ""))
    # describe the zone price is actually in, not the one we hoped for
    zone_name = "Discount" if sm.range_pos <= 0.5 else "Premium"
    favourable = (sm.range_pos <= 0.5) if is_buy else (sm.range_pos >= 0.5)
    cand.reasons.append(
        f"{zone_name} Zone {'✔' if favourable else '⚠️'} (rangePos {sm.range_pos:.2f})"
        + (" | OTE ✔" if in_ote else ""))

    cand.notes.append(f"รอแท่งเทียน {entry_tf} ปิดยืนยันก่อนเข้า")
    cand.notes.append(f"ยกเลิกสัญญาณหากราคาปิดเลย SL ({cand.sl}) ก่อนเข้าไม้")
    cand.notes.append("หลีกเลี่ยงการเข้าใกล้ช่วงประกาศข่าวสำคัญ")
    return cand, None
