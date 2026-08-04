"""LEVEL 3 and 7 - market memory and self-learning.

Every closed signal carries the regime and strategy it was taken under, so
the record can answer "this kind of market, this kind of setup - how did it
actually go" from its own history rather than from assertion.

The honesty rule matters more here than anywhere: with a handful of trades
a win rate is noise. Below MIN_SAMPLE this module says so and returns no
adjustment, because a confident number from four trades is worse than no
number at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# below this many closed trades, any rate is noise and is reported as such
MIN_SAMPLE = 20
# how far the learned multiplier may move a category's weight
MAX_ADJUST = 0.25


@dataclass
class Recall:
    """What history says about a regime, if it says anything at all."""
    regime: str = ""
    samples: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_rr: float = 0.0
    worst_streak: int = 0
    enough: bool = False
    note: str = ""
    strategies: dict = field(default_factory=dict)

    @property
    def score_delta(self) -> float:
        """Points to add or remove from a setup's score, -6..+6.

        Zero until the sample is large enough - the system does not get to
        act on a pattern it has not actually observed.
        """
        if not self.enough:
            return 0.0
        return round(max(-6.0, min(6.0, (self.win_rate - 50.0) / 50.0 * 6.0)), 1)


def recall(signals, regime_name: str) -> Recall:
    """How setups taken in this regime have actually resolved."""
    closed = [s for s in signals
              if getattr(s, "regime", "") == regime_name and not s.is_live
              and s.status != "CANCELLED"]
    out = Recall(regime=regime_name, samples=len(closed))
    if not closed:
        out.note = (f"ยังไม่เคยมีสัญญาณที่ปิดแล้วในสภาพตลาด {regime_name} — "
                    f"ไม่มีสถิติให้อ้างอิง")
        return out

    out.wins = sum(1 for s in closed if s.tp1_hit)
    out.losses = sum(1 for s in closed if s.status == "SL_HIT" and not s.tp1_hit)
    out.win_rate = round(out.wins / len(closed) * 100.0, 1)

    realised = []
    for s in closed:
        if s.tp3_hit:
            realised.append(s.rr * 3)
        elif s.tp2_hit:
            realised.append(s.rr * 2)
        elif s.tp1_hit:
            realised.append(s.rr)
        else:
            realised.append(-1.0)
    out.avg_rr = round(sum(realised) / len(realised), 2)

    streak = worst = 0
    for s in closed:
        if s.tp1_hit:
            streak = 0
        else:
            streak += 1
            worst = max(worst, streak)
    out.worst_streak = worst

    for s in closed:
        for name in (getattr(s, "strategies", None) or []):
            bucket = out.strategies.setdefault(name, [0, 0])
            bucket[0] += 1
            bucket[1] += 1 if s.tp1_hit else 0

    out.enough = len(closed) >= MIN_SAMPLE
    out.note = (f"สภาพตลาดนี้เคยปิดไปแล้ว {len(closed)} ไม้ · "
                f"ชนะ {out.win_rate:.0f}% · RR เฉลี่ย {out.avg_rr:+.2f} · "
                f"แพ้ติดกันสูงสุด {out.worst_streak} ไม้"
                if out.enough else
                f"มีเพียง {len(closed)} ไม้ที่ปิดแล้วในสภาพตลาดนี้ "
                f"(ต้องมี {MIN_SAMPLE} ไม้ขึ้นไปจึงจะเชื่อสถิติได้) — "
                f"ยังไม่นำมาปรับคะแนน")
    return out


def learn(signals) -> dict:
    """LEVEL 7 - per-category weight multipliers from what actually worked.

    A category earns a nudge up when the setups that scored well on it won
    more often than the setups that did not. Adjustments are capped, and
    nothing is learned at all until the sample is large enough.
    """
    closed = [s for s in signals if not s.is_live and s.status != "CANCELLED"
              and getattr(s, "scores", None)]
    if len(closed) < MIN_SAMPLE:
        return {}

    out: dict = {}
    keys = set()
    for s in closed:
        keys.update(s.scores or {})
    for key in keys:
        strong = [s for s in closed if (s.scores or {}).get(key, 0) >= 60]
        weak = [s for s in closed if (s.scores or {}).get(key, 0) < 60]
        if len(strong) < 5 or len(weak) < 5:
            continue
        strong_rate = sum(1 for s in strong if s.tp1_hit) / len(strong)
        weak_rate = sum(1 for s in weak if s.tp1_hit) / len(weak)
        edge = strong_rate - weak_rate            # -1 .. +1
        out[key] = round(1.0 + max(-MAX_ADJUST, min(MAX_ADJUST, edge)), 3)
    return out


def summary(signals) -> str:
    """One line on the whole record, for the report footer."""
    closed = [s for s in signals if not s.is_live and s.status != "CANCELLED"]
    if not closed:
        return "ยังไม่มีสัญญาณที่ปิดแล้ว — ระบบยังไม่มีสถิติของตัวเอง"
    wins = sum(1 for s in closed if s.tp1_hit)
    rate = wins / len(closed) * 100.0
    tail = "" if len(closed) >= MIN_SAMPLE else f" (ยังไม่ถึง {MIN_SAMPLE} ไม้ ถือว่ายังไม่นิ่ง)"
    return f"สถิติสะสม {len(closed)} ไม้ · ถึง TP1 {rate:.0f}%{tail}"
