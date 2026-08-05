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

from . import costs as COSTS
from . import exits as EXITS
from . import macro as MACRO
from . import memory as MEM
from . import probability as PROB
from . import regime as REG
from . import smc as S
from . import strategy as STRAT
from . import voters as VOTE
from .config import Settings
from .data import freshness
from .profiles import Profile


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
    profile: str = "day"           # trading style this setup belongs to
    grade: str = "A"               # quality grade derived from the score
    hold_time: str = ""            # expected holding time
    bar_time: str = ""             # entry bar the setup formed on
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)   # what could break this plan
    scores: dict[str, float] = field(default_factory=dict)
    # --- LEVEL 9: what the desk needs on the ticket ------------------
    regime: str = ""               # market regime this was taken in
    regime_confidence: float = 0.0
    strategies: list = field(default_factory=list)   # approaches actually used
    weights: dict = field(default_factory=dict)      # the dynamic weighting used
    strategy_why: str = ""
    win_probability: float = 0.0
    prob_source: str = "model"
    expected_value: float = 0.0
    expected_rr: float = 0.0
    macro_note: str = ""
    memory_note: str = ""
    approval: str = ""
    invalidation: str = ""
    # --- Adaptive Multi-Strategy: who voted, and what they said -------
    consensus: object = None          # voters.Consensus, when voting is on
    analysis_trends: dict = field(default_factory=dict)
    # --- Dynamic Exit Engine -----------------------------------------
    exit_mode: str = "fixed"
    exit_label: str = ""
    trail_atr: float = 0.0          # trail distance in ATR, 0 = fixed exits
    prob_tp: tuple = (0.0, 0.0, 0.0)
    prob_sl: float = 0.0
    expected_drawdown: float = 0.0
    exit_reasons: list = field(default_factory=list)

    @property
    def side(self) -> str:
        return "BUY" if self.direction > 0 else "SELL"


@dataclass
class Rejection:
    """Why a symbol produced no signal - shown in the status report."""
    symbol: str
    stage: str
    detail: str
    profile: str = "day"


@dataclass
class MarketView:
    """What the market looks like right now for one symbol.

    Produced on every pass, whether or not a signal qualifies, so the
    briefing can report trend and key zones even while the pipeline is
    still waiting for a setup.
    """
    symbol: str
    profile: str = "day"
    price: float = 0.0
    atr: float = 0.0
    # structure per timeframe: "UP" / "DOWN" / "SIDE".
    # tf_names carries the actual timeframe each slot refers to, since
    # every profile reads a different ladder.
    tf_names: tuple = ("D1", "H4", "H1", "M15")
    trend_d1: str = S.SIDEWAYS
    trend_h4: str = S.SIDEWAYS
    trend_h1: str = S.SIDEWAYS
    trend_entry: str = S.SIDEWAYS
    direction: int = 0               # agreed structure direction, 0 = conflict
    # key liquidity levels
    swing_high: float = 0.0          # resistance / buy-side liquidity
    swing_low: float = 0.0           # support / sell-side liquidity
    equal_highs: bool = False
    equal_lows: bool = False
    # position inside the dealing range
    range_pos: float = 0.5
    zone: str = "Equilibrium"
    # smart-money state
    recent_bos: bool = False
    recent_choch: bool = False
    sweep_bull: bool = False
    sweep_bear: bool = False
    ob_zone: tuple = ()               # (bottom, top, quality) of the relevant OB
    ob_mitigating: bool = False
    fvg: bool = False
    fvg_mitigated: bool = False
    # how far through the pipeline this symbol got
    steps_passed: int = 0
    waiting: str = ""
    score: float = 0.0
    data_age_min: float = 0.0        # how old the newest candle is
    data_stale: bool = False
    price_note: str = ""             # set when the feed is not the spot market
    news_verified: bool = False      # False = calendar could not be reached
    news_note: str = ""
    regime: str = ""
    regime_confidence: float = 0.0
    regime_evidence: list = field(default_factory=list)
    strategies: list = field(default_factory=list)
    consensus: object = None          # voters.Consensus, when voting is on
    # Timeframes read for context but never allowed to block an entry.
    analysis_trends: dict = field(default_factory=dict)


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


# Quality bands, best to worst. A relaxed threshold can let a weaker setup
# through, so the grade travels with the signal and drives the sizing
# advice - a D is still a real setup that passed every structural gate,
# it just cleared the score bar at its lowest.
GRADES = ((92.0, "S"), (85.0, "A"), (78.0, "B"), (72.0, "C"), (0.0, "D"))

GRADE_ADVICE = {
    "S": "คุณภาพสูงสุด — ขนาดไม้ปกติได้เต็มที่",
    "A": "คุณภาพดี — ขนาดไม้ปกติได้",
    "B": "คุณภาพปานกลาง — แนะนำลดขนาดไม้เหลือ 50-70%",
    "C": "คุณภาพพอใช้ (ผ่านเกณฑ์ที่ผ่อนแล้ว) — แนะนำลดขนาดไม้เหลือ 30-50%",
    "D": "คุณภาพต่ำสุดที่ระบบยอมส่ง — ลดขนาดไม้เหลือ 20-30% หรือข้ามไม้นี้ก็ได้",
}

# grades that only exist because the daily pacing lowered the bar
RELAXED_GRADES = ("B", "C", "D")


def grade_for(score: float) -> str:
    for cutoff, label in GRADES:
        if score >= cutoff:
            return label
    return "D"


def _decide_direction(d1: S.Structure, h4: S.Structure, h1: S.Structure,
                      strict: bool = False) -> int:
    """Direction from structure only, never from an indicator.

    Nothing may oppose: if any of the three higher timeframes points the
    other way, there is no trade. What changed is the treatment of a
    *neutral* timeframe. Requiring both H4 and H1 to be actively
    directional threw away every setup where one of them happened to be
    ranging, which is most of them - a clean H1 trend under a sideways H4
    is an ordinary continuation, not a conflict.

    `strict` restores the old behaviour for anyone who wants it.
    """
    dir_h4, dir_h1 = h4.direction, h1.direction
    dir_d1 = 1 if d1.trend == S.UPTREND else -1 if d1.trend == S.DOWNTREND else 0

    if strict:
        if dir_h4 == 0 or dir_h1 == 0 or dir_h4 != dir_h1:
            return 0
        if dir_d1 != 0 and dir_d1 != dir_h4:
            return 0
        return dir_h4

    votes = [d for d in (dir_d1, dir_h4, dir_h1) if d != 0]
    if not votes:
        return 0                       # nothing is trending anywhere
    if len(set(votes)) > 1:
        return 0                       # something actively opposes
    direction = votes[0]
    # the entry timeframe's own ladder still has to have a say: a lone
    # daily vote with both intermediate frames flat is not a setup
    if dir_h4 == 0 and dir_h1 == 0:
        return 0
    return direction


def _tf_support(st: S.Structure, direction: int) -> float:
    """How strongly one timeframe supports the direction (0..1)."""
    want = S.UPTREND if direction > 0 else S.DOWNTREND
    against = S.DOWNTREND if direction > 0 else S.UPTREND
    if st.trend == want:
        return 1.0
    if st.trend == against:
        return 0.0
    return 0.5 if st.bias == direction else 0.0


def _score_trend(direction: int, d1, h4, h1, entry) -> float:
    """Trend Alignment 15% - do the timeframes agree on direction?"""
    return _clamp(20 * _tf_support(d1, direction) + 30 * _tf_support(h4, direction)
                  + 30 * _tf_support(h1, direction) + 20 * _tf_support(entry, direction))


def _score_smc(direction: int, entry_st: S.Structure, ob: S.OrderBlock,
               sm: S.SmartMoney) -> float:
    """SMC Structure 30% - the structural evidence: BOS/CHoCH, OB, FVG.

    This is the heaviest category because it is what the whole system is
    built on: a break of structure in our direction, an institutional
    zone to enter from, and an imbalance supporting the move.
    """
    score = 0.0
    # structure break carries the most weight inside the category
    if entry_st.bias == direction and entry_st.recent_bos:
        score += 25.0
    if entry_st.bias == direction and entry_st.recent_choch:
        score += 15.0
    # order block, scaled by its own quality and whether price is there
    if ob.valid:
        score += (ob.quality / 100.0) * (30.0 if ob.mitigating else 15.0)
    # imbalance
    fvg = sm.fvg_bull if direction > 0 else sm.fvg_bear
    mitigated = sm.fvg_bull_mitigated if direction > 0 else sm.fvg_bear_mitigated
    if fvg:
        score += 20.0 if mitigated else 12.0
    return _clamp(score)


def _score_liquidity(direction: int, sm: S.SmartMoney) -> float:
    """Liquidity 20% - was liquidity taken, and is there a pool to run at?"""
    score = 0.0
    if direction > 0:
        score += 45 if sm.sweep_bull else 0
        score += 25 if sm.bsl_above else 0          # target above price
        score += 20 if sm.range_pos <= 0.5 else 0   # buying at discount
        score += 10 if sm.equal_lows else 0         # pool that was swept
    else:
        score += 45 if sm.sweep_bear else 0
        score += 25 if sm.ssl_below else 0
        score += 20 if sm.range_pos >= 0.5 else 0
        score += 10 if sm.equal_highs else 0
    return _clamp(score)


def _score_ict(direction: int, sm: S.SmartMoney, entry_st: S.Structure,
               in_ote: bool, in_kill_zone: bool) -> float:
    """ICT Setup 10% - OTE, Power of Three, kill zone, Judas-style reversal."""
    score = 0.0
    if in_ote:
        score += 30.0
    if in_kill_zone:
        score += 25.0
    # Power of Three: manipulation (sweep) followed by distribution (BOS)
    swept = sm.sweep_bull if direction > 0 else sm.sweep_bear
    if swept and entry_st.recent_bos and entry_st.bias == direction:
        score += 30.0
    # Judas-style: liquidity grabbed against us, then structure flipped
    if swept and entry_st.recent_choch and entry_st.bias == direction:
        score += 15.0
    return _clamp(score)


def _score_volume(direction: int, entry_df: pd.DataFrame) -> float:
    """Volume 5% - is there participation behind the move?"""
    ratio = S.volume_ratio(entry_df)
    if ratio >= 1.5:
        score = 70.0
    elif ratio >= 1.1:
        score = 55.0
    elif ratio >= 0.8:
        score = 35.0
    else:
        score = 15.0
    closes = entry_df["close"]
    if len(closes) > 6:
        slope = float(closes.iloc[-2] - closes.iloc[-6])
        if (slope > 0) == (direction > 0):
            score += 30.0
    return _clamp(score)


def _score_indicator(direction: int, entry_df: pd.DataFrame,
                     h1_df: pd.DataFrame) -> float:
    """Indicator Confirmation 5% - agreement only, never a reason to enter."""
    score = 0.0
    if S.ema_trend(entry_df) == direction:
        score += 25
    r = float(S.rsi(entry_df["close"]).iloc[-2])
    if direction > 0 and 50 < r < 70:
        score += 25
    if direction < 0 and 30 < r < 50:
        score += 25
    main, signal = S.macd(h1_df["close"])
    if (main > signal) == (direction > 0):
        score += 25
    if S.cmf(entry_df) * direction > 0:
        score += 25
    return _clamp(score)


def _score_rr(planned_rr: float) -> float:
    """Risk Reward 5% - reward the setups that pay more for the same risk."""
    if planned_rr >= 4.0:
        return 100.0
    if planned_rr >= 3.0:
        return 90.0
    if planned_rr >= 2.0:
        return 75.0
    if planned_rr >= 1.5:
        return 55.0
    return 25.0


def _score_spread(session: str, vol_state: str) -> float:
    """Spread 5% - execution conditions.

    The free OHLC feeds carry no bid/ask, so real spread cannot be
    measured. This estimates execution quality from the two things that
    actually drive it: which session is open, and how violent the market
    is right now. Reported as an estimate, never as a measured spread.
    """
    score = {"Overlap": 100.0, "London": 85.0, "NewYork": 85.0,
             "Asian": 45.0}.get(session, 30.0)
    if vol_state == "high":
        score -= 25.0          # spreads widen when volatility spikes
    return _clamp(score)


def analyse(symbol: str, tier: int, frames: dict[str, pd.DataFrame],
            cfg: Settings, prof: Profile, news_ctx=None,
            session: str = "", in_kill_zone: bool = False,
            threshold_override: Optional[float] = None,
            macro_view=None, learned: Optional[dict] = None,
            signals: Optional[list] = None
            ) -> tuple[Optional[Candidate], Optional[Rejection], MarketView]:
    """Run the full SMC pipeline for one symbol.

    Always returns a MarketView describing the current chart, plus either
    a Candidate (every step passed and the score cleared the threshold)
    or a Rejection naming the step that stopped it.
    """
    entry_tf = prof.entry_tf
    d1_df = frames[prof.htf_major]        # sets the overall bias
    h4_df = frames[prof.htf_mid]
    h1_df = frames[prof.htf_minor]
    entry_df = frames[entry_tf]

    view = MarketView(symbol=symbol, profile=prof.name)
    view.tf_names = (prof.htf_major, prof.htf_mid, prof.htf_minor, entry_tf)
    view.price = float(entry_df["close"].iloc[-1])
    view.data_age_min, view.data_stale = freshness(entry_df, entry_tf)
    view.price_note = entry_df.attrs.get("proxy_note", "")
    if news_ctx is not None:
        view.news_verified = news_ctx.verified()
        view.news_note = (news_ctx.notes[0] if news_ctx.notes else "")

    def reject(stage: str, detail: str):
        view.waiting = detail
        return None, Rejection(symbol, stage, detail, prof.name), view

    # --- structure on every timeframe ---------------------------------
    st_d1 = S.analyse_structure(d1_df, cfg.swing_bars, cfg.struct_lookback)
    st_h4 = S.analyse_structure(h4_df, cfg.swing_bars, cfg.struct_lookback)
    st_h1 = S.analyse_structure(h1_df, cfg.swing_bars, cfg.struct_lookback)
    st_entry = S.analyse_structure(entry_df, cfg.swing_bars, cfg.struct_lookback)

    # The faster charts every symbol is read on, gate or no gate: an H1
    # entry still wants to know what the five-minute chart is doing, it
    # just must not be refused because of it.
    for tf in prof.analysis_tfs:
        small = frames.get(tf)
        if small is not None and len(small) > 5:
            view.analysis_trends[tf] = S.analyse_structure(
                small, cfg.swing_bars, cfg.struct_lookback).trend

    view.trend_d1, view.trend_h4 = st_d1.trend, st_h4.trend
    view.trend_h1, view.trend_entry = st_h1.trend, st_entry.trend
    view.swing_high, view.swing_low = st_entry.last_high, st_entry.last_low
    view.recent_bos, view.recent_choch = st_entry.recent_bos, st_entry.recent_choch

    # STEP 1: direction from structure
    direction = _decide_direction(st_d1, st_h4, st_h1, cfg.strict_structure)
    if direction == 0:
        return reject("1-structure", f"โครงสร้าง {prof.htf_major}/{prof.htf_mid}/{prof.htf_minor} ไม่ตรงกัน")
    is_buy = direction > 0
    view.direction = direction
    view.steps_passed = 1

    # STEP 2: liquidity map
    atr_value = S.atr(entry_df, cfg.atr_period)
    if atr_value <= 0:
        return reject("2-liquidity", "คำนวณ ATR ไม่ได้")
    view.atr = atr_value
    sm = S.analyse_smart_money(entry_df, st_entry, atr_value, cfg.smc_window)
    ob_view = sm.ob_bull if is_buy else sm.ob_bear
    view.equal_highs, view.equal_lows = sm.equal_highs, sm.equal_lows
    view.range_pos = sm.range_pos
    view.zone = ("Discount" if sm.range_pos < 0.45
                 else "Premium" if sm.range_pos > 0.55 else "Equilibrium")
    view.sweep_bull, view.sweep_bear = sm.sweep_bull, sm.sweep_bear
    if ob_view.valid:
        view.ob_zone = (ob_view.bottom, ob_view.top, ob_view.quality)
        view.ob_mitigating = ob_view.mitigating
    view.fvg = sm.fvg_bull if is_buy else sm.fvg_bear
    view.fvg_mitigated = sm.fvg_bull_mitigated if is_buy else sm.fvg_bear_mitigated

    # --- LEVEL 2: what kind of market is this --------------------------
    news_active = bool(news_ctx is not None and getattr(news_ctx, "upcoming", None))
    reg = REG.detect(entry_df, st_entry, sm, atr_value, news_active)
    view.regime, view.regime_confidence = reg.name, reg.confidence
    view.regime_evidence = list(reg.evidence)

    # --- LEVEL 4/5: the market picks the strategies and their weights ---
    strategies, weights, strategy_why = STRAT.select(reg, learned)
    view.strategies = list(strategies)
    # LEVEL 1: what the global tape implies for this instrument
    if macro_view is not None:
        macro_dir, macro_why = MACRO.bias_for(macro_view, symbol)
    else:
        macro_dir, macro_why = 0, "ยังไม่มีข้อมูลภาพรวมมหภาคในรอบนี้"
    # LEVEL 3: what this regime has actually paid in the past
    recall = MEM.recall(signals or [], reg.name)
    view.steps_passed = 2

    if cfg.require_liquidity_target and not (sm.bsl_above if is_buy else sm.ssl_below):
        return reject("2-liquidity", "ไม่มี liquidity pool ฝั่งกำไร")

    # STEP 3+4: BOS / CHoCH
    if cfg.gate("REQUIRE_BOS_CHOCH", prof.require_bos_choch):
        if not (st_entry.bias == direction and (st_entry.recent_bos or st_entry.recent_choch)):
            return reject("3-bos", "ยังไม่มี BOS/CHoCH ในทิศทางเทรด")
    view.steps_passed = 4

    # STEP 5: order block
    ob = sm.ob_bull if is_buy else sm.ob_bear
    if cfg.gate("REQUIRE_ORDER_BLOCK", prof.require_order_block):
        if not ob.valid:
            return reject("5-orderblock", "ไม่พบ order block")
        min_q = cfg.number("MIN_OB_QUALITY", prof.min_ob_quality)
        if ob.quality < min_q:
            return reject("5-orderblock", f"OB คุณภาพ {ob.quality:.0f} < {min_q:.0f}")
    view.steps_passed = 5

    # STEP 6: fair value gap
    if cfg.gate("REQUIRE_FVG", prof.require_fvg) and not (sm.fvg_bull if is_buy else sm.fvg_bear):
        return reject("6-fvg", "ไม่พบ FVG ในทิศทางเทรด")
    view.steps_passed = 6

    # STEP 7: liquidity sweep must already have happened
    if cfg.gate("REQUIRE_SWEEP", prof.require_sweep) and not (sm.sweep_bull if is_buy else sm.sweep_bear):
        return reject("7-sweep", "ยังไม่เกิด liquidity sweep")
    view.steps_passed = 7

    # STEP 8: premium / discount (+ optional OTE)
    if cfg.gate("REQUIRE_PREMIUM_DISCOUNT", prof.require_premium_discount):
        if is_buy and sm.range_pos > cfg.discount_max:
            return reject("8-zone", f"ราคาอยู่โซน premium ({sm.range_pos:.2f}) ไม่ซื้อ")
        if not is_buy and sm.range_pos < 1 - cfg.discount_max:
            return reject("8-zone", f"ราคาอยู่โซน discount ({sm.range_pos:.2f}) ไม่ขาย")
    in_ote = (0.21 <= sm.range_pos <= 0.38) if is_buy else (0.62 <= sm.range_pos <= 0.79)
    if cfg.require_ote and not in_ote:
        return reject("8-ote", "ไม่อยู่ในโซน OTE")
    view.steps_passed = 8

    # STEP 9: mitigation
    if cfg.gate("REQUIRE_MITIGATION", prof.require_mitigation):
        mitigating = (ob.valid and ob.mitigating) or (
            sm.fvg_bull_mitigated if is_buy else sm.fvg_bear_mitigated)
        if not mitigating:
            return reject("9-mitigation", "ราคายังไม่กลับมา mitigate OB/FVG")
    view.steps_passed = 9

    # STEP 10: lower-timeframe confirmation - the faster charts this
    # profile watches must not be running the other way
    for tf in prof.confirm_tfs:
        conf_df = frames.get(tf)
        if conf_df is None:
            continue
        if S.ema_trend(conf_df) == -direction:
            return reject("10-confirm", f"{tf} สวนทิศทาง")
    view.steps_passed = 10

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
        sl_dist = atr_value * prof.atr_mult_sl
    sl_dist = max(sl_dist, atr_value * prof.min_sl_atr)
    sl_dist = min(sl_dist, atr_value * prof.max_sl_atr)

    # --- the stop has to survive the cost of being in the market -------
    # ATR alone produced stops narrower than the spread on quiet pairs, and
    # a stop inside the spread is a loss that has already happened. The
    # floor is applied last so it beats the style's own ceiling: a stop
    # wider than the style intended is a worse trade, a stop inside the
    # spread is not a trade at all.
    digits = 2 if "JPY" in symbol or symbol == "XAUUSD" else 5
    floor, floor_why = COSTS.stop_floor(
        symbol, entry_df, reg.volatility,
        spread_multiple=cfg.min_sl_spreads,
        noise_multiple=cfg.min_sl_candles, digits=digits)
    ceiling = atr_value * prof.max_sl_atr
    if floor > ceiling * cfg.sl_floor_stretch:
        return reject("10-stop",
                      f"ตลาดนิ่งเกินไปสำหรับ {entry_tf} — SL ที่ปลอดภัยต้องกว้าง "
                      f"{floor:.5g} แต่สไตล์นี้รับได้แค่ {ceiling:.5g} "
                      f"(สเปรดจะกินไม้นี้หมด)")
    widened = floor > sl_dist
    sl_dist = max(sl_dist, floor)

    # --- Dynamic Exit Engine: targets computed, never assumed ----------
    # distance to the pool price is actually being drawn toward
    if is_buy:
        pool = (st_entry.last_high - price) if st_entry.last_high > price else 0.0
    else:
        pool = (price - st_entry.last_low) if st_entry.last_low < price else 0.0

    # --- confidence score ----------------------------------------------
    # volatility state from ATR against its own recent average
    atr_series = entry_df["high"].sub(entry_df["low"]).rolling(50).mean()
    baseline = float(atr_series.iloc[-2]) if len(atr_series) > 50 else atr_value
    vol_state = "high" if baseline > 0 and atr_value > baseline * 1.4 else "normal"
    news_score = news_ctx.score if news_ctx is not None else 50.0
    planned_rr = prof.tp_r[1]      # provisional, replaced by the exit plan

    parts = {
        "smc": _score_smc(direction, st_entry, ob, sm),
        "liquidity": _score_liquidity(direction, sm),
        "trend": _score_trend(direction, st_d1, st_h4, st_h1, st_entry),
        "ict": _score_ict(direction, sm, st_entry, in_ote, in_kill_zone),
        "volume": _score_volume(direction, entry_df),
        "indicator": _score_indicator(direction, entry_df, h1_df),
        "rr": _score_rr(planned_rr),
        "spread": _score_spread(session, vol_state),
        "news": news_score,
        "macro": 75.0 if macro_dir == direction else 30.0 if macro_dir else 50.0,
    }
    # Only weigh the categories that were actually scored, so a playbook
    # naming a category this pass could not compute cannot quietly dilute
    # the result by inflating the denominator.
    used_w = {k: weights.get(k, 0.0) for k in parts}
    total = (sum(parts[k] * used_w[k] for k in parts) / sum(used_w.values())
             if sum(used_w.values()) > 0 else 50.0)

    # --- Adaptive Multi-Strategy: let each technique vote --------------
    consensus = None
    if cfg.strategy_voting:
        vote_ctx = {
            "st_entry": st_entry, "st_d1": st_d1, "st_h4": st_h4,
            "sm": sm, "df": entry_df, "reg": reg,
            "strength": REG._directional_strength(entry_df),
            "symbol": symbol, "session": session, "in_kill_zone": in_kill_zone,
            "macro_dir": macro_dir, "macro_why": macro_why,
            "news_ctx": news_ctx,
        }
        consensus = VOTE.decide(direction, vote_ctx)
        view.consensus = consensus
        # A score the called techniques agree on is worth more than the
        # same score they are split over, so the consensus moves the
        # number rather than replacing it.
        total = _clamp(total + (consensus.confidence - 50.0) * cfg.vote_influence)
        parts["consensus"] = consensus.confidence
        if cfg.vote_conflict_blocks and consensus.verdict == "WAIT" \
                and len(consensus.dissenters) >= 2:
            return reject("10-consensus",
                          "เทคนิคขัดแย้งกัน — "
                          + ", ".join(consensus.dissenters) + " ไม่สนับสนุนทิศทางนี้")

    view.score = round(total, 1)
    base = cfg.number("SCORE_THRESHOLD", prof.score_threshold)
    if threshold_override is not None:
        # the adaptive bar may lower the requirement, never raise it
        base = min(base, threshold_override)
    threshold = base + (cfg.tier3_extra if tier == 3 else 0.0)
    if total < threshold:
        return reject("10-score", f"คะแนน {total:.1f} < {threshold:.0f}")
    view.steps_passed = 11
    view.waiting = "พร้อมเข้า — ผ่านครบทุกขั้น"

    # --- Dynamic Exit Engine -------------------------------------------
    momentum = min(1.0, max(0.0, REG._directional_strength(entry_df, 20)))
    strength = REG._directional_strength(entry_df)
    news_clear_now = not (news_ctx is not None and getattr(news_ctx, "upcoming", None))
    plan = EXITS.build(
        score=total, threshold=threshold, reg=reg, strength=strength,
        atr=atr_value, sl_distance=sl_dist, liquidity_distance=pool,
        volume_ratio=S.volume_ratio(entry_df), session=session,
        macro_agrees=(1 if macro_dir == direction and macro_dir else
                      -1 if macro_dir == -direction and macro_dir else 0),
        news_clear=news_clear_now, momentum=momentum, recall=recall,
        style_cap=prof.tp_r[2])

    if not plan.positive:
        return reject("11-ev", f"ทุกแผนออกให้ EV ติดลบ (ดีสุด {plan.expected_value:+.2f}R) "
                               f"— ไม่คุ้มที่จะเข้า")

    # --- build the candidate -------------------------------------------
    def r(x: float) -> float:
        # float() first: ATR-derived values are numpy scalars, and a numpy
        # float64 in the state file makes json.dumps refuse to save at all
        return round(float(x), digits)

    raw_tps = [price + sl_dist * k * (1 if is_buy else -1) for k in plan.tp_r]
    tp_levels = COSTS.space_ladder(price, raw_tps, direction, digits)

    if ob.valid and ob.mitigating:
        zone_low, zone_high = ob.bottom, ob.top
    else:
        zone_low, zone_high = price - atr_value * 0.2, price + atr_value * 0.2

    cand = Candidate(
        symbol=symbol, tier=tier, direction=direction,
        entry=r(price), entry_low=r(zone_low), entry_high=r(zone_high),
        sl=r(price - sl_dist if is_buy else price + sl_dist),
        tp1=tp_levels[0], tp2=tp_levels[1], tp3=tp_levels[2],
        rr=plan.tp_r[1], score=round(total, 1), timeframe=entry_tf, scores=parts,
        profile=prof.name, grade=grade_for(total), hold_time=prof.hold_time,
        bar_time=str(entry_df.index[-2]),
        regime=reg.name, regime_confidence=reg.confidence,
        strategies=list(strategies), weights=dict(weights),
        strategy_why=strategy_why, macro_note=macro_why,
        memory_note=recall.note, consensus=consensus,
        analysis_trends=dict(view.analysis_trends),
    )

    # --- LEVEL 6: the numbers come from the chosen exit plan ------------
    cand.exit_mode, cand.exit_label = plan.mode, plan.label
    cand.trail_atr = plan.trail_atr
    cand.exit_reasons = list(plan.reasons)
    cand.win_probability = plan.win_probability
    cand.prob_source = "history" if (recall and recall.enough) else "model"
    cand.expected_value = plan.expected_value
    cand.expected_rr = plan.expected_rr
    cand.prob_tp = plan.prob_tp
    cand.prob_sl = plan.prob_sl
    cand.expected_drawdown = plan.expected_drawdown
    odds = PROB.Odds(win_probability=plan.win_probability,
                     source=cand.prob_source, expected_rr=plan.expected_rr,
                     expected_value=plan.expected_value,
                     prob_tp1=plan.prob_tp[0], prob_sl=plan.prob_sl)
    cand.invalidation = (
        f"ราคาปิด{'ต่ำกว่า' if is_buy else 'สูงกว่า'} {cand.sl} บน {entry_tf} "
        f"= โครงสร้างเสีย ยกเลิกแผนนี้ทั้งหมด")

    # --- LEVEL 10: would a portfolio manager sign this off? -------------
    news_blocking = bool(news_ctx is not None and getattr(news_ctx, "blocking", False))
    approved, verdict = PROB.approve(odds, reg, 11, 11, news_blocking,
                                     cfg.min_regime_confidence)
    cand.approval = verdict
    if not approved:
        view.waiting = f"ไม่ผ่านการอนุมัติ: {verdict}"
        return None, Rejection(symbol, "11-approval", verdict, prof.name), view

    bias = (f"{prof.htf_major}:{st_d1.trend} {prof.htf_mid}:{st_h4.trend} "
            f"{prof.htf_minor}:{st_h1.trend}")
    cand.reasons.append(f"สภาพตลาด: {reg.name} (มั่นใจ {reg.confidence:.0f}%)")
    cand.reasons.append(f"กลยุทธ์ที่เลือกใช้: {', '.join(strategies)}")
    if consensus is not None:
        cand.reasons.append(
            f"มติเทคนิค: สนับสนุน {len(consensus.supporters)}/{len(consensus.votes)} "
            f"· สอดคล้อง {consensus.agreement:.0%} · ความมั่นใจ {consensus.confidence:.0f}")
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

    if widened:
        cand.notes.append(
            f"SL ถูกขยายให้กว้างขึ้นจากที่โครงสร้างบอก เพราะแคบกว่านั้นสเปรด"
            f"และการแกว่งปกติจะกินไม้นี้ทิ้ง — {floor_why}")
    cand.notes.append(f"รอแท่งเทียน {entry_tf} ปิดยืนยันก่อนเข้า")
    cand.notes.append(f"ยกเลิกสัญญาณหากราคาปิดเลย SL ({cand.sl}) ก่อนเข้าไม้")
    cand.notes.append("หลีกเลี่ยงการเข้าใกล้ช่วงประกาศข่าวสำคัญ")
    if prof.note:
        cand.notes.append(prof.note)

    # --- what could invalidate this plan -------------------------------
    if cand.grade in RELAXED_GRADES:
        cand.risks.append(
            f"สัญญาณเกรด {cand.grade} — ผ่านเกณฑ์ที่ผ่อนลงเพื่อให้ครบเป้าหมายรายวัน "
            f"ไม่ใช่ setup ระดับ S/A")
    opposite = "ต่ำกว่า" if is_buy else "สูงกว่า"
    cand.risks.append(f"ราคาปิด{opposite} SL ({cand.sl}) = โครงสร้างเสีย ต้องออก")
    if st_entry.recent_choch:
        cand.risks.append("เข้าจาก CHoCH ซึ่งเป็นการกลับตัว หากไม่ตามต่อจะเป็นสัญญาณหลอกได้")
    if not (sm.sweep_bull if is_buy else sm.sweep_bear):
        cand.risks.append("ยังไม่เห็น liquidity sweep ชัดเจน — อาจโดนกวาด stop ก่อนวิ่ง")
    if view.range_pos > 0.7 and is_buy:
        cand.risks.append(f"ราคาอยู่ค่อนไปทาง premium ({view.range_pos:.2f}) เหลือระยะวิ่งน้อย")
    if view.range_pos < 0.3 and not is_buy:
        cand.risks.append(f"ราคาอยู่ค่อนไปทาง discount ({view.range_pos:.2f}) เหลือระยะวิ่งน้อย")
    if news_ctx is not None and not news_ctx.verified():
        cand.risks.append("ไม่สามารถยืนยันข่าวล่าสุดได้ — อาจมีข่าวที่ระบบไม่เห็น")
    elif news_ctx is not None and news_ctx.upcoming:
        soon = news_ctx.upcoming[0]
        cand.risks.append(f"มีข่าวแรงใกล้เข้ามา: {soon.title} ({soon.currency})")
    if consensus is not None and consensus.dissenters:
        cand.risks.append(
            "เทคนิคที่ไม่สนับสนุนแผนนี้: " + ", ".join(consensus.dissenters)
            + " — ถ้าราคาไม่ไปตามแผนเร็ว ให้ถอยก่อน")
    if consensus is not None and consensus.agreement < 0.55:
        cand.risks.append(
            f"เทคนิคเห็นตรงกันแค่ {consensus.agreement:.0%} — "
            f"ลดขนาดไม้ลงจากปกติ")
    if view.data_stale:
        cand.risks.append(f"ข้อมูลราคาช้า {view.data_age_min:.0f} นาที ตรวจราคาจริงก่อนเข้า")
    if view.price_note:
        cand.risks.append(f"{view.price_note} — เทียบราคากับกระดานก่อนตั้ง Entry/SL/TP")

    # --- news, only when it was actually verified ----------------------
    if news_ctx is not None and news_ctx.verified():
        news_dir, news_why = news_ctx.bias_for(symbol)
        if news_dir == direction:
            cand.reasons.append(f"ข่าวสนับสนุน: {news_why}")
        elif news_dir == -direction:
            cand.reasons.append(f"⚠️ ข่าวสวนทาง: {news_why}")
        else:
            cand.reasons.append(f"ข่าว: {news_why}")
    else:
        cand.reasons.append("ข่าว: ไม่สามารถยืนยันข้อมูลล่าสุดได้ (ไม่ใช้ประกอบการตัดสินใจ)")

    return cand, None, view
