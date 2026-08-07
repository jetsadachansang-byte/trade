"""Daily result review - what the signals of the session just closed did.

Sent at 05:00 Bangkok, an hour before the planning report, because that is
when the session it covers is actually over: New York closes at 04:00-05:00
Bangkok, so a calendar day would cut the busiest hours of the US afternoon
in half and file them under the wrong date. The window this reviews runs
05:00 to 05:00, which is the market's own day rather than the clock's.

The result of a signal is not a fact the bot observes - the bot never opens
a position. It is what the published management rule would have produced:
a third off at each take profit, the stop to break-even once TP1 is paid.
Every R figure here is stated on that basis and labelled as such, because
the alternative is presenting a simulation as a track record.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import state as ST

BANGKOK = timezone(timedelta(hours=7))

# What the management rule closes at each level.
SLICE = 1.0 / 3.0


@dataclass
class Outcome:
    """One signal and what the management rule made of it."""
    signal: object
    result_r: float = 0.0
    label: str = ""
    closed: bool = False
    expected_r: float = 0.0        # what the exit engine predicted


@dataclass
class DayReview:
    """The session's record, and how well the engine's own forecast held."""
    start: datetime = None
    end: datetime = None
    issued: int = 0                            # signals sent in the window
    closed: list = field(default_factory=list)  # Outcome, finished in window
    still_open: list = field(default_factory=list)
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    expired: int = 0
    total_r: float = 0.0
    expected_r: float = 0.0
    win_rate: float = 0.0
    by_symbol: dict = field(default_factory=dict)   # symbol -> (n, R)
    by_regime: dict = field(default_factory=dict)   # regime -> (n, R)
    notes: list = field(default_factory=list)
    all_time: str = ""


def window(now: datetime, hour: int = 5) -> tuple:
    """The 05:00-to-05:00 Bangkok session that has just finished."""
    local = now.astimezone(BANGKOK)
    end = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if local.hour < hour:
        end -= timedelta(days=1)
    return end - timedelta(days=1), end


def due(state, now: datetime, hour: int = 5) -> bool:
    """Is the review of the last session still owed?

    Keyed on the Bangkok date of the window's end, so however many times
    the scan runs in a morning the review goes out exactly once.
    """
    local = now.astimezone(BANGKOK)
    if local.hour < hour:
        return False
    _, end = window(now, hour)
    return getattr(state, "last_summary_date", "") != end.date().isoformat()


def _r_levels(sig) -> tuple:
    """Each take profit expressed in R, from the prices actually issued."""
    risk = abs(sig.entry - sig.sl)
    if risk <= 0:
        return 0.0, 0.0, 0.0
    sign = 1.0 if sig.direction > 0 else -1.0
    return tuple(round((tp - sig.entry) * sign / risk, 2)
                 for tp in (sig.tp1, sig.tp2, sig.tp3))


def realised_r(sig) -> tuple:
    """(R, label) under the management rule the signal was published with.

    A stop that is hit before TP1 costs the full 1R. After TP1 the rule has
    already moved the stop to break-even, so the thirds banked are kept and
    the remainder goes out at zero - that is why a stopped-out trade can
    still show a profit here.
    """
    r1, r2, r3 = _r_levels(sig)
    if sig.status == ST.SL_HIT:
        if sig.tp2_hit:
            return round((r1 + r2) * SLICE, 2), "โดน SL หลัง TP2 (ยังกำไร)"
        if sig.tp1_hit:
            return round(r1 * SLICE, 2), "โดน SL หลัง TP1 (เสมอตัว/กำไรเล็กน้อย)"
        return -1.0, "โดน SL เต็ม"
    if sig.status == ST.TP3 or sig.tp3_hit:
        return round((r1 + r2 + r3) * SLICE, 2), "ถึง TP3 ครบ"
    if sig.status == ST.CANCELLED:
        return 0.0, (sig.close_reason or "ยกเลิก — หมดเวลาโดยไม่ถึง TP1")
    if sig.tp2_hit:
        return round((r1 + r2) * SLICE, 2), "ถึง TP2 แล้ว (ยังถืออยู่)"
    if sig.tp1_hit:
        return round(r1 * SLICE, 2), "ถึง TP1 แล้ว (ยังถืออยู่)"
    return 0.0, "ยังไม่ถึง TP1"


def _in_window(stamp: str, start: datetime, end: datetime) -> bool:
    if not stamp:
        return False
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return start <= moment.astimezone(BANGKOK) < end


def build(state, now: datetime, hour: int = 5, memory_line: str = "") -> DayReview:
    """Everything the 05:00 message reports, computed from stored signals."""
    start, end = window(now, hour)
    rev = DayReview(start=start, end=end, all_time=memory_line)

    for sig in state.signals:
        issued_here = _in_window(sig.created, start, end)
        if issued_here:
            rev.issued += 1

        closed_here = _in_window(getattr(sig, "closed_at", ""), start, end)
        if closed_here:
            r, label = realised_r(sig)
            out = Outcome(signal=sig, result_r=r, label=label, closed=True,
                          expected_r=getattr(sig, "expected_value", 0.0))
            rev.closed.append(out)
        elif sig.is_live and (issued_here or _in_window(sig.created, start - timedelta(days=30), end)):
            r, label = realised_r(sig)
            rev.still_open.append(
                Outcome(signal=sig, result_r=r, label=label, closed=False,
                        expected_r=getattr(sig, "expected_value", 0.0)))

    for out in rev.closed:
        sig = out.signal
        rev.total_r += out.result_r
        rev.expected_r += out.expected_r
        if out.result_r > 0.05:
            rev.wins += 1
        elif out.result_r < -0.05:
            rev.losses += 1
        else:
            rev.breakeven += 1
        if sig.status == ST.CANCELLED:
            rev.expired += 1
        n, r = rev.by_symbol.get(sig.symbol, (0, 0.0))
        rev.by_symbol[sig.symbol] = (n + 1, round(r + out.result_r, 2))
        key = sig.regime or "ไม่ระบุ"
        n, r = rev.by_regime.get(key, (0, 0.0))
        rev.by_regime[key] = (n + 1, round(r + out.result_r, 2))

    decided = rev.wins + rev.losses
    rev.win_rate = (rev.wins / decided * 100.0) if decided else 0.0
    rev.total_r = round(rev.total_r, 2)
    rev.expected_r = round(rev.expected_r, 2)

    rev.notes = _notes(rev)
    return rev


def _notes(rev: DayReview) -> list:
    """The honest read on the session - including when there is nothing to read."""
    out = []
    if not rev.closed and not rev.issued:
        out.append("ไม่มีสัญญาณส่งออกและไม่มีไม้ปิดในรอบนี้ — "
                   "ตลาดไม่เข้าเงื่อนไข ไม่ใช่ระบบหยุดทำงาน")
        return out
    if not rev.closed:
        out.append(f"ส่งสัญญาณไป {rev.issued} ไม้ แต่ยังไม่มีไม้ไหนปิดในรอบนี้ "
                   "— ยังสรุปผลไม่ได้")
        return out

    if len(rev.closed) < 5:
        out.append(f"ปิดไปแค่ {len(rev.closed)} ไม้ — กลุ่มตัวอย่างเล็กเกินกว่าจะบอกว่า "
                   "ระบบดีขึ้นหรือแย่ลง อย่าเพิ่งปรับอะไรจากรอบเดียว")

    gap = rev.total_r - rev.expected_r
    if rev.expected_r:
        if gap >= 0.5:
            out.append(f"ได้จริง {rev.total_r:+.2f}R ดีกว่าที่โมเดลคาดไว้ "
                       f"({rev.expected_r:+.2f}R)")
        elif gap <= -0.5:
            out.append(f"ได้จริง {rev.total_r:+.2f}R ต่ำกว่าที่โมเดลคาดไว้ "
                       f"({rev.expected_r:+.2f}R) — ถ้าเกิดซ้ำหลายวันแปลว่าโมเดลมองโลกสวยเกินไป")
        else:
            out.append(f"ผลจริง {rev.total_r:+.2f}R ใกล้เคียงที่โมเดลคาด "
                       f"({rev.expected_r:+.2f}R)")

    if rev.expired:
        out.append(f"มี {rev.expired} ไม้หมดอายุโดยไม่ถึง TP1 — "
                   "ถ้าเกิดบ่อยแปลว่าเข้าเร็วเกินไปหรือตั้งเวลาถือสั้นเกินไป")

    if rev.by_symbol:
        worst = min(rev.by_symbol.items(), key=lambda kv: kv[1][1])
        best = max(rev.by_symbol.items(), key=lambda kv: kv[1][1])
        if best[1][1] > 0:
            out.append(f"คู่ที่ทำผลงานดีสุดรอบนี้: {best[0]} ({best[1][1]:+.2f}R)")
        if worst[1][1] < 0:
            out.append(f"คู่ที่เสียมากสุดรอบนี้: {worst[0]} ({worst[1][1]:+.2f}R)")
    return out


# ----------------------------------------------------------------------
# The status board, and the week's tally. Both read the same stored
# signals the daily review does - nothing new is computed about the
# market, only about what was already sent.
# ----------------------------------------------------------------------

def status_due(state, now: datetime, hours: float) -> bool:
    if hours <= 0:
        return False
    last = getattr(state, "last_status_at", "")
    if not last:
        return True
    return (now - datetime.fromisoformat(last)).total_seconds() / 3600 >= hours


@dataclass
class PlanBoard:
    """Every plan and where it stands, sorted into four buckets."""
    running: list = field(default_factory=list)    # still open
    won: list = field(default_factory=list)        # reached TP3
    lost: list = field(default_factory=list)       # stopped out
    cancelled: list = field(default_factory=list)  # expired unfilled
    since: datetime = None


def board(state, now: datetime, closed_hours: int = 24) -> PlanBoard:
    """Live plans, plus the ones that finished recently.

    Every plan ever sent would grow without limit and bury today's under
    last week's, so finished plans age out while open ones never do - an
    open plan is still a decision the reader has to make.
    """
    cutoff = now - timedelta(hours=closed_hours)
    out = PlanBoard(since=cutoff)
    for sig in state.signals:
        if sig.is_live:
            out.running.append(sig)
            continue
        closed = getattr(sig, "closed_at", "")
        if closed:
            try:
                when = datetime.fromisoformat(closed)
            except ValueError:
                continue
            if when < cutoff:
                continue
        elif sig.created_at() < cutoff:
            continue
        if sig.status == ST.SL_HIT:
            out.lost.append(sig)
        elif sig.status == ST.CANCELLED:
            out.cancelled.append(sig)
        else:
            out.won.append(sig)
    return out


def stage_of(sig) -> str:
    """How far a plan has got, in three words or fewer."""
    if sig.tp3_hit:
        return "ครบ TP3"
    if sig.tp2_hit:
        return "ถึง TP2"
    if sig.tp1_hit:
        return "ถึง TP1"
    return "ยังไม่ถึง TP1"


# --- the week -----------------------------------------------------------

def week_window(now: datetime, hour: int = 6, tz=BANGKOK) -> tuple:
    """The seven days ending at this Sunday's report hour."""
    local = now.astimezone(tz)
    end = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if local < end:
        end -= timedelta(days=1)
    return end - timedelta(days=7), end


def weekly_due(state, now: datetime, hour: int = 6, weekday: int = 6) -> bool:
    """Sunday morning, once. `weekday` is Python's: Monday 0, Sunday 6."""
    local = now.astimezone(BANGKOK)
    if local.weekday() != weekday or local.hour < hour:
        return False
    return getattr(state, "last_weekly_date", "") != local.date().isoformat()


@dataclass
class WeekReview:
    start: datetime = None
    end: datetime = None
    issued: int = 0
    won: int = 0
    lost: int = 0
    cancelled: int = 0
    still_open: int = 0
    tp_counts: tuple = (0, 0, 0)     # how many plans reached TP1 / TP2 / TP3
    tp_total: int = 0                # take profits collected in total
    total_r: float = 0.0
    win_rate: float = 0.0
    by_symbol: dict = field(default_factory=dict)


def weekly(state, now: datetime, hour: int = 6) -> WeekReview:
    start, end = week_window(now, hour)
    rev = WeekReview(start=start, end=end)

    for sig in state.signals:
        if not _in_window(sig.created, start, end):
            continue
        rev.issued += 1
        if sig.is_live:
            rev.still_open += 1
        r, _ = realised_r(sig)
        rev.total_r += r
        n, acc = rev.by_symbol.get(sig.symbol, (0, 0.0))
        rev.by_symbol[sig.symbol] = (n + 1, round(acc + r, 2))

        hits = [sig.tp1_hit, sig.tp2_hit, sig.tp3_hit]
        rev.tp_counts = tuple(c + int(h) for c, h in zip(rev.tp_counts, hits))
        rev.tp_total += sum(hits)

        if sig.status == ST.CANCELLED:
            rev.cancelled += 1
        elif sig.is_live:
            pass                       # not decided yet, counts neither way
        elif r > 0.05:
            rev.won += 1
        elif r < -0.05:
            rev.lost += 1
        else:
            rev.cancelled += 1         # closed at break-even

    decided = rev.won + rev.lost
    rev.win_rate = (rev.won / decided * 100.0) if decided else 0.0
    rev.total_r = round(rev.total_r, 2)
    return rev
