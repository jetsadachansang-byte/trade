"""LEVEL 6 and 10 - probability, expected value, and the final gate.

Two things are kept strictly apart here. A *modelled* win probability is
derived from the setup's own score and context; an *empirical* one comes
from closed trades in the same regime. The message always says which it
is, because presenting a model output as a measured rate would be the
single most misleading thing this system could do.

LEVEL 10 is the last word: expected value has to be positive and the
structural picture has to hold, or the signal is not sent.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Odds:
    """The numbers a desk would want before approving the risk."""
    win_probability: float = 0.0    # 0-100
    source: str = "model"           # "model" or "history"
    expected_rr: float = 0.0
    expected_value: float = 0.0     # in R, per unit risked
    prob_tp1: float = 0.0
    prob_sl: float = 0.0
    max_drawdown_r: float = 1.0     # one full stop, by construction
    notes: list = field(default_factory=list)

    @property
    def positive(self) -> bool:
        return self.expected_value > 0


def _model_win_rate(score: float, threshold: float) -> float:
    """Turn a confidence score into a win probability, conservatively.

    Anchored so a setup sitting exactly on the bar is around break-even
    for a 1:2 payoff, rising into the low seventies at the top of the
    range. It is a model, not a measurement, and every message that
    carries it says so.
    """
    span = max(1.0, 100.0 - threshold)
    above = max(0.0, min(1.0, (score - threshold) / span))
    return round(48.0 + above * 24.0, 1)


def assess(score: float, threshold: float, tp_r: tuple,
           recall=None, macro_agrees: int = 0, news_clear: bool = True) -> Odds:
    """Work out whether this trade is worth taking at all."""
    odds = Odds()

    win = _model_win_rate(score, threshold)
    odds.source = "model"
    if recall is not None and recall.enough:
        # blend the measured rate in once there is genuinely enough of it
        win = round(win * 0.4 + recall.win_rate * 0.6, 1)
        odds.source = "history"
        odds.notes.append(f"อิงสถิติจริง {recall.samples} ไม้ในสภาพตลาดนี้")
    else:
        odds.notes.append("เป็นค่าประเมินจากคะแนน ไม่ใช่สถิติที่วัดได้จริง")

    if macro_agrees > 0:
        win += 3.0
        odds.notes.append("ภาพมหภาคสนับสนุนทิศทางนี้")
    elif macro_agrees < 0:
        win -= 6.0
        odds.notes.append("⚠️ ภาพมหภาคสวนทิศทางนี้")
    if not news_clear:
        win -= 4.0
        odds.notes.append("⚠️ ใกล้ช่วงข่าวแรง")

    odds.win_probability = round(max(5.0, min(90.0, win)), 1)
    odds.prob_tp1 = odds.win_probability
    odds.prob_sl = round(100.0 - odds.win_probability, 1)

    # A third of the position comes off at each target. Once TP1 is banked
    # the stop moves to break-even, so a runner that stalls returns 0 R
    # rather than a loss - but a trade that never reaches TP1 loses the
    # full 1 R. Reaching each further target is progressively less likely.
    p = odds.win_probability / 100.0
    avg_win = (tp_r[0] + tp_r[1] * 0.55 + tp_r[2] * 0.30) / 3.0
    odds.expected_rr = round(avg_win, 2)
    odds.expected_value = round(p * avg_win - (1 - p) * 1.0, 3)
    if avg_win < 0.7:
        odds.notes.append(
            f"⚠️ เป้า TP ของสไตล์นี้ให้ผลตอบแทนเฉลี่ยเพียง {avg_win:.2f}R "
            f"ต่อความเสี่ยง 1R — ต้องชนะเกิน {1/(1+avg_win)*100:.0f}% จึงจะคุ้ม")
    return odds


def approve(odds: Odds, reg, steps_passed: int, total_steps: int,
            news_blocking: bool, min_regime_confidence: float = 15.0) -> tuple:
    """LEVEL 10 - would a portfolio manager sign this off?

    Returns (approved, reason). A rejection here is a good outcome: the
    brief is explicit that capital matters more than taking a trade.
    """
    if news_blocking:
        return False, "อยู่ในช่วงข่าวแรง — ไม่อนุมัติ"
    if steps_passed < total_steps:
        return False, f"ผ่านโครงสร้างเพียง {steps_passed}/{total_steps} ขั้น"
    if not odds.positive:
        return False, (f"Expected Value {odds.expected_value:+.2f}R ไม่เป็นบวก — "
                       f"เข้าไปก็ขาดทุนในระยะยาว")
    if odds.win_probability < 35:
        return False, f"โอกาสชนะ {odds.win_probability:.0f}% ต่ำเกินรับได้"
    if reg is not None and reg.confidence < min_regime_confidence:
        return False, (f"ระบุสภาพตลาดได้ไม่ชัด (มั่นใจ {reg.confidence:.0f}%) — "
                       f"ไม่รู้ว่ากำลังเทรดอะไรอยู่")
    return True, (f"EV {odds.expected_value:+.2f}R · "
                  f"โอกาสชนะ {odds.win_probability:.0f}% · "
                  f"สภาพตลาด {reg.name if reg else '-'}")
