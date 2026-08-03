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
        return cls(signals=signals, last_signal_at=raw.get("last_signal_at", ""))

    def save(self, path: Path = STATE_FILE) -> None:
        # keep the file small: drop finished signals older than 30 days
        cutoff = datetime.now(timezone.utc).timestamp() - 30 * 86400
        keep = [s for s in self.signals
                if s.is_live or s.created_at().timestamp() > cutoff]
        payload = {
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "last_signal_at": self.last_signal_at,
            "signals": [asdict(s) for s in keep],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    # --- queries -------------------------------------------------------
    def live(self) -> list[Signal]:
        return [s for s in self.signals if s.is_live]

    def has_live(self, symbol: str) -> bool:
        return any(s.symbol == symbol and s.is_live for s in self.signals)

    def issued_today(self, now: datetime) -> int:
        today = now.date()
        return sum(1 for s in self.signals if s.created_at().date() == today)

    def minutes_since_last(self, now: datetime) -> float:
        if not self.last_signal_at:
            return float("inf")
        delta = now - datetime.fromisoformat(self.last_signal_at)
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
