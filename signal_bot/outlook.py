"""The full read on one instrument: trend, levels, scenarios, news.

This is the analysis the bot exists to produce. It answers, for one pair,
in one message:

  - which way each timeframe is pointing, from the weekly down to M5, and
    whether they agree with each other
  - where the long-term trend actually is, separately from today's noise
  - the levels that matter above and below, and why each one matters
  - what happens *if* price reaches each of them - break or reject, and
    where it goes next in either case
  - which techniques back the read and which argue with it
  - the news that can move this pair today

Nothing is invented. The trends come from structure, the levels from
places the market has already turned, the scenarios from those levels,
and the news from the calendar feed - and when a feed cannot be reached
the report says so rather than filling the gap with a guess.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from . import levels as LV
from .data import digits_for as DIGITS_FOR
from . import news as NEWS
from . import smc as S

# Weekly at the top so the message reads top-down: the big picture first,
# then the chart the reader is actually watching.
TF_LADDER = ("W1", "D1", "H4", "H1", "M15", "M5")
MAJOR_TFS = ("W1", "D1")
MID_TFS = ("H4", "H1")
MINOR_TFS = ("M15", "M5")

UP, DOWN, SIDE = "UP", "DOWN", "SIDE"
_WORD = {UP: "ขาขึ้น", DOWN: "ขาลง", SIDE: "ออกข้าง"}
_ARROW = {UP: "▲", DOWN: "▼", SIDE: "↔"}


@dataclass
class TFRead:
    """One timeframe's verdict, and the one fact behind it."""
    tf: str
    trend: str = SIDE
    note: str = ""

    @property
    def arrow(self) -> str:
        return _ARROW.get(self.trend, "↔")

    @property
    def word(self) -> str:
        return _WORD.get(self.trend, "ออกข้าง")


@dataclass
class Outlook:
    """Everything one message about one instrument needs."""
    symbol: str
    price: float = 0.0
    digits: int = 5
    quote_tf: str = ""
    price_age_min: float = 0.0
    # trend
    reads: list = field(default_factory=list)      # list[TFRead], W1 -> M5
    long_term: str = ""
    alignment: str = ""
    direction: int = 0                             # the read's overall lean
    # The two halves of the picture, stated separately so a reader can see
    # a weekly uptrend and an hourly pullback at the same time instead of
    # having to reconcile one averaged verdict.
    big_picture: list = field(default_factory=list)     # W1 · D1
    small_picture: list = field(default_factory=list)   # H4 · H1 · M15 · M5
    primary_path: str = ""
    alternate_path: str = ""
    # levels and what happens at them
    level_map: object = None
    scenarios: list = field(default_factory=list)
    range_note: str = ""
    invalidation: str = ""
    # evidence
    regime: str = ""
    regime_confidence: float = 0.0
    volatility: str = "normal"
    atr: float = 0.0
    daily_atr: float = 0.0
    buy_score: float = 0.0
    sell_score: float = 0.0
    # The straight answer to "can this be entered right now", carried over
    # from the report so retiring the market-pulse table does not take the
    # only place that question was ever answered plainly.
    verdict: str = "WAIT"
    verdict_why: str = ""
    techniques_for: list = field(default_factory=list)
    techniques_against: list = field(default_factory=list)
    # news
    events: list = field(default_factory=list)
    news_note: str = ""
    error: str = ""


def _trend_of(structure) -> str:
    return {S.UPTREND: UP, S.DOWNTREND: DOWN}.get(
        getattr(structure, "trend", ""), SIDE)


def _note_for(structure, df) -> str:
    """The single most useful thing to say about one timeframe."""
    if structure is None:
        return "ข้อมูลไม่พอ"
    bits = []
    if getattr(structure, "recent_choch", False):
        bits.append("เพิ่งเปลี่ยนโครงสร้าง (CHoCH)")
    elif getattr(structure, "recent_bos", False):
        bits.append("เพิ่งเบรกโครงสร้าง (BOS)")
    trend = _trend_of(structure)
    if trend == UP:
        bits.append("ยอดสูงขึ้น ก้นสูงขึ้น")
    elif trend == DOWN:
        bits.append("ยอดต่ำลง ก้นต่ำลง")
    else:
        bits.append("ยังไม่ทำโครงสร้างใหม่")
    if df is not None:
        try:
            ema_dir = S.ema_trend(df)
        except Exception:               # noqa: BLE001 - a note is not worth a crash
            ema_dir = 0
        if ema_dir > 0:
            bits.append("EMA เรียงขึ้น")
        elif ema_dir < 0:
            bits.append("EMA เรียงลง")
    return " · ".join(bits[:2])


def _alignment(reads: list) -> tuple:
    """How the timeframes line up, as a sentence and a direction."""
    by_tf = {r.tf: r.trend for r in reads}

    def lean(group):
        ups = sum(1 for tf in group if by_tf.get(tf) == UP)
        downs = sum(1 for tf in group if by_tf.get(tf) == DOWN)
        return 1 if ups > downs else -1 if downs > ups else 0

    major, mid, minor = lean(MAJOR_TFS), lean(MID_TFS), lean(MINOR_TFS)
    word = {1: "ขึ้น", -1: "ลง", 0: "ออกข้าง"}
    sentence = (f"TF ใหญ่ {word[major]} · TF กลาง {word[mid]} · "
                f"TF เล็ก {word[minor]}")

    if major and major == mid == minor:
        sentence += f" — เรียงตัวไปทางเดียวกันทั้งหมด ({word[major]})"
        direction = major
    elif major and major == mid:
        sentence += " — TF ใหญ่กับ TF กลางตรงกัน TF เล็กสวนอยู่ (ปกติคือการย่อ)"
        direction = major
    elif major and mid and major != mid:
        sentence += " — TF ใหญ่กับ TF กลางขัดกัน ให้รอจนกว่าจะเลือกข้าง"
        direction = 0
    else:
        sentence += " — ยังไม่มีทิศทางที่ชัด"
        direction = major or mid
    return sentence, direction


def _long_term(reads: list, lv, price: float, digits: int) -> str:
    """The multi-week picture, stated separately from today."""
    by_tf = {r.tf: r.trend for r in reads}
    w1, d1 = by_tf.get("W1", SIDE), by_tf.get("D1", SIDE)
    if w1 == d1 == UP:
        head = "เทรนด์ระยะยาวเป็นขาขึ้น (W1 และ D1 ตรงกัน)"
    elif w1 == d1 == DOWN:
        head = "เทรนด์ระยะยาวเป็นขาลง (W1 และ D1 ตรงกัน)"
    elif w1 == UP and d1 == DOWN:
        head = "ระยะยาวยังขาขึ้น แต่ระยะกลางกำลังย่อลง"
    elif w1 == DOWN and d1 == UP:
        head = "ระยะยาวยังขาลง แต่ระยะกลางกำลังเด้งขึ้น"
    else:
        head = "ระยะยาวยังไม่เลือกข้าง เป็นการสะสมกำลังในกรอบ"

    if lv is not None and lv.above and lv.below:
        top, bottom = lv.above[-1].price, lv.below[-1].price
        span = top - bottom
        if span > 0:
            pos = (price - bottom) / span
            where = ("ค่อนไปทางบนของกรอบใหญ่" if pos >= 0.66 else
                     "ค่อนไปทางล่างของกรอบใหญ่" if pos <= 0.34 else
                     "อยู่กลางกรอบใหญ่")
            head += f" · ตอนนี้ราคา{where}"
    return head


def _range_position(df, price: float, bars: int) -> float:
    """Where price sits in the last N bars of a chart, 0 = low, 1 = high."""
    if df is None or len(df) < 3 or price <= 0:
        return -1.0
    window = df.iloc[-bars:]
    low, high = float(window["low"].min()), float(window["high"].max())
    if high <= low:
        return -1.0
    return max(0.0, min(1.0, (price - low) / (high - low)))


def _where(pos: float) -> str:
    if pos < 0:
        return ""
    if pos >= 0.8:
        return "ติดขอบบน"
    if pos >= 0.6:
        return "ค่อนไปทางบน"
    if pos <= 0.2:
        return "ติดขอบล่าง"
    if pos <= 0.4:
        return "ค่อนไปทางล่าง"
    return "กลางกรอบ"


def _rsi_word(value: float) -> str:
    if value >= 70:
        return f"RSI {value:.0f} (ซื้อมากเกิน)"
    if value <= 30:
        return f"RSI {value:.0f} (ขายมากเกิน)"
    if value >= 55:
        return f"RSI {value:.0f} (โมเมนตัมเอียงขึ้น)"
    if value <= 45:
        return f"RSI {value:.0f} (โมเมนตัมเอียงลง)"
    return f"RSI {value:.0f} (กลาง ๆ)"


def _rsi_of(df) -> float:
    if df is None or len(df) < 20:
        return -1.0
    try:
        value = float(S.rsi(df["close"]).iloc[-1])
    except Exception:                   # noqa: BLE001 - a number is not worth a crash
        return -1.0
    return value if value == value else -1.0      # NaN check


def _big_picture(reads: list, frames: dict, price: float, digits: int,
                 daily_atr: float) -> list:
    """The weekly and daily read: the tide, not the waves."""
    by_tf = {r.tf: r.trend for r in reads}
    out = []

    w1, d1 = by_tf.get("W1", SIDE), by_tf.get("D1", SIDE)
    out.append(f"W1 {_WORD.get(w1, 'ออกข้าง')} · D1 {_WORD.get(d1, 'ออกข้าง')}"
               + (" — ตรงกัน ถือเป็นทิศทางหลักได้"
                  if w1 == d1 and w1 != SIDE else
                  " — ยังไม่ตรงกัน ทิศทางหลักยังไม่นิ่ง"))

    pos_w = _range_position(frames.get("W1"), price, 12)
    if pos_w >= 0:
        out.append(f"ในกรอบ 12 สัปดาห์ ราคาอยู่{_where(pos_w)} "
                   f"({pos_w * 100:.0f}% ของกรอบ)")
    pos_d = _range_position(frames.get("D1"), price, 20)
    if pos_d >= 0:
        out.append(f"ในกรอบ 20 วัน ราคาอยู่{_where(pos_d)} "
                   f"({pos_d * 100:.0f}% ของกรอบ)")
    rsi_d = _rsi_of(frames.get("D1"))
    if rsi_d >= 0:
        out.append(f"D1 {_rsi_word(rsi_d)}")
    if daily_atr > 0:
        out.append(f"ระยะแกว่งเฉลี่ยต่อวัน {LV._fmt(daily_atr, digits)} "
                   "— ใช้ประเมินว่าเป้าที่ตั้งไว้ไกลเกินวันเดียวหรือไม่")
    return out


def _small_picture(reads: list, frames: dict, price: float, digits: int,
                   atr: float, direction: int) -> list:
    """The intraday read: which leg the market is in right now."""
    by_tf = {r.tf: r.trend for r in reads}
    out = []

    h4, h1 = by_tf.get("H4", SIDE), by_tf.get("H1", SIDE)
    m15, m5 = by_tf.get("M15", SIDE), by_tf.get("M5", SIDE)
    out.append(f"H4 {_WORD.get(h4, 'ออกข้าง')} · H1 {_WORD.get(h1, 'ออกข้าง')} · "
               f"M15 {_WORD.get(m15, 'ออกข้าง')} · M5 {_WORD.get(m5, 'ออกข้าง')}")

    # Is the fast chart pulling back against the slow one, or driving with it?
    fast = 1 if m15 == UP and m5 == UP else -1 if m15 == DOWN and m5 == DOWN else 0
    if direction and fast and fast == direction:
        out.append("TF เล็กวิ่งไปทางเดียวกับทิศทางหลัก — เป็นช่วงออกตัว "
                   "ไล่ราคาตรงนี้คือไล่ที่ปลายขา")
    elif direction and fast and fast != direction:
        out.append("TF เล็กสวนทิศทางหลักอยู่ — ลักษณะของการย่อ "
                   "เป็นจังหวะที่คนรอทิศทางหลักมักเฝ้า")
    else:
        out.append("TF เล็กยังไม่มีทิศชัด — ตลาดกำลังพักตัว")

    pos_h = _range_position(frames.get("H1"), price, 24)
    if pos_h >= 0:
        out.append(f"ในกรอบ 24 ชั่วโมง ราคาอยู่{_where(pos_h)} "
                   f"({pos_h * 100:.0f}% ของกรอบ)")
    rsi_h1 = _rsi_of(frames.get("H1"))
    if rsi_h1 >= 0:
        out.append(f"H1 {_rsi_word(rsi_h1)}")
    if atr > 0:
        out.append(f"ระยะแกว่งเฉลี่ยต่อชั่วโมง {LV._fmt(atr, digits)}")
    return out


def _paths(direction: int, lv, digits: int, alignment_dir: int) -> tuple:
    """The road the read favours, and the road it does not - both named.

    A one-sided call is not analysis. The primary path is the one the
    structure currently favours; the alternate is what the same chart
    would have to do to turn that on its head, with the price that says
    it has happened.
    """
    def road(sign):
        # The gate is the first level in the way; the targets are what
        # opens up past it. Naming the gate as its own first target read
        # as "break 3,359 to reach 3,359", which says nothing.
        gate = (lv.r(1) if sign > 0 else lv.s(1)) if lv else None
        legs = [x for x in ((lv.r(2), lv.r(3)) if sign > 0
                            else (lv.s(2), lv.s(3))) if lv and x is not None]
        verb = "เปิดทางขึ้นไปที่" if sign > 0 else "เปิดทางลงไปที่"
        if gate is None:
            return "ยังไม่มีระดับถัดไปให้อ้างอิง"
        pass_word = "ยืนเหนือ" if sign > 0 else "หลุด"
        head = f"{pass_word} {LV._fmt(gate.price, digits)} ได้"
        if not legs:
            return head + " → พ้นแนวสุดท้ายที่มองเห็น ต้องใช้ระยะแกว่งวัดเป้าเอา"
        return (head + " → " + verb + " "
                + " แล้ว ".join(LV._fmt(x.price, digits) for x in legs))

    if direction > 0:
        return f"ขาขึ้น — {road(1)}", f"ขาลง — {road(-1)}"
    if direction < 0:
        return f"ขาลง — {road(-1)}", f"ขาขึ้น — {road(1)}"
    # No lean: neither road is the alternate, so both are stated as equals.
    return f"ยังไม่เลือกข้าง · ขึ้น: {road(1)}", f"ลง: {road(-1)}"


def _invalidation(direction: int, lv, digits: int) -> str:
    """The one price that would prove this read wrong."""
    if direction > 0 and lv is not None and lv.s(1) is not None:
        return (f"มุมมองขาขึ้นเสียถ้าหลุด {LV._fmt(lv.s(1).price, digits)} "
                f"และปิดต่ำกว่าได้")
    if direction < 0 and lv is not None and lv.r(1) is not None:
        return (f"มุมมองขาลงเสียถ้ายืนเหนือ {LV._fmt(lv.r(1).price, digits)} "
                f"และปิดสูงกว่าได้")
    if lv is not None and lv.r(1) is not None and lv.s(1) is not None:
        return (f"ยังไม่มีทิศทาง จนกว่าจะหลุด {LV._fmt(lv.s(1).price, digits)} "
                f"หรือยืนเหนือ {LV._fmt(lv.r(1).price, digits)}")
    return ""


def _techniques(rep) -> tuple:
    """Which techniques back the leaning side, and which argue with it."""
    con = getattr(rep, "consensus", None)
    if con is None:
        return [], []
    for_ = [v.label for v in con.votes if v.supports]
    against = [v.label for v in con.votes if v.opposes]
    return for_, against


def _events_for(symbol: str, news_ctx, now: datetime) -> tuple:
    """Today's releases that can move this pair, and what to say if none.

    Never invented: with no verified calendar the note says exactly that,
    and no news reasoning is used anywhere in the message.
    """
    if news_ctx is None or not getattr(news_ctx, "available", False):
        detail = ""
        if news_ctx is not None and getattr(news_ctx, "error", ""):
            detail = f" ({news_ctx.error[:60]})"
        return [], f"ยืนยันปฏิทินข่าวรอบนี้ไม่ได้{detail} — ไม่นำข่าวมาใช้เป็นเหตุผล"

    wanted = NEWS.currencies_for([symbol])
    out = [e for e in getattr(news_ctx, "day_events", [])
           if e.currency in wanted]
    out.sort(key=lambda e: e.when)
    ahead = [e for e in out if e.when >= now]
    if not out:
        return [], "วันนี้ไม่มีข่าวในปฏิทินที่กระทบคู่นี้"
    if not ahead:
        return out[-3:], "ข่าวของวันนี้ออกครบแล้ว"
    return ahead[:5], ""


def build(rep, frames: dict, news_ctx, now: datetime,
          swing_bars: int = 3) -> Outlook:
    """Assemble the whole picture for one instrument.

    `rep` is the daily report for the symbol: it already carries the
    structure read, the regime, the ballot and the scores, so this adds
    the parts a trend-and-levels analysis needs on top of it rather than
    computing any of it a second time.
    """
    symbol = rep.symbol
    digits = DIGITS_FOR(symbol)
    out = Outlook(symbol=symbol, price=rep.price, digits=digits,
                  quote_tf=rep.quote_tf, price_age_min=rep.price_age_min,
                  regime=rep.regime, regime_confidence=rep.regime_confidence,
                  volatility=rep.volatility, atr=rep.atr,
                  buy_score=rep.buy_score, sell_score=rep.sell_score)
    if rep.error:
        out.error = rep.error
        return out

    structures = getattr(rep, "structures", {}) or {}
    for tf in TF_LADDER:
        if tf not in frames and tf not in structures:
            continue
        st = structures.get(tf)
        out.reads.append(TFRead(tf=tf, trend=_trend_of(st),
                                note=_note_for(st, frames.get(tf))))

    out.alignment, out.direction = _alignment(out.reads)

    d1 = frames.get("D1")
    out.daily_atr = S.atr(d1, 14) if d1 is not None and len(d1) > 20 else 0.0

    # How far out a level still matters: a few days of range, not a few
    # hours of it. Sizing this off the hourly ATR alone hid every weekly
    # level on a quiet chart and left the reader with nothing above price.
    reach = max(rep.atr * 10.0, out.daily_atr * 3.0)
    out.level_map = LV.collect(symbol, frames, rep.price, rep.atr,
                               sm=getattr(rep, "smart_money", None),
                               swing_bars=swing_bars, digits=digits,
                               reach=reach, day_atr=out.daily_atr)
    LV.project(out.level_map, out.daily_atr, digits)
    out.scenarios = LV.scenarios(out.level_map, out.direction,
                                 confirm_tf="H1", digits=digits)
    out.range_note = LV.expected_range(out.level_map, out.daily_atr, digits)
    out.long_term = _long_term(out.reads, out.level_map, rep.price, digits)
    out.big_picture = _big_picture(out.reads, frames, rep.price, digits,
                                   out.daily_atr)
    out.small_picture = _small_picture(out.reads, frames, rep.price, digits,
                                       rep.atr, out.direction)
    out.primary_path, out.alternate_path = _paths(
        out.direction, out.level_map, digits, out.direction)
    out.invalidation = _invalidation(out.direction, out.level_map, digits)
    out.techniques_for, out.techniques_against = _techniques(rep)
    out.verdict = getattr(rep, "now_verdict", "WAIT") or "WAIT"
    out.verdict_why = getattr(rep, "now_why", "")
    out.events, out.news_note = _events_for(symbol, news_ctx, now)
    return out
