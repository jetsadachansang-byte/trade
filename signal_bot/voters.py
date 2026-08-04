"""Strategy Voting System - every technique scores the trade on its own.

The old scoring blended a handful of categories into one number, which
made it impossible to answer the question that actually matters: *which*
techniques back this trade, and which ones do not. So each technique here
is a separate voter. It looks at the same chart, decides how much it
supports the direction on offer from 0 to 100, and says why in its own
terms.

Two rules give the result its meaning. Only the voters that suit the
current regime are called - running harmonic pattern logic in a news-driven
spike is noise dressed as analysis - and disagreement between the voters
that *were* called costs confidence rather than being averaged away. A
trade every technique likes and a trade the techniques are split over are
not the same trade, and a single blended score cannot tell them apart.

Techniques the brief lists that cannot be computed honestly from the data
available are declared in UNAVAILABLE and never vote. Harmonic patterns
and Elliott Wave need a fitting judgement that would be invented rather
than measured; real order flow needs a feed that does not exist here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import regime as R
from . import smc as S

# Named so the report can say what it deliberately did not use.
UNAVAILABLE = (
    "Harmonic Pattern (Gartley/Bat/Butterfly/Crab/Shark/Cypher) — "
    "ต้องจับอัตราส่วน 5 จุดซึ่งตีความได้หลายแบบ ระบบไม่เดา",
    "Elliott Wave — การนับคลื่นไม่มีคำตอบเดียว ใช้เป็นตัวช่วยไม่ได้ในระบบอัตโนมัติ",
    "Order Flow / Footprint จริง — ต้องมี order book ฟีดฟรีไม่มี",
)

NEUTRAL = 50.0


@dataclass
class Vote:
    """One technique's opinion on one direction."""
    name: str
    label: str                 # Thai name for the report
    score: float = NEUTRAL     # 0-100 support for the direction asked about
    weight: float = 1.0
    reasons: list = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.score >= 65:
            return "สนับสนุน"
        if self.score <= 35:
            return "ค้าน"
        return "เป็นกลาง"

    @property
    def supports(self) -> bool:
        return self.score >= 65

    @property
    def opposes(self) -> bool:
        return self.score <= 35


@dataclass
class Consensus:
    """What the called voters concluded together."""
    score: float = NEUTRAL         # weighted 0-100
    agreement: float = 0.0         # 0-1, how aligned the voters were
    confidence: float = 0.0        # 0-100 after the agreement adjustment
    verdict: str = "WAIT"
    votes: list = field(default_factory=list)
    supporters: list = field(default_factory=list)
    dissenters: list = field(default_factory=list)
    reasoning: list = field(default_factory=list)
    selection_why: str = ""


def _clamp(x: float) -> float:
    return max(0.0, min(100.0, x))


# ----------------------------------------------------------------------
# Individual voters. Each returns 0-100 support for `direction`.
# ----------------------------------------------------------------------

def vote_smc(direction, ctx) -> Vote:
    """BOS, CHoCH, order blocks, imbalance, premium/discount."""
    from . import analyzer as A
    st, sm = ctx["st_entry"], ctx["sm"]
    ob = sm.ob_bull if direction > 0 else sm.ob_bear
    score = A._score_smc(direction, st, ob, sm)
    reasons = []
    if st.bias == direction and st.recent_bos:
        reasons.append("BOS ต่อเนื่องในทิศทางนี้")
    if st.bias == direction and st.recent_choch:
        reasons.append("CHoCH เปลี่ยนโครงสร้างมาทางนี้")
    if ob.valid:
        reasons.append(f"Order Block คุณภาพ {ob.quality:.0f}"
                       + (" · ราคาอยู่ในโซนแล้ว" if ob.mitigating else " · รอราคากลับมา"))
    if (sm.fvg_bull if direction > 0 else sm.fvg_bear):
        reasons.append("มี Fair Value Gap หนุน")
    if not reasons:
        reasons.append("ไม่มีหลักฐานโครงสร้างฝั่งนี้")
    return Vote("smc", "SMC", score, reasons=reasons)


def vote_ict(direction, ctx) -> Vote:
    """OTE, Power of Three, kill zone, Judas-style reversal."""
    from . import analyzer as A
    sm, st = ctx["sm"], ctx["st_entry"]
    in_ote = ((0.21 <= sm.range_pos <= 0.38) if direction > 0
              else (0.62 <= sm.range_pos <= 0.79))
    score = A._score_ict(direction, sm, st, in_ote, ctx.get("in_kill_zone", False))
    reasons = []
    if in_ote:
        reasons.append("ราคาอยู่ในโซน OTE (0.62-0.79 ของการย่อ)")
    # mirror the scoring conditions exactly, bias check included, so a
    # reason can never read as support for points that were not awarded
    swept = sm.sweep_bull if direction > 0 else sm.sweep_bear
    if swept and st.recent_bos and st.bias == direction:
        reasons.append("Power of Three: กวาดสภาพคล่องแล้วเบรกโครงสร้างมาทางนี้")
    elif swept and st.recent_bos:
        reasons.append("⚠️ กวาดสภาพคล่องแล้วเบรก แต่เบรกไปคนละทางกับไม้นี้")
    if ctx.get("in_kill_zone"):
        reasons.append("อยู่ในช่วง Kill Zone")
    if not reasons:
        reasons.append("ยังไม่เข้าเงื่อนไข ICT ที่ชัดเจน")
    return Vote("ict", "ICT", score, reasons=reasons)


def vote_wyckoff(direction, ctx) -> Vote:
    """Accumulation or distribution, and the spring/upthrust that ends it.

    Read from range behaviour and effort: a market compressed near the low
    of its range that keeps rejecting lower prices on rising volume is
    accumulating, whatever the trend label says.
    """
    df, sm = ctx["df"], ctx["sm"]
    pos, vol = sm.range_pos, S.volume_ratio(df)
    strength = ctx["strength"]
    score, reasons = NEUTRAL, []

    accumulating = pos < 0.4 and strength < 0.4
    distributing = pos > 0.6 and strength < 0.4
    spring = sm.sweep_bull and pos < 0.35        # swept the low and reclaimed
    upthrust = sm.sweep_bear and pos > 0.65      # poked the high and failed

    if direction > 0:
        if spring:
            score = 88.0
            reasons.append("Spring — กวาดใต้ฐานแล้วดึงกลับ (Phase C)")
        elif accumulating:
            score = 72.0
            reasons.append("อยู่ในโซนสะสม (Accumulation) ใกล้ฐานกรอบ")
        elif upthrust:
            score = 22.0
            reasons.append("Upthrust — สัญญาณกระจายของ ตรงข้ามกับการซื้อ")
        elif distributing:
            score = 30.0
            reasons.append("อยู่ในโซนกระจาย (Distribution) ไม่เหมาะซื้อ")
    else:
        if upthrust:
            score = 88.0
            reasons.append("Upthrust — ดันเหนือยอดแล้วถูกกดกลับ (Phase C)")
        elif distributing:
            score = 72.0
            reasons.append("อยู่ในโซนกระจาย (Distribution) ใกล้ยอดกรอบ")
        elif spring:
            score = 22.0
            reasons.append("Spring — สัญญาณสะสมของ ตรงข้ามกับการขาย")
        elif accumulating:
            score = 30.0
            reasons.append("อยู่ในโซนสะสม (Accumulation) ไม่เหมาะขาย")

    if vol >= 1.4:
        score += 6
        reasons.append(f"มี effort ชัด (volume {vol:.1f}x)")
    if not reasons:
        reasons.append("ยังไม่เห็นเฟส Wyckoff ที่ชัดเจน")
    return Vote("wyckoff", "Wyckoff", _clamp(score), reasons=reasons)


def vote_dow(direction, ctx) -> Vote:
    """Dow Theory: does the primary trend agree, and is the secondary with it."""
    primary = ctx["st_d1"]
    secondary = ctx["st_h4"]
    want = S.UPTREND if direction > 0 else S.DOWNTREND
    against = S.DOWNTREND if direction > 0 else S.UPTREND

    score, reasons = NEUTRAL, []
    if primary.trend == want:
        score += 25
        reasons.append("Primary Trend ไปทางเดียวกัน (ยอด/ฐานยกตัวสอดคล้อง)")
    elif primary.trend == against:
        score -= 30
        reasons.append("⚠️ Primary Trend สวนทาง — เทรดทวนเทรนด์ใหญ่")
    if secondary.trend == want:
        score += 20
        reasons.append("Secondary Trend สอดคล้อง")
    elif secondary.trend == against:
        score -= 15
        reasons.append("Secondary Trend ย่อสวน — อาจเป็นแค่การพักตัว")
    if not reasons:
        reasons.append("โครงสร้างยอด/ฐานยังไม่ชี้ทางชัด")
    return Vote("dow", "Dow Theory", _clamp(score), reasons=reasons)


def vote_trend(direction, ctx) -> Vote:
    """Trend following: EMA stack, slope, and how efficiently price travels."""
    df = ctx["df"]
    close = df["close"]
    fast, slow = S.ema(close, 20), S.ema(close, 50)
    stacked = float(fast.iloc[-2]) - float(slow.iloc[-2])
    strength = ctx["strength"]

    score, reasons = NEUTRAL, []
    if (stacked > 0) == (direction > 0):
        score += 22
        reasons.append("EMA20 อยู่ถูกฝั่งของ EMA50")
    else:
        score -= 20
        reasons.append("⚠️ EMA20 อยู่ผิดฝั่งของ EMA50")

    # efficiency stands in for ADX: how much ground was actually covered
    if strength >= 0.45:
        score += 20
        reasons.append(f"ราคาเดินทางจริง {strength:.0%} ของระยะที่แกว่ง — เทรนด์มีแรง")
    elif strength < 0.2:
        score -= 15
        reasons.append(f"เดินทางจริงแค่ {strength:.0%} — แกว่งมากกว่าจะไปไหน")

    price = float(close.iloc[-1])
    vwap = ctx.get("vwap")
    if vwap:
        if (price > vwap) == (direction > 0):
            score += 8
            reasons.append("ราคาอยู่ถูกฝั่งของ VWAP")
        else:
            score -= 8
            reasons.append("ราคาอยู่ผิดฝั่งของ VWAP")
    return Vote("trend", "Trend Following", _clamp(score), reasons=reasons)


def vote_mean_reversion(direction, ctx) -> Vote:
    """Bollinger position and RSI extremes - the fade case."""
    df = ctx["df"]
    close = df["close"]
    ma = close.rolling(20).mean()
    sd = close.rolling(20).std()
    if len(close) < 25 or pd.isna(sd.iloc[-2]) or sd.iloc[-2] == 0:
        return Vote("mean_reversion", "Mean Reversion", NEUTRAL,
                    reasons=["ข้อมูลไม่พอคำนวณ Bollinger"])
    z = (float(close.iloc[-2]) - float(ma.iloc[-2])) / float(sd.iloc[-2])
    r = float(S.rsi(close).iloc[-2])

    score, reasons = NEUTRAL, []
    if direction > 0:
        if z <= -1.8:
            score += 28
            reasons.append(f"ราคาหลุดขอบล่าง Bollinger ({z:.1f}σ) — โซนเด้งกลับ")
        elif z >= 1.8:
            score -= 25
            reasons.append(f"ราคายืดเหนือขอบบน ({z:+.1f}σ) — ไล่ซื้อตรงนี้เสี่ยง")
        if r <= 32:
            score += 16
            reasons.append(f"RSI {r:.0f} — ขายมากเกินไป")
        elif r >= 70:
            score -= 14
            reasons.append(f"RSI {r:.0f} — ซื้อมากเกินไปแล้ว")
    else:
        if z >= 1.8:
            score += 28
            reasons.append(f"ราคายืดเหนือขอบบน Bollinger ({z:+.1f}σ) — โซนย่อกลับ")
        elif z <= -1.8:
            score -= 25
            reasons.append(f"ราคาหลุดขอบล่าง ({z:.1f}σ) — ไล่ขายตรงนี้เสี่ยง")
        if r >= 68:
            score += 16
            reasons.append(f"RSI {r:.0f} — ซื้อมากเกินไป")
        elif r <= 30:
            score -= 14
            reasons.append(f"RSI {r:.0f} — ขายมากเกินไปแล้ว")
    if not reasons:
        reasons.append("ราคายังอยู่กลางกรอบ Bollinger ไม่มีขอบให้เล่น")
    return Vote("mean_reversion", "Mean Reversion", _clamp(score), reasons=reasons)


def vote_breakout(direction, ctx) -> Vote:
    """Compression then expansion, and whether the break held."""
    df, st = ctx["df"], ctx["st_entry"]
    spans = df["high"].sub(df["low"])
    recent = float(spans.iloc[-6:-1].mean())
    baseline = float(spans.iloc[-60:-6].mean()) if len(spans) > 60 else recent
    expansion = recent / baseline if baseline > 0 else 1.0
    price = float(df["close"].iloc[-1])

    score, reasons = NEUTRAL, []
    if expansion >= 1.4:
        score += 18
        reasons.append(f"ช่วงราคาขยายตัว {expansion:.1f}x — พลังงานถูกปล่อยแล้ว")
    elif expansion <= 0.7:
        score -= 5
        reasons.append(f"ช่วงราคาบีบตัว {expansion:.1f}x — ยังไม่ปล่อยพลังงาน")

    if direction > 0 and st.last_high > 0 and price > st.last_high:
        score += 25
        reasons.append("ราคาทะลุยอดเดิมขึ้นไปแล้ว")
    elif direction < 0 and st.last_low > 0 and price < st.last_low:
        score += 25
        reasons.append("ราคาทะลุฐานเดิมลงไปแล้ว")
    elif st.recent_bos and st.bias == direction:
        score += 14
        reasons.append("เพิ่งเบรกโครงสร้างในทิศทางนี้")
    if not reasons:
        reasons.append("ยังไม่มีการเบรกที่ยืนยันได้")
    return Vote("breakout", "Volatility Breakout", _clamp(score), reasons=reasons)


def vote_vsa(direction, ctx) -> Vote:
    """Volume Spread Analysis: effort against result.

    FX carries tick volume rather than delivered size, so this measures
    activity, not institutional participation - which is stated on the
    vote rather than left to be assumed.
    """
    df = ctx["df"]
    bar = df.iloc[-2]
    spread = float(bar["high"] - bar["low"])
    if spread <= 0:
        return Vote("vsa", "VSA", NEUTRAL, reasons=["แท่งไม่มีช่วงราคา"])
    close_pos = (float(bar["close"]) - float(bar["low"])) / spread   # 0 low, 1 high
    vol = S.volume_ratio(df)
    spans = df["high"].sub(df["low"])
    wide = spread > float(spans.iloc[-30:-1].mean()) * 1.3

    score, reasons = NEUTRAL, []
    if direction > 0:
        if vol >= 1.5 and close_pos >= 0.65:
            score += 26
            reasons.append("Stopping Volume — แรงเข้าหนักและปิดใกล้ยอดแท่ง")
        if vol <= 0.7 and close_pos <= 0.4 and wide:
            score += 18
            reasons.append("No Supply — แท่งลงกว้างแต่แรงเบา ฝั่งขายหมดแรง")
        if vol >= 1.5 and close_pos <= 0.3 and wide:
            score -= 24
            reasons.append("⚠️ Climactic Volume ฝั่งขาย — แรงมากแต่ปิดใกล้ก้นแท่ง")
    else:
        if vol >= 1.5 and close_pos <= 0.35:
            score += 26
            reasons.append("Stopping Volume ฝั่งขาย — แรงเข้าหนักและปิดใกล้ก้นแท่ง")
        if vol <= 0.7 and close_pos >= 0.6 and wide:
            score += 18
            reasons.append("No Demand — แท่งขึ้นกว้างแต่แรงเบา ฝั่งซื้อหมดแรง")
        if vol >= 1.5 and close_pos >= 0.7 and wide:
            score -= 24
            reasons.append("⚠️ Climactic Volume ฝั่งซื้อ — แรงมากแต่ปิดใกล้ยอดแท่ง")
    if not reasons:
        reasons.append(f"แรงกับผลลัพธ์สมดุลกัน (volume {vol:.1f}x, ปิดที่ {close_pos:.0%} ของแท่ง)")
    reasons.append("<i>หมายเหตุ: FX ใช้ tick volume ไม่ใช่ปริมาณส่งมอบจริง</i>")
    return Vote("vsa", "VSA", _clamp(score), reasons=reasons)


def vote_market_profile(direction, ctx) -> Vote:
    """Where price sits against the volume-weighted value area."""
    df = ctx["df"]
    window = df.iloc[-120:] if len(df) > 120 else df
    if len(window) < 20:
        return Vote("market_profile", "Market Profile", NEUTRAL,
                    reasons=["แท่งไม่พอสร้างโปรไฟล์"])

    mids = ((window["high"] + window["low"]) / 2).dropna()
    vols = window["volume"].reindex(mids.index).fillna(0.0).replace(0, 1.0)
    bins = 24
    if len(mids) < 20:
        return Vote("market_profile", "Market Profile", NEUTRAL,
                    reasons=["ข้อมูลราคาไม่ครบพอสร้างโปรไฟล์"])
    lo, hi = float(mids.min()), float(mids.max())
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return Vote("market_profile", "Market Profile", NEUTRAL,
                    reasons=["ช่วงราคาแคบเกินไป"])
    idx = ((mids - lo) / (hi - lo) * (bins - 1)).astype(int)
    hist = np.zeros(bins)
    for b, v in zip(idx, vols):
        hist[int(b)] += float(v)

    poc_bin = int(np.argmax(hist))
    poc = lo + (poc_bin + 0.5) * (hi - lo) / bins
    # value area: the bins around the POC holding 70% of activity
    order = np.argsort(-hist)
    total, acc, chosen = hist.sum(), 0.0, []
    for b in order:
        chosen.append(int(b))
        acc += hist[b]
        if acc >= total * 0.7:
            break
    va_lo = lo + (min(chosen)) * (hi - lo) / bins
    va_hi = lo + (max(chosen) + 1) * (hi - lo) / bins
    price = float(df["close"].iloc[-1])

    score, reasons = NEUTRAL, []
    if price < va_lo:
        reasons.append(f"ราคาต่ำกว่า Value Area ({va_lo:.5g}) — ของถูกเทียบมูลค่าที่ตลาดยอมรับ")
        score += 20 if direction > 0 else -18
    elif price > va_hi:
        reasons.append(f"ราคาสูงกว่า Value Area ({va_hi:.5g}) — ของแพงเทียบมูลค่าที่ตลาดยอมรับ")
        score += 20 if direction < 0 else -18
    else:
        reasons.append(f"ราคาอยู่ใน Value Area ({va_lo:.5g}–{va_hi:.5g}) — ตลาดยอมรับราคานี้")
        score -= 6
    reasons.append(f"POC (ราคาที่ซื้อขายหนาแน่นสุด) {poc:.5g}")
    return Vote("market_profile", "Market Profile", _clamp(score), reasons=reasons)


def vote_candlestick(direction, ctx) -> Vote:
    """The pattern the last closed bar actually made."""
    df = ctx["df"]
    if len(df) < 4:
        return Vote("candlestick", "Candlestick", NEUTRAL, reasons=["แท่งไม่พอ"])
    c1, c2 = df.iloc[-2], df.iloc[-3]          # last closed, and the one before
    o1, h1, l1, cl1 = (float(c1["open"]), float(c1["high"]),
                       float(c1["low"]), float(c1["close"]))
    o2, cl2 = float(c2["open"]), float(c2["close"])
    body, span = abs(cl1 - o1), h1 - l1
    if span <= 0:
        return Vote("candlestick", "Candlestick", NEUTRAL, reasons=["แท่งไม่มีช่วงราคา"])
    upper, lower = h1 - max(o1, cl1), min(o1, cl1) - l1
    bull = cl1 > o1

    score, reasons = NEUTRAL, []
    if direction > 0:
        if bull and cl1 >= max(o2, cl2) and o1 <= min(o2, cl2):
            score += 28
            reasons.append("Bullish Engulfing — แท่งเขียวกลืนแท่งก่อนหน้า")
        if lower > body * 2 and lower > upper * 2:
            score += 22
            reasons.append("Pin Bar / ค้อน — ไส้ล่างยาว ถูกซื้อกลับ")
        if not bull and upper > body * 2:
            score -= 18
            reasons.append("⚠️ ไส้บนยาวบนแท่งแดง — ถูกขายกดลงมา")
    else:
        if not bull and cl1 <= min(o2, cl2) and o1 >= max(o2, cl2):
            score += 28
            reasons.append("Bearish Engulfing — แท่งแดงกลืนแท่งก่อนหน้า")
        if upper > body * 2 and upper > lower * 2:
            score += 22
            reasons.append("Pin Bar / ดาวตก — ไส้บนยาว ถูกขายกลับ")
        if bull and lower > body * 2:
            score -= 18
            reasons.append("⚠️ ไส้ล่างยาวบนแท่งเขียว — ถูกซื้อดันขึ้น")

    if body < span * 0.2:
        reasons.append("ตัวแท่งเล็กเทียบช่วงราคา — ยังลังเล")
    if not reasons:
        reasons.append("ไม่มีรูปแบบแท่งเทียนที่มีนัยสำคัญ")
    return Vote("candlestick", "Candlestick", _clamp(score), reasons=reasons)


def vote_chart_pattern(direction, ctx) -> Vote:
    """Double tops and bottoms, and range compression.

    Only the patterns that can be identified from swing points without a
    judgement call. Triangles and head-and-shoulders need a fit that would
    be asserted rather than measured, so they are left out.
    """
    df, sm = ctx["df"], ctx["sm"]
    score, reasons = NEUTRAL, []

    if sm.equal_highs:
        reasons.append("Double Top — ยอดสองยอดใกล้กัน เป็นแนวต้านและกองสภาพคล่อง")
        score += 20 if direction < 0 else -16
    if sm.equal_lows:
        reasons.append("Double Bottom — ฐานสองฐานใกล้กัน เป็นแนวรับและกองสภาพคล่อง")
        score += 20 if direction > 0 else -16

    spans = df["high"].sub(df["low"])
    if len(spans) > 60:
        recent = float(spans.iloc[-20:-1].mean())
        older = float(spans.iloc[-60:-20].mean())
        if older > 0 and recent / older <= 0.7:
            score += 8
            reasons.append("กรอบราคาบีบแคบลง — มักตามด้วยการเบรก")
    if not reasons:
        reasons.append("ไม่พบรูปแบบกราฟที่ระบุได้อย่างมั่นใจ")
    return Vote("chart_pattern", "Chart Pattern", _clamp(score), reasons=reasons)


def vote_momentum(direction, ctx) -> Vote:
    """RSI, MACD and stochastic - confirmation only, never a reason to enter."""
    df = ctx["df"]
    close = df["close"]
    score, reasons = NEUTRAL, []

    r = float(S.rsi(close).iloc[-2])
    if (r > 50) == (direction > 0):
        score += 14
        reasons.append(f"RSI {r:.0f} อยู่ถูกฝั่งของ 50")
    else:
        score -= 12
        reasons.append(f"RSI {r:.0f} อยู่ผิดฝั่งของ 50")

    main, sig = S.macd(close)
    if (main > sig) == (direction > 0):
        score += 14
        reasons.append("MACD อยู่ถูกฝั่งของเส้นสัญญาณ")
    else:
        score -= 12
        reasons.append("MACD อยู่ผิดฝั่งของเส้นสัญญาณ")

    low14 = close.rolling(14).min()
    high14 = close.rolling(14).max()
    rng = float(high14.iloc[-2] - low14.iloc[-2])
    if rng > 0:
        k = (float(close.iloc[-2]) - float(low14.iloc[-2])) / rng * 100
        if direction > 0 and k < 25:
            score += 10
            reasons.append(f"Stochastic {k:.0f} — ต่ำ พร้อมเด้ง")
        elif direction < 0 and k > 75:
            score += 10
            reasons.append(f"Stochastic {k:.0f} — สูง พร้อมย่อ")
    reasons.append("<i>ใช้ยืนยันเท่านั้น ไม่ใช่เหตุผลเข้าเทรด</i>")
    return Vote("momentum", "Momentum", _clamp(score), reasons=reasons)


def vote_intermarket(direction, ctx) -> Vote:
    """What the global tape implies for this instrument."""
    macro_dir = ctx.get("macro_dir", 0)
    macro_why = ctx.get("macro_why", "")
    if not macro_dir:
        return Vote("intermarket", "Intermarket", NEUTRAL,
                    reasons=[macro_why or "ภาพมหภาคยังไม่ชี้ทาง"])
    score = 78.0 if macro_dir == direction else 24.0
    return Vote("intermarket", "Intermarket", score,
                reasons=[macro_why or ("มหภาคหนุนทิศทางนี้" if macro_dir == direction
                                       else "⚠️ มหภาคสวนทิศทางนี้")])


def vote_correlation(direction, ctx) -> Vote:
    """Whether the instruments that usually move together still are.

    A real coefficient needs both series; when only the macro snapshot is
    available this reports directional consistency instead and says so,
    rather than presenting one as the other.
    """
    ref = ctx.get("ref_series")
    own = ctx.get("own_daily")
    if ref is not None and own is not None and len(ref) > 25 and len(own) > 25:
        n = min(len(ref), len(own), 60)
        a = pd.Series(own[-n:]).pct_change().dropna()
        b = pd.Series(ref[-n:]).pct_change().dropna()
        m = min(len(a), len(b))
        if m > 10:
            corr = float(np.corrcoef(a[-m:], b[-m:])[0, 1])
            expected = ctx.get("expected_corr", -1.0)
            aligned = (corr < 0) == (expected < 0)
            score = 70.0 if aligned else 38.0
            return Vote("correlation", "Correlation", score, reasons=[
                f"ค่าสหสัมพันธ์กับ DXY {corr:+.2f} "
                + ("สอดคล้องกับความสัมพันธ์ปกติ" if aligned
                   else "⚠️ ผิดจากความสัมพันธ์ปกติ — ตลาดกำลังให้น้ำหนักอย่างอื่น")])
    macro_dir = ctx.get("macro_dir", 0)
    if not macro_dir:
        return Vote("correlation", "Correlation", NEUTRAL,
                    reasons=["ไม่มีคู่อ้างอิงให้คำนวณค่าสหสัมพันธ์จริง"])
    return Vote("correlation", "Correlation",
                62.0 if macro_dir == direction else 34.0,
                reasons=["ประเมินจากทิศทางมหภาคเท่านั้น "
                         "<i>(ไม่ใช่ค่าสหสัมพันธ์ที่คำนวณจริง)</i>"])


def vote_news(direction, ctx) -> Vote:
    """The calendar, and only when it can actually be read."""
    news = ctx.get("news_ctx")
    if news is None:
        return Vote("news", "News", NEUTRAL, reasons=["ไม่ได้เปิดใช้การอ่านข่าว"])
    if not news.verified():
        return Vote("news", "News", NEUTRAL,
                    reasons=["ไม่สามารถยืนยันข้อมูลข่าวล่าสุดได้ — "
                             "จะไม่ใช้ข่าวประกอบการตัดสินใจ"])
    bias, why = news.bias_for(ctx["symbol"])
    if bias == direction and bias:
        return Vote("news", "News", 76.0, reasons=[f"ข่าวสนับสนุน: {why}"])
    if bias == -direction and bias:
        return Vote("news", "News", 26.0, reasons=[f"⚠️ ข่าวสวนทาง: {why}"])
    score = news.score if getattr(news, "score", None) is not None else NEUTRAL
    return Vote("news", "News", _clamp(score), reasons=[why or "ไม่มีข่าวแรงใกล้ ๆ"])


def vote_risk(direction, ctx) -> Vote:
    """Execution conditions: session, volatility, and how far the stop must sit."""
    from . import analyzer as A
    reg = ctx["reg"]
    score = A._score_spread(ctx.get("session", ""), reg.volatility)
    reasons = [f"สภาพการเข้าออก: session {ctx.get('session') or 'นอกเวลาหลัก'} "
               f"· ความผันผวน {reg.volatility}"]
    if reg.volatility == "high":
        reasons.append("⚠️ ผันผวนสูง — สเปรดกว้างและ SL ต้องกว้างตาม")
    return Vote("risk", "Risk / Execution", _clamp(score), reasons=reasons)


ALL_VOTERS = {
    "smc": vote_smc, "ict": vote_ict, "wyckoff": vote_wyckoff, "dow": vote_dow,
    "trend": vote_trend, "mean_reversion": vote_mean_reversion,
    "breakout": vote_breakout, "vsa": vote_vsa,
    "market_profile": vote_market_profile, "candlestick": vote_candlestick,
    "chart_pattern": vote_chart_pattern, "momentum": vote_momentum,
    "intermarket": vote_intermarket, "correlation": vote_correlation,
    "news": vote_news, "risk": vote_risk,
}

# Which techniques suit which market. Straight from the brief's selector,
# with the always-on trio (news, risk, intermarket) added because ignoring
# the calendar or the spread is never the right call in any regime.
ALWAYS = ("news", "risk", "intermarket")

SELECTOR = {
    R.STRONG_BULL: ("smc", "ict", "trend", "momentum", "dow"),
    R.STRONG_BEAR: ("smc", "ict", "trend", "momentum", "dow"),
    R.WEAK_BULL: ("smc", "dow", "trend", "candlestick"),
    R.WEAK_BEAR: ("smc", "dow", "trend", "candlestick"),
    R.RANGE: ("wyckoff", "mean_reversion", "market_profile", "chart_pattern"),
    R.MEAN_REVERSION: ("mean_reversion", "market_profile", "wyckoff", "candlestick"),
    R.COMPRESSION: ("breakout", "market_profile", "chart_pattern", "wyckoff"),
    R.EXPANSION: ("breakout", "trend", "momentum", "vsa"),
    R.BREAKOUT_TRUE: ("breakout", "vsa", "trend", "smc"),
    R.BREAKOUT_FALSE: ("smc", "wyckoff", "vsa", "mean_reversion"),
    R.LIQUIDITY_HUNT: ("smc", "ict", "wyckoff", "candlestick"),
    R.TREND_EXHAUSTION: ("wyckoff", "vsa", "mean_reversion", "candlestick"),
    R.NEWS_DRIVEN: ("vsa", "breakout", "market_profile"),
}

_WHY = {
    R.STRONG_BULL: "เทรนด์แรง — ใช้ SMC + ICT + Trend Following ตามโมเมนตัม",
    R.STRONG_BEAR: "เทรนด์แรง — ใช้ SMC + ICT + Trend Following ตามโมเมนตัม",
    R.WEAK_BULL: "เทรนด์อ่อน — ใช้ SMC + Dow Theory ดูว่าเทรนด์ยังมีจริงไหม",
    R.WEAK_BEAR: "เทรนด์อ่อน — ใช้ SMC + Dow Theory ดูว่าเทรนด์ยังมีจริงไหม",
    R.RANGE: "ตลาดในกรอบ — ใช้ Wyckoff + Mean Reversion + Market Profile",
    R.MEAN_REVERSION: "ตลาดกลับค่าเฉลี่ย — ใช้ Mean Reversion + Market Profile",
    R.COMPRESSION: "ราคาบีบตัว — ใช้ Breakout + Market Profile เตรียมรับการปล่อยพลังงาน",
    R.EXPANSION: "ความผันผวนขยาย — ใช้ Breakout + Volume + Trend",
    R.BREAKOUT_TRUE: "เบรกจริง — ใช้ Breakout + Volume ยืนยัน",
    R.BREAKOUT_FALSE: "เบรกหลอก — ใช้ Liquidity Sweep + Wyckoff จับการกลับตัว",
    R.LIQUIDITY_HUNT: "กำลังไล่ล่าสภาพคล่อง — ใช้ SMC + ICT เป็นหลัก",
    R.TREND_EXHAUSTION: "เทรนด์หมดแรง — ใช้ Wyckoff + VSA จับการกระจาย/สะสม",
    R.NEWS_DRIVEN: "ข่าวขับเคลื่อน — ใช้ News + Intermarket + VSA ไม่ใช้เทคนิคกราฟล้วน",
}


def decide(direction: int, ctx: dict, weights: dict | None = None) -> Consensus:
    """Call the voters this market deserves and weigh what they said.

    Agreement is what separates a real setup from an average of opinions:
    when the called techniques line up the confidence rises, and when they
    split it falls until the honest answer is to wait.
    """
    reg = ctx["reg"]
    chosen = tuple(SELECTOR.get(reg.name, ("smc", "trend", "candlestick"))) + ALWAYS
    weights = weights or {}

    votes = []
    for name in chosen:
        fn = ALL_VOTERS.get(name)
        if fn is None:
            continue
        try:
            v = fn(direction, ctx)
        except Exception as exc:        # noqa: BLE001 - a broken voter must not stop the rest
            v = Vote(name, name, NEUTRAL, reasons=[f"คำนวณไม่ได้: {exc}"])
        v.weight = float(weights.get(name, 1.0))
        votes.append(v)

    total_w = sum(v.weight for v in votes) or 1.0
    score = sum(v.score * v.weight for v in votes) / total_w

    # How aligned were they? Spread of opinion, not just the average.
    opinions = [v.score for v in votes]
    spread = float(np.std(opinions)) if len(opinions) > 1 else 0.0
    agreement = max(0.0, min(1.0, 1.0 - spread / 30.0))

    supporters = [v for v in votes if v.supports]
    dissenters = [v for v in votes if v.opposes]

    # Confidence starts at the weighted score and is pulled toward neutral
    # by disagreement - a 70 that half the voters argue with is not a 70.
    confidence = NEUTRAL + (score - NEUTRAL) * (0.55 + 0.45 * agreement)

    if len(dissenters) >= max(2, len(votes) // 3):
        verdict = "WAIT"
    elif confidence >= 62 and len(supporters) >= 2:
        verdict = "BUY" if direction > 0 else "SELL"
    else:
        verdict = "WAIT"

    reasoning = [
        f"เลือกเทคนิคตามสภาพตลาด: {_WHY.get(reg.name, reg.name)}",
        f"เรียกใช้ {len(votes)} เทคนิค — สนับสนุน {len(supporters)} · "
        f"ค้าน {len(dissenters)} · เป็นกลาง {len(votes) - len(supporters) - len(dissenters)}",
        f"ความสอดคล้องระหว่างเทคนิค {agreement:.0%}"
        + (" — สูง จึงเพิ่มความมั่นใจ" if agreement >= 0.7
           else " — ต่ำ จึงลดความมั่นใจลง"),
    ]
    if dissenters:
        reasoning.append("เทคนิคที่ไม่สนับสนุน: "
                         + ", ".join(f"{v.label} ({v.score:.0f})" for v in dissenters))
    if verdict == "WAIT" and len(dissenters) >= 2:
        reasoning.append("⚠️ เทคนิคขัดแย้งกันมาก — แนะนำรอจนกว่าจะสอดคล้องกว่านี้")

    return Consensus(score=round(score, 1), agreement=round(agreement, 3),
                     confidence=round(_clamp(confidence), 1), verdict=verdict,
                     votes=votes, supporters=[v.label for v in supporters],
                     dissenters=[v.label for v in dissenters],
                     reasoning=reasoning, selection_why=_WHY.get(reg.name, reg.name))
