"""Merge this run's signal state onto whatever the repo advanced to.

The save step used to reset to origin/main and then copy this run's file
straight over it. That silently discards anything a run which finished in
between had written: two scans overlap, the later one checked the repo out
before the earlier one pushed, and the earlier one's work is gone. It cost
a 05:00 review its "already sent" marker within thirty seconds of being
written, which would have sent the same review again on the next scan.

So the two versions are merged instead of one replacing the other. Every
field here moves in one direction only - a signal progresses toward being
closed, a marker date moves forward - so "merge" means taking whichever
side got further, never averaging or guessing.

Usage:  python -m signal_bot.merge_state <mine.json> <onto.json>
The result is written back to <onto.json>.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Dates and timestamps are ISO-8601, so a plain string comparison already
# orders them correctly and the later one wins.
FORWARD_ONLY = ("last_signal_at", "last_gold_scan_at", "last_pulse_at",
                "last_daily_date", "last_summary_date", "updated")

# "YYYY-MM-DD#HH" markers are compared by date and hour, not as raw text.
# Padding makes the plain string comparison correct today, but a marker
# that silently goes backwards costs a report resent every scan, so this
# does not rely on the format staying padded.
SLOT_KEYS = ("last_daily_slot",)


def _slot_rank(value: str) -> tuple:
    date, _, hour = (value or "").partition("#")
    try:
        return (date, int(hour))
    except ValueError:
        return (date, -1)


def _progress(sig: dict) -> tuple:
    """How far a signal got, as something two versions can be compared on."""
    return (
        1 if sig.get("closed_at") else 0,          # finished beats running
        sum(1 for k in ("tp1_hit", "tp2_hit", "tp3_hit") if sig.get(k)),
        1 if sig.get("status") in ("SL_HIT", "CANCELLED", "TP3") else 0,
        str(sig.get("checked_to") or ""),          # examined more bars
        abs(float(sig.get("trail_peak") or 0.0)),  # trailed further
    )


def _pick(a: dict, b: dict) -> dict:
    """Whichever version of one signal got further."""
    return a if _progress(a) >= _progress(b) else b


def merge(mine: dict, theirs: dict) -> dict:
    """Combine two state documents without either one losing work."""
    out = dict(theirs)

    for key in FORWARD_ONLY:
        out[key] = max(mine.get(key, "") or "", theirs.get(key, "") or "")
    for key in SLOT_KEYS:
        out[key] = max(mine.get(key, "") or "", theirs.get(key, "") or "",
                       key=_slot_rank)

    # The macro snapshot is a cache; this run may have just refreshed it.
    if mine.get("macro"):
        out["macro"] = mine["macro"]

    by_id: dict = {}
    for sig in theirs.get("signals", []) + mine.get("signals", []):
        key = sig.get("id")
        by_id[key] = _pick(by_id[key], sig) if key in by_id else sig
    out["signals"] = sorted(by_id.values(), key=lambda s: s.get("id") or 0)
    return out


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def main(argv: list | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: python -m signal_bot.merge_state <mine.json> <onto.json>")
        return 2
    mine_path, onto_path = Path(args[0]), Path(args[1])
    mine = _load(mine_path)
    if not mine:
        print("merge: this run wrote no readable state - keeping the repo's copy")
        return 0
    theirs = _load(onto_path)
    merged = merge(mine, theirs) if theirs else mine
    onto_path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    kept = len(merged.get("signals", []))
    print(f"merge: {len(mine.get('signals', []))} จากรอบนี้ + "
          f"{len(theirs.get('signals', []))} จาก repo → {kept} รายการ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
