"""CapitalGuard - Feature engineering pipeline.

Reads the JSONL trade log exported by the EA (MQL5/Files/CapitalGuard/
trades_<magic>.jsonl), joins open/close events into completed trades and
builds a feature matrix ready for model training.

Usage:
    python feature_engineering.py --log trades_20260803.jsonl --out features.csv
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def load_events(path: Path) -> pd.DataFrame:
    """Parse the JSON-lines log into a flat DataFrame of events."""
    rows = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # tolerate partial lines from live logging
    return pd.DataFrame(rows)


def build_trades(events: pd.DataFrame) -> pd.DataFrame:
    """Join open and close events into one row per completed trade."""
    opens = events[events["event"] == "open"].copy()
    closes = events[events["event"] == "close"].copy()
    if opens.empty or closes.empty:
        return pd.DataFrame()

    # scores dict -> flat columns
    scores = opens["scores"].apply(pd.Series)
    scores.columns = [f"score_{c}" for c in scores.columns]
    opens = pd.concat([opens.drop(columns=["scores"]), scores], axis=1)

    # last close per position carries the final realized numbers
    closes = closes.sort_values("time").groupby("position", as_index=False).agg(
        profit=("profit", "sum"),
        realized_rr=("realized_rr", "last"),
        close_time=("time", "last"),
    )
    trades = opens.merge(closes, left_on="ticket", right_on="position", how="inner")
    trades["win"] = (trades["profit"] > 0).astype(int)
    return trades


def add_features(trades: pd.DataFrame) -> pd.DataFrame:
    """Derive model features from the raw trade context."""
    df = trades.copy()
    df["open_time"] = pd.to_datetime(df["time"], format="%Y.%m.%d %H:%M:%S", errors="coerce")
    df["hour"] = df["open_time"].dt.hour
    df["weekday"] = df["open_time"].dt.weekday
    df["is_buy"] = (df["dir"] == "BUY").astype(int)
    df["sl_dist"] = (df["entry"] - df["sl"]).abs()
    df["tp_dist"] = (df["tp"] - df["entry"]).abs()
    # one-hot the regime label ("TREND UP/HIGH VOL" style strings)
    df["regime_trend"] = df["regime"].str.contains("TREND", na=False).astype(int)
    df["regime_highvol"] = df["regime"].str.contains("HIGH", na=False).astype(int)
    df["session_london"] = (df["session"] == "London").astype(int)
    df["session_ny"] = (df["session"] == "NewYork").astype(int)
    return df


FEATURE_COLUMNS = [
    "score", "score_structure", "score_liquidity", "score_bos_choch",
    "score_orderblock", "score_fvg", "score_volume", "score_indicator",
    "planned_rr", "hour", "weekday", "is_buy", "sl_dist", "tp_dist",
    "regime_trend", "regime_highvol", "session_london", "session_ny",
]
TARGET_COLUMN = "win"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True, help="path to trades_<magic>.jsonl")
    parser.add_argument("--out", default="features.csv", help="output CSV path")
    args = parser.parse_args()

    events = load_events(Path(args.log))
    trades = build_trades(events)
    if trades.empty:
        raise SystemExit("no completed trades found in the log")
    df = add_features(trades)
    cols = [c for c in FEATURE_COLUMNS if c in df.columns] + [TARGET_COLUMN, "profit", "open_time"]
    df[cols].to_csv(args.out, index=False)
    print(f"wrote {len(df)} trades with {len(cols)} columns to {args.out}")


if __name__ == "__main__":
    main()
