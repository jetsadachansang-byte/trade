"""Dynamic Exit Engine - targets are computed, never assumed.

A fixed take-profit ladder is a bet that every market pays the same. It
does not: a 0.5R first target needs a 67% strike rate just to break even,
which is how the old scalp profiles were quietly negative-expectancy.

So nothing here is fixed. Several exit plans are built from what the chart
is actually doing - regime, trend efficiency, ATR, where the liquidity
sits, participation, session, momentum, and the news calendar - each is
priced, and the one with the highest expected value wins. If none of them
clears zero, the setup is not worth taking and no signal is sent.

The probability model is the part that makes the choice honest. Reaching
+kR before -1R is the classic two-barrier problem: with no drift the
answer is 1/(1+k), and expected value then works out to the same number
whatever target is chosen - which would make "pick the best exit"
meaningless. What actually separates a near target from a far one is
drift, so the model uses the drifted form. A market that travels earns a
positive drift and rewards distance; a market that oscillates does not,
and the engine picks a near target for it on its own.

Order flow appears in the brief but no free feed carries it; volume here
is tick volume on FX, which is participation, not delivered size. Both
limits are stated on the ticket rather than papered over.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import regime as R

# Plans are described by their first target so the message can say what
# kind of exit it is without leaking implementation detail.
FIXED, TRAILING = "fixed", "trailing"

# No target may sit closer than this in R - anything tighter cannot pay
# for the spread it crosses on the way in and out.
MIN_TARGET_R = 0.8
# Nor further than this, which is beyond what any of these styles holds for.
MAX_TARGET_R = 8.0


@dataclass
class ExitPlan:
    """One way to get out, priced end to end."""
    mode: str = FIXED
    tp_r: tuple = (1.0, 2.0, 3.0)
    trail_atr: float = 0.0          # trail distance in ATR, 0 = no trailing
    label: str = ""
    # --- the numbers the brief asks for -----------------------------
    win_probability: float = 0.0    # probability of reaching TP1
    prob_tp: tuple = (0.0, 0.0, 0.0)
    prob_sl: float = 0.0
    expected_rr: float = 0.0
    expected_value: float = 0.0
    expected_drawdown: float = 1.0  # in R; a full stop, by construction
    edge: float = 0.0
    reasons: list = field(default_factory=list)

    @property
    def positive(self) -> bool:
        return self.expected_value > 0


def _hit_probability(k: float, lam: float) -> float:
    """Chance of reaching +kR before -1R, for a walk with drift `lam`.

    Brownian motion with drift, barriers at +k and -1:

        P = (1 - e^(lam)) / (e^(-lam*k) - e^(lam))

    At lam = 0 this collapses to 1/(1+k), the fair coin. Positive drift
    lifts it, and lifts it more at distance - which is precisely why a
    trending market can afford a target a range-bound one cannot.
    """
    k = max(0.1, k)
    if abs(lam) < 1e-6:
        return 1.0 / (1.0 + k)
    try:
        num = 1.0 - math.exp(lam)
        den = math.exp(-lam * k) - math.exp(lam)
        if abs(den) < 1e-12:
            return 1.0 / (1.0 + k)
        return max(0.02, min(0.95, num / den))
    except OverflowError:
        return 0.95 if lam > 0 else 0.02


def _persistence(reg, strength: float) -> float:
    """How much of the edge survives into the drift term."""
    if reg is None:
        return 0.6
    if reg.is_trending:
        return 0.7 + 1.1 * max(0.0, min(1.0, strength))
    if reg.is_ranging:
        return 0.35
    return 0.6


def _drift_at(edge: float, reg, strength: float, k: float,
              horizon: float) -> float:
    """Drift available out at distance `k`.

    Constant drift would make every extra R worth having, and the engine
    would simply always pick the furthest target allowed. It does not work
    that way: the edge belongs to the setup, and the setup ages. Distance
    costs time, so drift decays over it, and expected value therefore
    peaks at a finite target instead of running away.

    `horizon` is how far this style and regime can realistically carry a
    move - short for a one-minute scalp in a range, long for a daily
    position in a strong trend.
    """
    lam0 = edge * _persistence(reg, strength)
    return lam0 * math.exp(-max(0.0, k) / max(0.5, horizon))


def _edge(score: float, threshold: float, reg, strength: float,
          macro_agrees: int, has_liquidity_target: bool,
          volume_ratio: float, news_clear: bool, recall=None) -> tuple:
    """How much better than a coin flip this setup actually is.

    Every term is something measured. The total is capped at +0.55, which
    on a 1R target means roughly a 77% strike rate - already optimistic
    for any discretionary system, and deliberately hard to reach.
    """
    reasons = []
    edge = 0.0

    span = max(1.0, 100.0 - threshold)
    above = max(0.0, min(1.0, (score - threshold) / span))
    edge += above * 0.22
    if above > 0.4:
        reasons.append(f"คะแนนสูงกว่าเกณฑ์มาก ({score:.0f})")

    if reg is not None:
        conf = reg.confidence / 100.0
        if reg.is_trending and strength > 0.35:
            edge += 0.16 * conf
            reasons.append(f"เทรนด์ชัด ({reg.name}) เดินทางจริง {strength:.0%}")
        elif reg.is_ranging:
            edge += 0.06 * conf
            reasons.append(f"ตลาดอยู่ในกรอบ ({reg.name}) — เป้าต้องสั้นลง")
        else:
            edge += 0.08 * conf

    if macro_agrees > 0:
        edge += 0.07
        reasons.append("ภาพมหภาคหนุนทิศทางนี้")
    elif macro_agrees < 0:
        edge -= 0.12
        reasons.append("⚠️ ภาพมหภาคสวนทิศทางนี้")

    if has_liquidity_target:
        edge += 0.08
        reasons.append("มีกองสภาพคล่องรออยู่ฝั่งกำไร — ราคามักถูกดูดไปหา")

    if volume_ratio >= 1.3:
        edge += 0.05
        reasons.append(f"มีแรงร่วมหนุน (volume {volume_ratio:.1f}x ปกติ)")
    elif volume_ratio < 0.7:
        edge -= 0.05
        reasons.append(f"แรงร่วมเบาบาง (volume {volume_ratio:.1f}x ปกติ)")

    if not news_clear:
        edge -= 0.10
        reasons.append("⚠️ ใกล้ข่าวแรง — ลดความน่าเชื่อถือของเป้าไกล")

    if recall is not None and recall.enough:
        edge += max(-0.12, min(0.12, (recall.win_rate - 50.0) / 100.0))
        reasons.append(f"สถิติจริง {recall.samples} ไม้ ชนะ {recall.win_rate:.0f}%")

    return max(-0.35, min(0.55, edge)), reasons


def _session_factor(session: str) -> float:
    """How far price typically travels in this session."""
    return {"Overlap": 1.20, "London": 1.10, "NewYork": 1.05,
            "Asian": 0.75}.get(session, 0.85)


def _regime_factor(reg) -> float:
    """Strong trends pay for distance. Ranges do not."""
    if reg is None:
        return 1.0
    return {R.STRONG_BULL: 1.45, R.STRONG_BEAR: 1.45,
            R.EXPANSION: 1.35, R.BREAKOUT_TRUE: 1.30,
            R.WEAK_BULL: 1.05, R.WEAK_BEAR: 1.05,
            R.LIQUIDITY_HUNT: 1.00,
            R.RANGE: 0.70, R.MEAN_REVERSION: 0.65,
            R.COMPRESSION: 0.75, R.BREAKOUT_FALSE: 0.65,
            R.TREND_EXHAUSTION: 0.70, R.NEWS_DRIVEN: 0.80}.get(reg.name, 1.0)


def _price_plan(ladder: tuple, mode: str, label: str, trail_atr: float,
                edge: float, reg, strength: float, horizon: float,
                reasons: list) -> ExitPlan:
    """Price one ladder end to end: probabilities, EV, expected drawdown."""
    k1, k2, k3 = ladder

    def hit(k: float) -> float:
        return _hit_probability(k, _drift_at(edge, reg, strength, k, horizon))

    p1, p2, p3 = hit(k1), hit(k2), hit(k3)
    plan = ExitPlan(mode=mode, tp_r=(round(k1, 2), round(k2, 2), round(k3, 2)),
                    trail_atr=round(trail_atr, 2), label=label,
                    win_probability=round(p1 * 100, 1),
                    prob_tp=(round(p1 * 100, 1), round(p2 * 100, 1),
                             round(p3 * 100, 1)),
                    prob_sl=round((1 - p1) * 100, 1), edge=round(edge, 3),
                    reasons=list(reasons))

    if mode == TRAILING:
        # A third is banked at TP1 and the stop then trails the rest.
        # Expected travel comes from the same model - the integral of the
        # probability of still being alive at each distance - so the
        # trailing plan is priced on the same footing as the fixed ones,
        # minus the distance the trail itself gives back.
        # Expected distance travelled beyond TP1, given TP1 was reached:
        # the survival integral from k1 out to the style's horizon,
        # normalised by the probability of getting there at all.
        step, total, x = 0.25, 0.0, k1
        limit = k1 + horizon * 2.0
        while x <= limit:
            total += hit(x) * step
            x += step
        beyond = total / max(p1, 0.05)
        captured = max(0.0, beyond - trail_atr)
        plan.expected_rr = round((k1 + 2 * captured) / 3.0, 2)
        plan.expected_value = round(
            p1 * (k1 + 2 * captured) / 3.0 - (1 - p1) * 1.0, 3)
    else:
        plan.expected_rr = round((k1 + k2 + k3) / 3.0, 2)
        # Stop moves to break-even once TP1 is banked, so runners that
        # stall return nothing rather than losing; only a trade that never
        # reaches TP1 gives back the full 1R.
        plan.expected_value = round(
            (p1 * k1 + p2 * k2 + p3 * k3) / 3.0 - (1 - p1) * 1.0, 3)

    plan.expected_drawdown = round((1 - p1) * 1.0, 3)
    return plan


def build(score: float, threshold: float, reg, strength: float, atr: float,
          sl_distance: float, liquidity_distance: float = 0.0,
          volume_ratio: float = 1.0, session: str = "",
          macro_agrees: int = 0, news_clear: bool = True,
          momentum: float = 0.0, recall=None,
          style_cap: float = 6.0) -> ExitPlan:
    """Search for the exit worth the most, and return it.

    Rather than choosing between a few hand-written ladders, this sweeps
    the target distance and lets expected value pick the shape. What comes
    back in a strong trend is genuinely different from what comes back in
    a range, because the drift that survives to each distance is.
    """
    has_pool = liquidity_distance > 0 and sl_distance > 0
    edge, reasons = _edge(score, threshold, reg, strength, macro_agrees,
                          has_pool, volume_ratio, news_clear, recall)

    # How far this style and this market can realistically carry a move.
    # The style sets the ceiling: a one-minute scalp does not get a swing
    # target just because the trend happens to be strong.
    horizon = style_cap * _regime_factor(reg) * _session_factor(session)
    horizon = max(0.8, min(style_cap * 1.6, horizon))
    cap = max(1.5, min(MAX_TARGET_R, horizon * 1.3))

    # The first target has to be worth taking. Banking a third of the
    # position far below what this market routinely travels just churns
    # the position for spread, and the expected-value maths on its own
    # would always choose the tightest target allowed - because moving the
    # stop to break-even afterwards looks free to it, and is not.
    min_k1 = max(MIN_TARGET_R, min(2.5, 0.35 * horizon))

    candidates = []

    # --- sweep fixed ladders -------------------------------------------
    # spacing between partial exits, as a ratio - tight ladders bank
    # sooner, wide ones let the runner breathe
    for spacing in (1.5, 1.8, 2.2):
        k1 = min_k1
        while k1 <= cap:
            # every target stays inside what this style can hold - a
            # third target at twice the horizon is a number, not a plan
            k2 = min(cap, k1 * spacing)
            k3 = min(cap, k1 * spacing * spacing)
            candidates.append(_price_plan(
                (k1, k2, k3), FIXED, "", 0.0, edge, reg, strength, horizon,
                reasons))
            k1 += 0.2

    # --- aim at the pool price is being drawn toward --------------------
    if has_pool:
        k = max(min_k1, min(cap, liquidity_distance / sl_distance))
        candidates.append(_price_plan(
            (max(min_k1, k * 0.6), k, min(cap, k * 1.7)), FIXED, f"เป้าที่กองสภาพคล่อง {k:.1f}R", 0.0,
            edge, reg, strength, horizon,
            reasons + ["วางเป้าตรงกองสภาพคล่องที่ราคากำลังถูกดูดไปหา"]))

    # --- trail instead of capping, when momentum earns it ---------------
    if momentum >= 0.5 and atr > 0 and sl_distance > 0:
        trail_r = (atr * 1.5) / sl_distance
        for spacing in (1.8, 2.2):
            k1 = min_k1
            while k1 <= cap:
                candidates.append(_price_plan(
                    (k1, min(cap, k1 * spacing),
                     min(cap, k1 * spacing * spacing)), TRAILING,
                    "ปิดบางส่วน + ลาก SL ตาม", trail_r, edge, reg, strength,
                    horizon,
                    reasons + [f"โมเมนตัมสูง ({momentum:.0%}) — ลาก SL แทนเป้าตายตัว "
                               f"จะได้ไม่ตัดกำไรตอนตลาดยังวิ่ง"]))
                k1 += 0.4

    best = max(candidates, key=lambda p: p.expected_value)
    if not best.label:
        shape = ("เป้าไกล" if best.tp_r[0] >= 2.0 else
                 "เป้ากลาง" if best.tp_r[0] >= 1.3 else "เป้าใกล้")
        best.label = f"{shape} {best.tp_r[0]}/{best.tp_r[1]}/{best.tp_r[2]}R"

    # say what else was considered, so the choice is auditable
    fixed_best = max((p for p in candidates if p.mode == FIXED),
                     key=lambda p: p.expected_value, default=None)
    trail_best = max((p for p in candidates if p.mode == TRAILING),
                     key=lambda p: p.expected_value, default=None)
    if trail_best and fixed_best:
        loser = trail_best if best.mode == FIXED else fixed_best
        best.reasons.append(
            f"เลือกแผนนี้เพราะ EV สูงสุด ({best.expected_value:+.2f}R) "
            f"เทียบกับอีกแบบ ({loser.expected_value:+.2f}R)")
    else:
        best.reasons.append(
            f"เลือกจากการไล่คำนวณทุกระยะเป้า — EV สูงสุด {best.expected_value:+.2f}R")
    return best
