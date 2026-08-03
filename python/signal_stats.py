"""CapitalGuard - Signal statistics: daily / weekly / monthly summaries.

Reads the signal JSONL log written by CapitalGuardSignalEA
(MQL5/Files/CapitalGuard/signals_<magic>.jsonl) and produces
per-day, per-week and per-month summaries of signal outcomes.

Win = signal reached TP1 or beyond. Loss = SL hit before TP1.
Cancelled signals are counted separately and excluded from win rate.

Usage:
    python signal_stats.py --log signals_20260804.jsonl
    python signal_stats.py --log signals_20260804.jsonl --export summary.csv
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

# lifecycle terminal outcomes we aggregate on
WIN_EVENTS = {"TP1_HIT", "TP2_HIT", "TP3_HIT"}


def load_events(path: Path) -> pd.DataFrame:
    """Parse the JSON-lines log into a DataFrame of lifecycle events."""
    rows = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rows.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    df = pd.DataFrame(rows)
    if df.empty:
        raise SystemExit("no events found in the log")
    df["time"] = pd.to_datetime(df["time"], format="%Y.%m.%d %H:%M:%S", errors="coerce")
    return df


def outcomes(df: pd.DataFrame) -> pd.DataFrame:
    """Reduce lifecycle events to one row per signal with its outcome."""
    sigs = df[df["event"] == "SIGNAL"][["signal_id", "time", "dir", "entry", "sl", "rr", "score"]]
    results = []
    for sid, group in df.groupby("signal_id"):
        events = set(group["event"])
        if "SIGNAL" not in events:
            continue
        if events & WIN_EVENTS:
            outcome = "win"
            # realized R: best TP level reached
            best_r = 3.0 if "TP3_HIT" in events else 2.0 if "TP2_HIT" in events else 1.0
        elif "SL_HIT" in events:
            outcome, best_r = "loss", -1.0
        elif "CANCELLED" in events:
            outcome, best_r = "cancelled", 0.0
        else:
            outcome, best_r = "open", 0.0
        results.append({"signal_id": sid, "outcome": outcome, "best_r": best_r})
    out = sigs.merge(pd.DataFrame(results), on="signal_id", how="left")
    return out


def summarize(trades: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Aggregate signal outcomes by calendar period ('D', 'W', 'ME')."""
    t = trades.copy()
    t["period"] = t["time"].dt.to_period({"D": "D", "W": "W", "ME": "M"}[freq])
    rows = []
    for period, g in t.groupby("period"):
        wins = (g["outcome"] == "win").sum()
        losses = (g["outcome"] == "loss").sum()
        cancelled = (g["outcome"] == "cancelled").sum()
        decided = wins + losses
        rows.append({
            "period": str(period),
            "signals": len(g),
            "wins": wins,
            "losses": losses,
            "cancelled": cancelled,
            "win_rate_%": round(100 * wins / decided, 1) if decided else None,
            "net_r": round(g["best_r"].sum(), 1),
            "avg_score": round(g["score"].mean(), 1),
        })
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True, help="signals_<magic>.jsonl path")
    parser.add_argument("--export", default="", help="optional CSV path for the daily summary")
    args = parser.parse_args()

    events = load_events(Path(args.log))
    trades = outcomes(events)

    print(f"Signals total: {len(trades)}")
    for label, freq in [("DAILY", "D"), ("WEEKLY", "W"), ("MONTHLY", "ME")]:
        summary = summarize(trades, freq)
        print(f"\n===== {label} =====")
        print(summary.to_string(index=False))
        if freq == "D" and args.export:
            summary.to_csv(args.export, index=False)
            print(f"(exported to {args.export})")


if __name__ == "__main__":
    main()
