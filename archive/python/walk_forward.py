"""CapitalGuard - Walk-forward analysis harness.

Splits the trade history into rolling train/test windows and evaluates
model (or parameter-set) stability across time. A strategy that only
performs in one window is overfit - reject it.

This harness evaluates the ML veto model; for EA parameter optimization
use the MT5 Strategy Tester's built-in Forward mode (see repo README).

Usage:
    python walk_forward.py --features features.csv --train-window 200 --test-window 50
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import roc_auc_score

from feature_engineering import FEATURE_COLUMNS, TARGET_COLUMN


def walk_forward(df: pd.DataFrame, train_window: int, test_window: int) -> pd.DataFrame:
    """Roll a train window forward and score each out-of-sample block."""
    df = df.sort_values("open_time").reset_index(drop=True)
    cols = [c for c in FEATURE_COLUMNS if c in df.columns]
    results = []

    start = 0
    while start + train_window + test_window <= len(df):
        train = df.iloc[start : start + train_window]
        test = df.iloc[start + train_window : start + train_window + test_window]

        model = GradientBoostingClassifier(
            n_estimators=150, max_depth=3, learning_rate=0.05, random_state=42,
        )
        model.fit(train[cols].fillna(0.0), train[TARGET_COLUMN])
        proba = model.predict_proba(test[cols].fillna(0.0))[:, 1]

        auc = np.nan
        if test[TARGET_COLUMN].nunique() > 1:
            auc = roc_auc_score(test[TARGET_COLUMN], proba)

        # simulate the veto: only take trades the model likes
        taken = test[proba >= 0.5]
        results.append({
            "window_start": test["open_time"].iloc[0],
            "trades_total": len(test),
            "trades_taken": len(taken),
            "auc": auc,
            "pnl_all": test["profit"].sum(),
            "pnl_vetoed": taken["profit"].sum(),
        })
        start += test_window

    return pd.DataFrame(results)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", required=True)
    parser.add_argument("--train-window", type=int, default=200)
    parser.add_argument("--test-window", type=int, default=50)
    args = parser.parse_args()

    df = pd.read_csv(args.features)
    res = walk_forward(df, args.train_window, args.test_window)
    if res.empty:
        raise SystemExit("not enough trades for the requested windows")

    print(res.to_string(index=False))
    print("\nSummary:")
    print(f"  mean AUC:          {res['auc'].mean():.3f}")
    print(f"  PnL without veto:  {res['pnl_all'].sum():+.2f}")
    print(f"  PnL with veto:     {res['pnl_vetoed'].sum():+.2f}")
    consistent = (res["pnl_vetoed"] >= res["pnl_all"]).mean()
    print(f"  veto helped in {consistent * 100:.0f}% of windows")


if __name__ == "__main__":
    main()
