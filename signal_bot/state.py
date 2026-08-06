"""Persistent signal state.

GitHub Actions runners are ephemeral, so the bot stores its open signals
in a JSON file that the workflow commits back to the repository after
each run. That keeps TP/SL tracking alive across runs without any
external database.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent.parent / "signal_state.json"

ACTIVE, TP1, TP2, TP3, SL_HIT, CANCELLED = (
    "ACTIVE", "TP1", "TP2", "TP3", "SL_HIT", "CANCELLED")
LIVE_STATUSES = {ACTIVE, TP1, TP2}


@dataclass
class Signal:
    """One issued signal being tracked."""
    id: int
    symbol: str
    tier: int
    direction: int
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
    created: str                       # ISO-8601 UTC
    profile: str = ""                  # trading style that produced it
    expiry_hours: int = 0              # 0 = fall back to the global setting
    # institutional context, kept so LEVEL 3/7 can learn from the outcome
    regime: str = ""
    strategies: list = field(default_factory=list)
    scores: dict = field(default_factory=dict)
    win_probability: float = 0.0
    expected_value: float = 0.0
    # Dynamic Exit Engine: a trailing signal moves its own stop as the
    # trade advances, so the stop distance has to survive between runs.
    exit_mode: str = "fixed"
    trail_distance: float = 0.0        # in price, 0 = fixed exits
    trail_peak: float = 0.0            # best price reached so far
    bar_time: str = ""                 # entry bar this signal came from
    # Closing details, so the daily review can attribute a result to the
    # trading day it actually happened on rather than the day it was issued.
    closed_at: str = ""                # ISO-8601 UTC, "" while still running
    close_reason: str = ""             # short Thai description for the review
    # Timestamp of the newest bar already examined for TP/SL. Tracking used
    # to look only at the latest bar, so on an M1 signal scanned every five
    # minutes four bars went unchecked and a target hit inside them was
    # never announced.
    checked_to: str = ""
    status: str = ACTIVE
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    reasons: list[str] = field(default_factory=list)

    @property
    def side(self) -> str:
        return "BUY" if self.direction > 0 else "SELL"

    @property
    def is_live(self) -> bool:
        return self.status in LIVE_STATUSES

    def created_at(self) -> datetime:
        return datetime.fromisoformat(self.created)


@dataclass
class State:
    """Everything that must survive between runs."""
    signals: list[Signal] = field(default_factory=list)
    last_signal_at: str = ""           # ISO-8601 UTC, "" when none yet
    last_briefing_at: str = ""         # ISO-8601 UTC of the last chart briefing
    last_gold_scan_at: str = ""        # gold runs on its own slower clock
    last_pulse_at: str = ""            # last market-pulse check
    macro: dict = field(default_factory=dict)   # LEVEL 1 snapshot, refreshed hourly
    # Bangkok date of the last daily analysis, so the 06:00 report goes
    # out once a morning however many times the scan runs.
    last_daily_date: str = ""
    # Which session's analysis went out last, as "YYYY-MM-DD#hour" - the
    # report runs three times a day now, so the date alone cannot say
    # whether the London one has been sent.
    last_daily_slot: str = ""
    # Same idea for the 05:00 result review of the session that just closed.
    last_summary_date: str = ""
    # Bangkok date of the last news agenda, so it goes out once a morning.
    last_news_date: str = ""
    # Telegram update id to resume from, so a search typed into the chat is
    # answered exactly once rather than on every scan forever.
    last_update_id: int = 0

    # --- persistence -------------------------------------------------
    @classmethod
    def load(cls, path: Path = STATE_FILE) -> "State":
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        signals = [Signal(**item) for item in raw.get("signals", [])]
        return cls(signals=signals, last_signal_at=raw.get("last_signal_at", ""),
                   last_briefing_at=raw.get("last_briefing_at", ""),
                   last_gold_scan_at=raw.get("last_gold_scan_at", ""),
                   last_pulse_at=raw.get("last_pulse_at", ""),
                   macro=raw.get("macro", {}),
                   last_daily_date=raw.get("last_daily_date", ""),
                   last_daily_slot=raw.get("last_daily_slot", ""),
                   last_summary_date=raw.get("last_summary_date", ""),
                   last_news_date=raw.get("last_news_date", ""),
                   last_update_id=int(raw.get("last_update_id", 0) or 0))

    def save(self, path: Path = STATE_FILE) -> None:
        # keep the file small: drop finished signals older than 30 days
        cutoff = datetime.now(timezone.utc).timestamp() - 30 * 86400
        keep = [s for s in self.signals
                if s.is_live or s.created_at().timestamp() > cutoff]
        payload = {
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "last_signal_at": self.last_signal_at,
            "last_briefing_at": self.last_briefing_at,
            "last_gold_scan_at": self.last_gold_scan_at,
            "last_pulse_at": self.last_pulse_at,
            "macro": self.macro,
            "last_daily_date": self.last_daily_date,
            "last_daily_slot": self.last_daily_slot,
            "last_summary_date": self.last_summary_date,
            "last_news_date": self.last_news_date,
            "last_update_id": self.last_update_id,
            "signals": [asdict(s) for s in keep],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    # --- queries -------------------------------------------------------
    def live(self) -> list[Signal]:
        return [s for s in self.signals if s.is_live]

    def has_live(self, symbol: str, profile: str = "") -> bool:
        """Is a signal still running for this symbol (optionally this style)?

        Styles are tracked separately: a scalp and a swing position on the
        same symbol are different trades and must not block each other.
        """
        return any(s.symbol == symbol and s.is_live
                   and (not profile or s.profile == profile)
                   for s in self.signals)

    def signalled_this_bar(self, symbol: str, profile: str,
                           bar_time: str) -> bool:
        """Already issued a signal for this symbol/style on this candle?

        Without a cooldown the scan can run several times inside one
        entry-timeframe bar, so this stops the same setup being sent
        again the moment a previous signal closes.
        """
        if not bar_time:
            return False
        return any(s.symbol == symbol and s.bar_time == bar_time
                   and (not profile or s.profile == profile)
                   for s in self.signals)

    def issued_today(self, now: datetime, symbols=None,
                     exclude=None) -> int:
        """Signals issued today, optionally restricted to a set of symbols.

        The symbol filters exist because gold and the currency pairs are
        paced against separate daily targets.
        """
        today = now.date()
        return sum(1 for s in self.signals
                   if s.created_at().date() == today
                   and (symbols is None or s.symbol in symbols)
                   and (exclude is None or s.symbol not in exclude))

    def minutes_since_last(self, now: datetime) -> float:
        if not self.last_signal_at:
            return float("inf")
        delta = now - datetime.fromisoformat(self.last_signal_at)
        return delta.total_seconds() / 60.0

    def minutes_since_briefing(self, now: datetime) -> float:
        if not self.last_briefing_at:
            return float("inf")
        delta = now - datetime.fromisoformat(self.last_briefing_at)
        return delta.total_seconds() / 60.0

    def minutes_since_pulse(self, now: datetime) -> float:
        if not self.last_pulse_at:
            return float("inf")
        return (now - datetime.fromisoformat(self.last_pulse_at)).total_seconds() / 60.0

    def minutes_since_gold_scan(self, now: datetime) -> float:
        if not self.last_gold_scan_at:
            return float("inf")
        delta = now - datetime.fromisoformat(self.last_gold_scan_at)
        return delta.total_seconds() / 60.0

    def next_id(self, now: datetime) -> int:
        """Unique, human-readable id (epoch seconds)."""
        base = int(now.timestamp())
        used = {s.id for s in self.signals}
        while base in used:
            base += 1
        return base

    def stats(self) -> tuple[int, int, int]:
        """(wins, losses, cancelled) - a win is any signal that reached TP1."""
        wins = losses = cancelled = 0
        for s in self.signals:
            if s.status == CANCELLED:
                cancelled += 1
            elif s.tp1_hit:
                wins += 1
            elif s.status == SL_HIT:
                losses += 1
        return wins, losses, cancelled
