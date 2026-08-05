"""Daily Market Analysis Engine - one planning report, once a day.

This is deliberately not the signal engine. It runs once at 06:00 Bangkok
and produces the report a desk reads before the session starts: what the
world is pricing, what kind of market each instrument is in, where the
levels are, and three prepared plans per pair - long, short, and the case
for doing nothing. Entries are still issued separately by the signal
engine when its conditions actually fire.

Both directions are scored on every pair, every day. The signal engine
only ever scores the side the structure already agrees with, which is
right for pulling a trigger and wrong for planning: a trader needs to know
what would have to change for the other side to become valid, and that
question has no answer if the other side was never evaluated.

Nothing here is invented. Every level comes from the same structure
detectors the signal engine uses, and when a feed cannot be reached the
report says so instead of filling the gap.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import analyzer as A
from . import costs as COSTS
from . import exits as EXITS
from . import macro as MACRO
from . import regime as REG
from . import smc as S
from . import strategy as STRAT
from . import voters as VOTE

BANGKOK = timezone(timedelta(hours=7))

# The ladder a daily plan is built on. Fixed, unlike the signal engine's
# per-style ladders, because the report is about the day - not a style.
LADDER = ("W1", "D1", "H4", "H1", "M15", "M5")
PLAN_TF = "H1"          # the timeframe the plans are drawn on
BIAS_TFS = ("D1", "H4", "H1")

BUY, SELL, WAIT = "BUY", "SELL", "WAIT"


@dataclass
class Plan:
    """One prepared trade: long, short, or the reason to stand aside."""
    side: str = WAIT
    entry_low: float = 0.0
    entry_high: float = 0.0
    entry: float = 0.0
    sl: float = 0.0
    tp: tuple = (0.0, 0.0, 0.0)
    rr: float = 0.0
    win_probability: float = 0.0
    expected_value: float = 0.0
    hold_time: str = ""
    confidence: float = 0.0
    exit_mode: str = "fixed"
    why: list = field(default_factory=list)
    waiting_for: list = field(default_factory=list)
    viable: bool = False


@dataclass
class SymbolReport:
    """Everything the report says about one instrument."""
    symbol: str
    price: float = 0.0
    bias: str = WAIT
    buy_score: float = 0.0
    sell_score: float = 0.0
    regime: str = ""
    regime_confidence: float = 0.0
    volatility: str = "normal"
    strategies: list = field(default_factory=list)
    trends: dict = field(default_factory=dict)      # timeframe -> UP/DOWN/SIDE
    htf_support: str = ""
    atr: float = 0.0
    # zones
    strong_buy: tuple = ()
    weak_buy: tuple = ()
    strong_sell: tuple = ()
    weak_sell: tuple = ()
    order_block: tuple = ()
    fvg_note: str = ""
    swing_high: float = 0.0
    swing_low: float = 0.0
    liquidity_note: str = ""
    range_pos: float = 0.5
    # plans
    plan_buy: Plan = field(default_factory=Plan)
    plan_sell: Plan = field(default_factory=Plan)
    plan_wait: Plan = field(default_factory=Plan)
    now_verdict: str = WAIT
    now_why: str = ""
    # Adaptive Multi-Strategy: the ballot for each side, and the one that
    # belongs to whichever side the report ended up leaning toward.
    vote_buy: object = None
    vote_sell: object = None
    consensus: object = None
    error: str = ""


# One report per session, because a plan drawn before Tokyo has little to
# say about a London breakout eight hours later. The hours are Bangkok
# local and sit just before each session takes over, so the report lands
# while there is still time to act on it.
SESSIONS = {
    6: ("เช้า", "ก่อนเปิดลอนดอน · สรุปสิ่งที่รอบเอเชียทำไว้และวางแผนทั้งวัน"),
    14: ("บ่าย", "ช่วงลอนดอนเข้าตลาด · สภาพคล่องเข้าหนัก เทรนด์ของวันมักเกิดตรงนี้"),
    20: ("ค่ำ", "ช่วงนิวยอร์กเข้าตลาด · คาบเกี่ยวกับลอนดอน ผันผวนสูงสุดของวัน"),
}


def slot_for(now: datetime, hours) -> int | None:
    """The most recent report slot that has already come round today.

    Only the latest one is returned. If the bot was down all morning and
    wakes at 21:00 the market does not need three stale reports at once -
    it needs the current one.
    """
    local = now.astimezone(BANGKOK)
    past = [h for h in sorted(hours) if local.hour >= h]
    return past[-1] if past else None


def slot_key(now: datetime, hour: int) -> str:
    return f"{now.astimezone(BANGKOK).date().isoformat()}#{hour}"


def due(state, now: datetime, hours=(6,)) -> int | None:
    """Which report slot is still owed, if any.

    Keyed on the Bangkok date *and* the slot, so however many times the
    scan runs each session's report goes out exactly once.
    """
    if isinstance(hours, int):
        hours = (hours,)
    hour = slot_for(now, hours)
    if hour is None:
        return None
    if getattr(state, "last_daily_slot", "") == slot_key(now, hour):
        return None
    return hour


def _trend_word(structure) -> str:
    return {S.UPTREND: "UP", S.DOWNTREND: "DOWN"}.get(structure.trend, "SIDE")


def _side_score(direction: int, st_d1, st_h4, st_h1, st_entry, sm, ob,
                entry_df, h1_df, weights: dict, session: str,
                macro_dir: int, news_score: float, in_kz: bool) -> tuple:
    """Score one direction on its own merits, 0-100, with the reasons.

    The signal engine only scores the side structure already favours. A
    plan needs both, so this evaluates whichever side it is handed.
    """
    in_ote = ((0.21 <= sm.range_pos <= 0.38) if direction > 0
              else (0.62 <= sm.range_pos <= 0.79))
    vol_state = "normal"
    macro_score = 50.0 + (25.0 if macro_dir == direction and macro_dir else
                          -25.0 if macro_dir == -direction and macro_dir else 0.0)

    parts = {
        "smc": A._score_smc(direction, st_entry, ob, sm),
        "liquidity": A._score_liquidity(direction, sm),
        "trend": A._score_trend(direction, st_d1, st_h4, st_h1, st_entry),
        "ict": A._score_ict(direction, sm, st_entry, in_ote, in_kz),
        "volume": A._score_volume(direction, entry_df),
        "indicator": A._score_indicator(direction, entry_df, h1_df),
        "rr": A._score_rr(2.0),
        "spread": A._score_spread(session, vol_state),
        "news": news_score,
        "macro": macro_score,
    }
    total = sum(parts[k] * weights.get(k, 0.0) for k in parts) / sum(weights.values())

    why = []
    if st_entry.bias == direction and st_entry.recent_bos:
        why.append("มี BOS ต่อเนื่องในทิศทางนี้")
    if st_entry.bias == direction and st_entry.recent_choch:
        why.append("มี CHoCH เปลี่ยนโครงสร้างมาทางนี้")
    if (sm.sweep_bull if direction > 0 else sm.sweep_bear):
        why.append("กวาดสภาพคล่องฝั่งตรงข้ามแล้ว")
    if ob.valid:
        why.append(f"มี Order Block คุณภาพ {ob.quality:.0f}/100"
                   + (" และราคาอยู่ในโซนแล้ว" if ob.mitigating else ""))
    if (sm.fvg_bull if direction > 0 else sm.fvg_bear):
        why.append("มี Fair Value Gap หนุนทิศทางนี้")
    if in_ote:
        why.append("ราคาอยู่ในโซน OTE")
    fav_zone = (sm.range_pos <= 0.5) if direction > 0 else (sm.range_pos >= 0.5)
    why.append(("อยู่ฝั่งได้เปรียบของกรอบ" if fav_zone
                else "⚠️ อยู่ฝั่งเสียเปรียบของกรอบ")
               + f" (rangePos {sm.range_pos:.2f})")
    if macro_dir == direction and macro_dir:
        why.append("ภาพมหภาคหนุนทิศทางนี้")
    elif macro_dir == -direction and macro_dir:
        why.append("⚠️ ภาพมหภาคสวนทิศทางนี้")
    return round(A._clamp(total), 1), why, parts


def _missing_for(direction: int, st_entry, sm, ob, cfg) -> list:
    """What has yet to happen before this side becomes tradable."""
    out = []
    if not (st_entry.bias == direction and (st_entry.recent_bos or st_entry.recent_choch)):
        out.append("BOS หรือ CHoCH ในทิศทางนี้")
    if not ob.valid:
        out.append("Order Block ที่ใช้เป็นจุดเข้าได้")
    elif not ob.mitigating:
        out.append("ราคากลับมาที่ Order Block")
    if not (sm.fvg_bull if direction > 0 else sm.fvg_bear):
        out.append("Fair Value Gap ในทิศทางนี้")
    if not (sm.sweep_bull if direction > 0 else sm.sweep_bear):
        out.append("Liquidity Sweep ฝั่งตรงข้าม")
    if direction > 0 and sm.range_pos > 0.5:
        out.append("ราคาย่อลงมาฝั่ง Discount")
    if direction < 0 and sm.range_pos < 0.5:
        out.append("ราคาเด้งขึ้นไปฝั่ง Premium")
    return out


def _dynamic_sl(direction: int, price: float, st_entry, atr: float,
                sm, reg, session: str, symbol: str = "",
                entry_df=None, digits: int = 5,
                spread_multiple: float = 6.0) -> float:
    """Stop distance from structure and volatility, never a fixed number.

    The swing that would invalidate the idea sets the level; ATR sets the
    buffer beyond it so ordinary noise does not take the trade out. A
    violent market gets a wider buffer, a quiet session a tighter one, and
    the result is bounded so it can never collapse to something a spread
    would take out or balloon past what the day can carry.
    """
    vol_mult = {"high": 1.6, "normal": 1.0, "low": 0.8}.get(
        getattr(reg, "volatility", "normal"), 1.0)
    session_mult = {"Overlap": 1.15, "London": 1.05,
                    "NewYork": 1.05, "Asian": 0.85}.get(session, 1.0)
    buffer = atr * 0.35 * vol_mult * session_mult

    if direction > 0:
        level = st_entry.last_low if st_entry.last_low > 0 else price - atr
        dist = price - (level - buffer)
    else:
        level = st_entry.last_high if st_entry.last_high > 0 else price + atr
        dist = (level + buffer) - price

    if dist <= 0:
        dist = atr * 1.5 * vol_mult
    dist = max(atr * 0.9, min(atr * 4.0 * vol_mult, dist))
    # "Too tight is spread food" was asserted here without the spread ever
    # being known. Now it is estimated, and the floor is applied last so it
    # beats the ATR ceiling - the same rule the signal engine follows.
    if symbol and entry_df is not None:
        floor, _ = COSTS.stop_floor(
            symbol, entry_df, getattr(reg, "volatility", "normal"),
            spread_multiple=spread_multiple, digits=digits)
        dist = max(dist, floor)
    return dist


def _build_plan(direction: int, price: float, st_entry, sm, ob, atr: float,
                reg, session: str, score: float, why: list, missing: list,
                macro_dir: int, news_clear: bool, volume_ratio: float,
                digits: int, symbol: str = "", entry_df=None,
                spread_multiple: float = 6.0) -> Plan:
    """Turn one side's evidence into a costed plan."""
    side = BUY if direction > 0 else SELL
    plan = Plan(side=side, confidence=score, why=list(why),
                waiting_for=list(missing))

    sl_dist = _dynamic_sl(direction, price, st_entry, atr, sm, reg, session,
                          symbol, entry_df, digits, spread_multiple)
    if direction > 0:
        pool = (st_entry.last_high - price) if st_entry.last_high > price else 0.0
    else:
        pool = (price - st_entry.last_low) if st_entry.last_low < price else 0.0

    strength = 0.5 if reg is None else (0.6 if reg.is_trending else 0.25)
    exit_plan = EXITS.build(
        score=score, threshold=70.0, reg=reg, strength=strength, atr=atr,
        sl_distance=sl_dist, liquidity_distance=pool,
        volume_ratio=volume_ratio, session=session,
        macro_agrees=(1 if macro_dir == direction and macro_dir else
                      -1 if macro_dir == -direction and macro_dir else 0),
        news_clear=news_clear, momentum=strength, style_cap=4.0)

    def r(x):
        return round(float(x), digits)

    # Entry zone: the order block when there is one. Without it, the zone
    # has to lean the way the trade does - a band centred on price gave
    # BUY and SELL the identical entry, which reads as though the plan
    # does not care which way it goes.
    if ob.valid:
        lo, hi = ob.bottom, ob.top
    elif direction > 0:
        lo, hi = price - atr * 0.45, price          # buy the pullback
    else:
        lo, hi = price, price + atr * 0.45          # sell the bounce

    plan.entry_low, plan.entry_high = r(lo), r(hi)
    plan.entry = r((lo + hi) / 2)
    plan.sl = r(price - sl_dist if direction > 0 else price + sl_dist)
    # spaced after rounding: three targets that round to one price is not
    # a ladder, and that is exactly what a too-tight stop produced
    plan.tp = COSTS.space_ladder(
        price, [price + sl_dist * k * (1 if direction > 0 else -1)
                for k in exit_plan.tp_r], direction, digits)
    plan.rr = exit_plan.tp_r[1]
    plan.win_probability = exit_plan.win_probability
    plan.expected_value = exit_plan.expected_value
    plan.exit_mode = exit_plan.mode
    plan.hold_time = ("1 – 3 วัน" if reg is not None and reg.is_trending
                      else "4 – 12 ชั่วโมง")
    plan.viable = score >= 55 and exit_plan.positive and not missing[:1]
    # the exit engine restates some of the same evidence; keep the first
    # mention and drop the echo
    for extra in exit_plan.reasons[:3]:
        if extra not in plan.why:
            plan.why.append(extra)
    return plan


def analyse_symbol(symbol: str, frames: dict, cfg, reg_news_active: bool,
                   macro_view, news_ctx, session: str) -> SymbolReport:
    """The full daily read on one instrument."""
    rep = SymbolReport(symbol=symbol)
    try:
        entry_df = frames[PLAN_TF]
        rep.price = float(entry_df["close"].iloc[-1])
    except Exception as exc:            # noqa: BLE001 - one dead symbol is survivable
        rep.error = f"ไม่มีข้อมูล: {exc}"
        return rep

    digits = 2 if "JPY" in symbol or symbol == "XAUUSD" else 5
    structures = {}
    for tf in LADDER:
        df = frames.get(tf)
        if df is not None:
            structures[tf] = S.analyse_structure(df, cfg.swing_bars, cfg.struct_lookback)
            rep.trends[tf] = _trend_word(structures[tf])

    st_entry = structures.get(PLAN_TF)
    st_d1 = structures.get("D1", st_entry)
    st_h4 = structures.get("H4", st_entry)
    st_h1 = structures.get("H1", st_entry)
    if st_entry is None:
        rep.error = "โครงสร้างคำนวณไม่ได้"
        return rep

    rep.atr = S.atr(entry_df, cfg.atr_period)
    if rep.atr <= 0:
        rep.error = "คำนวณ ATR ไม่ได้"
        return rep

    sm = S.analyse_smart_money(entry_df, st_entry, rep.atr, cfg.smc_window)
    reg = REG.detect(entry_df, st_entry, sm, rep.atr, reg_news_active)
    rep.regime, rep.regime_confidence = reg.name, reg.confidence
    rep.volatility = reg.volatility
    rep.strategies = list(STRAT.select(reg)[0])
    rep.range_pos = sm.range_pos
    rep.swing_high, rep.swing_low = st_entry.last_high, st_entry.last_low

    weights = STRAT.select(reg)[1]
    macro_dir = (MACRO.bias_for(macro_view, symbol)[0]
                 if macro_view is not None else 0)
    news_score = news_ctx.score if news_ctx is not None else 50.0
    news_clear = not (news_ctx is not None and getattr(news_ctx, "upcoming", None))
    volume_ratio = S.volume_ratio(entry_df)
    h1_df = frames.get("H1", entry_df)

    rep.buy_score, buy_why, _ = _side_score(
        1, st_d1, st_h4, st_h1, st_entry, sm, sm.ob_bull, entry_df, h1_df,
        weights, session, macro_dir, news_score, False)
    rep.sell_score, sell_why, _ = _side_score(
        -1, st_d1, st_h4, st_h1, st_entry, sm, sm.ob_bear, entry_df, h1_df,
        weights, session, macro_dir, news_score, False)

    # --- Adaptive Multi-Strategy: put both sides to the vote ------------
    # Each side is judged by the techniques this regime actually calls for,
    # so the report can say which ones back BUY, which back SELL, and where
    # they contradict each other instead of only reporting two numbers.
    if getattr(cfg, "strategy_voting", True):
        macro_why = (MACRO.bias_for(macro_view, symbol)[1]
                     if macro_view is not None else "ยังไม่มีข้อมูลภาพรวมมหภาค")
        vote_ctx = {
            "st_entry": st_entry, "st_d1": st_d1, "st_h4": st_h4,
            "sm": sm, "df": entry_df, "reg": reg,
            "strength": REG._directional_strength(entry_df),
            "symbol": symbol, "session": session, "in_kill_zone": False,
            "macro_dir": macro_dir, "macro_why": macro_why,
            "news_ctx": news_ctx,
        }
        rep.vote_buy = VOTE.decide(1, vote_ctx)
        rep.vote_sell = VOTE.decide(-1, vote_ctx)
        influence = getattr(cfg, "vote_influence", 0.30)
        rep.buy_score = A._clamp(
            rep.buy_score + (rep.vote_buy.confidence - 50.0) * influence)
        rep.sell_score = A._clamp(
            rep.sell_score + (rep.vote_sell.confidence - 50.0) * influence)

    # --- which way the higher timeframes lean -------------------------
    votes = [rep.trends.get(tf) for tf in BIAS_TFS]
    ups, downs = votes.count("UP"), votes.count("DOWN")
    if ups > downs:
        rep.htf_support = f"TF ใหญ่หนุนฝั่ง BUY ({ups}/{len(votes)} ไทม์เฟรม)"
    elif downs > ups:
        rep.htf_support = f"TF ใหญ่หนุนฝั่ง SELL ({downs}/{len(votes)} ไทม์เฟรม)"
    else:
        rep.htf_support = "TF ใหญ่ยังไม่เลือกข้าง"

    # --- zones ---------------------------------------------------------
    if sm.ob_bull.valid:
        rep.strong_buy = (round(sm.ob_bull.bottom, digits),
                          round(sm.ob_bull.top, digits))
        rep.order_block = rep.strong_buy + (sm.ob_bull.quality,)
    if sm.ob_bear.valid:
        rep.strong_sell = (round(sm.ob_bear.bottom, digits),
                           round(sm.ob_bear.top, digits))
    if st_entry.last_low > 0:
        rep.weak_buy = (round(st_entry.last_low, digits),
                        round(st_entry.last_low + rep.atr * 0.5, digits))
    if st_entry.last_high > 0:
        rep.weak_sell = (round(st_entry.last_high - rep.atr * 0.5, digits),
                         round(st_entry.last_high, digits))

    fvg_bits = []
    if sm.fvg_bull:
        fvg_bits.append("ฝั่งซื้อ" + (" (เติมแล้ว)" if sm.fvg_bull_mitigated else ""))
    if sm.fvg_bear:
        fvg_bits.append("ฝั่งขาย" + (" (เติมแล้ว)" if sm.fvg_bear_mitigated else ""))
    rep.fvg_note = " · ".join(fvg_bits) if fvg_bits else "ไม่พบ FVG ที่ชัดเจน"

    liq = []
    if sm.equal_highs:
        liq.append("Equal Highs ด้านบน")
    if sm.equal_lows:
        liq.append("Equal Lows ด้านล่าง")
    if sm.sweep_bull:
        liq.append("เพิ่งกวาดฝั่งล่าง")
    if sm.sweep_bear:
        liq.append("เพิ่งกวาดฝั่งบน")
    rep.liquidity_note = " · ".join(liq) if liq else "ยังไม่มีสัญญาณกวาดสภาพคล่องชัดเจน"

    # --- plans ---------------------------------------------------------
    rep.plan_buy = _build_plan(
        1, rep.price, st_entry, sm, sm.ob_bull, rep.atr, reg, session,
        rep.buy_score, buy_why, _missing_for(1, st_entry, sm, sm.ob_bull, cfg),
        macro_dir, news_clear, volume_ratio, digits, symbol, entry_df,
        getattr(cfg, "min_sl_spreads", 6.0))
    rep.plan_sell = _build_plan(
        -1, rep.price, st_entry, sm, sm.ob_bear, rep.atr, reg, session,
        rep.sell_score, sell_why, _missing_for(-1, st_entry, sm, sm.ob_bear, cfg),
        macro_dir, news_clear, volume_ratio, digits, symbol, entry_df,
        getattr(cfg, "min_sl_spreads", 6.0))

    # --- bias and the answer for right now ------------------------------
    gap = rep.buy_score - rep.sell_score
    if rep.plan_buy.viable and gap > 5:
        rep.bias = BUY
    elif rep.plan_sell.viable and gap < -5:
        rep.bias = SELL
    else:
        rep.bias = WAIT

    rep.consensus = (rep.vote_buy if rep.bias == BUY else
                     rep.vote_sell if rep.bias == SELL else
                     rep.vote_buy if gap >= 0 else rep.vote_sell)

    if rep.bias == WAIT:
        blockers = (rep.plan_buy.waiting_for if gap >= 0
                    else rep.plan_sell.waiting_for)
        rep.plan_wait = Plan(side=WAIT, waiting_for=blockers[:4], viable=True)
        rep.now_verdict = WAIT
        rep.now_why = ("คะแนนสองฝั่งใกล้กันเกินไป "
                       f"(BUY {rep.buy_score:.0f} · SELL {rep.sell_score:.0f}) "
                       "ยังไม่มีฝั่งไหนได้เปรียบพอ" if abs(gap) <= 5
                       else "ยังขาดเงื่อนไขสำคัญ — ดูรายการที่ต้องรอด้านล่าง")
    else:
        chosen = rep.plan_buy if rep.bias == BUY else rep.plan_sell
        rep.now_verdict = rep.bias
        in_zone = chosen.entry_low <= rep.price <= chosen.entry_high
        rep.now_why = ("ราคาอยู่ในโซนเข้าแล้ว — รอแท่ง H1 ปิดยืนยัน"
                       if in_zone else
                       f"ราคายังไม่ถึงโซนเข้า ({chosen.entry_low}–{chosen.entry_high}) "
                       f"— รอให้ย้อนมาก่อน อย่าไล่ราคา")
    return rep


def risk_level(reports: list, macro_view) -> tuple:
    """(label, why) - how dangerous the day looks overall."""
    if not reports:
        return "HIGH", "ไม่มีข้อมูลพอจะประเมิน"
    high_vol = sum(1 for r in reports if r.volatility == "high")
    unclear = sum(1 for r in reports if r.bias == WAIT)
    risk_off = macro_view is not None and macro_view.risk == MACRO.RISK_OFF

    if high_vol >= len(reports) / 2 or risk_off:
        return "HIGH", ("ความผันผวนสูงหลายคู่" if high_vol else
                        "ตลาดอยู่โหมด Risk Off — เงินไหลหนีสินทรัพย์เสี่ยง")
    if unclear >= len(reports) * 0.7:
        return "MEDIUM", "ส่วนใหญ่ยังไม่เลือกทาง เหมาะกับการรอมากกว่าไล่เข้า"
    return "LOW", "โครงสร้างส่วนใหญ่อ่านได้ ความผันผวนอยู่ในเกณฑ์ปกติ"
