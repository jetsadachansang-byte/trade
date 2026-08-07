"""Key support and resistance, collected from every timeframe at once.

A level nobody else is looking at is not a level. So nothing here is
invented from an indicator: every price in the output is somewhere the
market has already turned, stopped, or been rejected - a swing point, a
prior session's extreme, an order block, or a round number the whole
market can see. What makes one level stronger than another is simply how
many of those independent reasons land on the same price.

Levels within a small distance of each other are the same level. Three
"different" resistances a tenth of an ATR apart is one wall, and reporting
it as three is how a reader ends up watching the wrong price. They are
merged, and the merge is what produces the strength score.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import smc as S

# How far apart two prices can be and still be the same level, as a
# fraction of the working ATR.
CLUSTER_ATR = 0.45

# Weight per source. A weekly swing is a wall; an hourly swing is a speed
# bump. These are the only opinions in this module and they are stated
# here rather than buried in the collection code.
WEIGHT = {"W1": 3.0, "D1": 3.0, "H4": 2.0, "H1": 1.0}

# Round numbers the whole market watches, per instrument type.
def round_step(symbol: str) -> float:
    if symbol == "XAUUSD":
        return 25.0
    if "JPY" in symbol:
        return 0.50
    return 0.0050


@dataclass
class Level:
    """One price the market has respected, and every reason it matters."""
    price: float
    labels: list = field(default_factory=list)
    strength: float = 0.0
    # A measured move rather than a place price has actually turned. Kept
    # visibly separate: a trader who cannot tell a real level from a
    # projected one will defend the wrong price.
    projected: bool = False

    @property
    def stars(self) -> str:
        """Strength as something readable at a glance."""
        if self.projected:
            return "◻"
        if self.strength >= 6:
            return "★★★"
        if self.strength >= 3.5:
            return "★★"
        return "★"

    @property
    def why(self) -> str:
        return " + ".join(self.labels[:3])


@dataclass
class LevelMap:
    """The levels above price and the levels below it."""
    above: list = field(default_factory=list)   # nearest first
    below: list = field(default_factory=list)   # nearest first
    price: float = 0.0
    atr: float = 0.0

    def r(self, n: int):
        """Resistance n (1-based), or None when there is no such level."""
        return self.above[n - 1] if len(self.above) >= n else None

    def s(self, n: int):
        return self.below[n - 1] if len(self.below) >= n else None


def _swing_prices(df, bars: int, keep: int = 4) -> tuple:
    """The last few confirmed swing highs and lows on one chart."""
    if df is None or len(df) < bars * 2 + 12:
        return [], []
    highs, lows = S._swing_points(df, bars)
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    return ([float(h[i]) for i in highs[-keep:]],
            [float(l[i]) for i in lows[-keep:]])


def _session_extremes(df, label_now: str, label_prev: str) -> list:
    """(price, label) for the current and previous bar of a slow chart.

    Yesterday's high and last week's high are levels in their own right -
    every desk marks them - and they are not always swing points.
    """
    out = []
    if df is None or len(df) < 2:
        return out
    for idx, label in ((-1, label_now), (-2, label_prev)):
        try:
            row = df.iloc[idx]
        except IndexError:
            continue
        out.append((float(row["high"]), f"High {label}"))
        out.append((float(row["low"]), f"Low {label}"))
    return out


def _round_numbers(price: float, step: float, reach: float) -> list:
    """Round numbers within reach of price, above and below."""
    if step <= 0:
        return []
    out = []
    base = round(price / step)
    for k in range(-3, 4):
        level = (base + k) * step
        if abs(level - price) <= reach and level > 0:
            out.append((level, "เลขกลม"))
    return out


def collect(symbol: str, frames: dict, price: float, atr: float,
            sm=None, swing_bars: int = 3, digits: int = 5,
            reach: float = 0.0) -> LevelMap:
    """Every level worth watching around the current price.

    `atr` is the working ATR (the hourly one) and sets the clustering
    distance. `reach` is how far out a level is still worth reporting;
    it defaults to a multiple of the hourly ATR, but the caller should
    pass something derived from the daily range - an hourly ATR on a
    quiet chart is small enough to hide every level on the weekly.
    """
    out = LevelMap(price=price, atr=atr)
    if price <= 0 or atr <= 0:
        return out

    raw: list = []       # (price, label, weight)

    for tf, weight in WEIGHT.items():
        highs, lows = _swing_prices(frames.get(tf), swing_bars)
        for value in highs:
            raw.append((value, f"จุดสูง {tf}", weight))
        for value in lows:
            raw.append((value, f"จุดต่ำ {tf}", weight))

    for value, label in _session_extremes(frames.get("D1"), "วันนี้", "เมื่อวาน"):
        raw.append((value, label, 2.5))
    for value, label in _session_extremes(frames.get("W1"), "สัปดาห์นี้",
                                          "สัปดาห์ก่อน"):
        raw.append((value, label, 3.0))

    # Order blocks are where price left from, which is where it tends to
    # be defended when it comes back.
    if sm is not None:
        for ob, name in ((getattr(sm, "ob_bull", None), "Order Block ฝั่งซื้อ"),
                         (getattr(sm, "ob_bear", None), "Order Block ฝั่งขาย")):
            if ob is not None and getattr(ob, "valid", False):
                raw.append((float(ob.top), name, 2.0))
                raw.append((float(ob.bottom), name, 2.0))

    reach = reach if reach > 0 else atr * 12.0
    for value, label in _round_numbers(price, round_step(symbol), reach):
        raw.append((value, label, 1.0))

    # Only what is close enough to matter before the picture changes.
    raw = [item for item in raw if 0 < item[0] and abs(item[0] - price) <= reach]
    if not raw:
        return out

    merged = _cluster(raw, atr * CLUSTER_ATR, digits)
    out.above = sorted([lv for lv in merged if lv.price > price],
                       key=lambda lv: lv.price)[:4]
    out.below = sorted([lv for lv in merged if lv.price < price],
                       key=lambda lv: -lv.price)[:4]
    return out


def _cluster(raw: list, tolerance: float, digits: int) -> list:
    """Merge prices that are close enough to be the same level.

    The merged price is the weighted average of its members, so a cluster
    carrying a weekly swing sits on the weekly swing rather than halfway
    between it and an hourly wick.
    """
    groups: list = []
    for value, label, weight in sorted(raw, key=lambda item: item[0]):
        if groups and value - groups[-1][-1][0] <= tolerance:
            groups[-1].append((value, label, weight))
        else:
            groups.append([(value, label, weight)])

    out = []
    for group in groups:
        total = sum(w for _, _, w in group) or 1.0
        price = sum(v * w for v, _, w in group) / total
        labels: list = []
        for _, label, _w in sorted(group, key=lambda item: -item[2]):
            if label not in labels:
                labels.append(label)
        out.append(Level(price=round(float(price), digits), labels=labels,
                         strength=round(float(total), 1)))
    return out


def project(lv: LevelMap, daily_atr: float, digits: int = 5,
            want: int = 2) -> LevelMap:
    """Fill an empty side with measured moves, clearly labelled as such.

    Price at a new high has no resistance above it - that is a fact about
    the chart, not a gap in the data. But "there is nothing above" is not
    something a reader can plan around, so the daily range is projected
    forward to give the move somewhere to be measured against. These are
    never presented as levels the market has defended.
    """
    if daily_atr <= 0 or lv.price <= 0:
        return lv
    label = "เป้าจากระยะแกว่งต่อวัน (ไม่ใช่แนวต้านเดิม)"
    label_dn = "เป้าจากระยะแกว่งต่อวัน (ไม่ใช่แนวรับเดิม)"
    gap = daily_atr * 0.5

    for side, sign, text in (("above", 1, label), ("below", -1, label_dn)):
        have = list(getattr(lv, side))
        step = 1
        while len(have) < want and step <= 4:
            price = lv.price + sign * daily_atr * (0.5 + 0.5 * step)
            step += 1
            if any(abs(price - x.price) < gap for x in have):
                continue
            have.append(Level(price=round(float(price), digits),
                              labels=[text], strength=0.0, projected=True))
        have.sort(key=lambda x: x.price * (1 if sign > 0 else -1))
        setattr(lv, side, have[:4])
    return lv


# ----------------------------------------------------------------------
# Point-by-point: what each level does if price actually gets there
# ----------------------------------------------------------------------
@dataclass
class Scenario:
    """One branch of the road: the trigger, what it means, where it goes."""
    icon: str
    trigger: str
    outcome: str
    targets: list = field(default_factory=list)
    likely: bool = False           # the branch the trend currently favours


def _fmt(value: float, digits: int) -> str:
    return f"{value:,.{digits}f}"


def scenarios(lv: LevelMap, bias: int, confirm_tf: str = "H1",
              digits: int = 5) -> list:
    """Both roads out of here, in the order they would actually happen.

    Written as "if price reaches X and does Y, then Z" rather than a
    forecast, because which way it breaks is not knowable in advance -
    what is knowable is where the decision gets made and what each answer
    opens up.
    """
    out: list = []
    r1, r2, r3 = lv.r(1), lv.r(2), lv.r(3)
    s1, s2, s3 = lv.s(1), lv.s(2), lv.s(3)

    if r1 is not None:
        targets = [x for x in (r2, r3) if x is not None]
        out.append(Scenario(
            icon="🟢",
            trigger=f"ยืนเหนือ {_fmt(r1.price, digits)} ได้ (ปิดแท่ง {confirm_tf})",
            outcome=("เปิดทางขึ้นต่อ ไปที่ "
                     + " แล้ว ".join(_fmt(t.price, digits) for t in targets)
                     if targets else "เปิดทางขึ้นต่อ เหนือนี้ยังไม่มีแนวต้านใกล้"),
            targets=[t.price for t in targets],
            likely=bias > 0))
        out.append(Scenario(
            icon="🔻",
            trigger=f"ขึ้นไปชน {_fmt(r1.price, digits)} แล้วเด้งลง (ไม่ผ่าน)",
            outcome=("กลับลงมาทดสอบ " + _fmt(s1.price, digits)
                     if s1 is not None else "กลับลงมาในกรอบเดิม"),
            targets=[s1.price] if s1 is not None else [],
            likely=bias < 0))

    if s1 is not None:
        targets = [x for x in (s2, s3) if x is not None]
        out.append(Scenario(
            icon="🔴",
            trigger=f"หลุด {_fmt(s1.price, digits)} ลงไป (ปิดแท่ง {confirm_tf})",
            outcome=("เปิดทางลงต่อ ไปที่ "
                     + " แล้ว ".join(_fmt(t.price, digits) for t in targets)
                     if targets else "เปิดทางลงต่อ ใต้นี้ยังไม่มีแนวรับใกล้"),
            targets=[t.price for t in targets],
            likely=bias < 0))
        out.append(Scenario(
            icon="🟩",
            trigger=f"ลงมาทดสอบ {_fmt(s1.price, digits)} แล้วรับอยู่",
            outcome=("ดีดกลับขึ้นไปหา " + _fmt(r1.price, digits)
                     if r1 is not None else "ดีดกลับขึ้นในกรอบเดิม"),
            targets=[r1.price] if r1 is not None else [],
            likely=bias > 0))
    return out


def expected_range(lv: LevelMap, daily_atr: float, digits: int = 5) -> str:
    """The band the day is most likely to spend its time inside."""
    if lv.price <= 0:
        return ""
    low = lv.s(1).price if lv.s(1) is not None else lv.price - daily_atr / 2
    high = lv.r(1).price if lv.r(1) is not None else lv.price + daily_atr / 2
    band = f"{_fmt(low, digits)} – {_fmt(high, digits)}"
    if daily_atr > 0:
        return f"{band} (ระยะแกว่งเฉลี่ยต่อวัน ~{_fmt(daily_atr, digits)})"
    return band
