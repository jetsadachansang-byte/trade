"""CapitalGuard - Baseline model training.

Trains a gradient-boosting classifier that predicts trade success from
the decision context logged by the EA. The trained model can later be
used to veto low-probability setups (an extra filter on top of the
rule-based scoring engine - never a replacement for risk management).

Usage:
    python train_model.py --features features.csv --model model.pkl
"""
from __future__ import annotations

import argparse
import pickle
from pathlib import Path

import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import classification_report, roc_auc_score
from sklearn.model_selection import train_test_split

from feature_engineering import FEATURE_COLUMNS, TARGET_COLUMN


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", required=True, help="features.csv from feature_engineering.py")
    parser.add_argument("--model", default="model.pkl", help="output model path")
    parser.add_argument("--test-size", type=float, default=0.25)
    args = parser.parse_args()

    df = pd.read_csv(args.features)
    cols = [c for c in FEATURE_COLUMNS if c in df.columns]
    X = df[cols].fillna(0.0)
    y = df[TARGET_COLUMN]

    if len(df) < 100:
        print(f"WARNING: only {len(df)} trades - results will not be statistically meaningful")

    # chronological split: never train on the future
    df_sorted = df.sort_values("open_time")
    split = int(len(df_sorted) * (1 - args.test_size))
    train_idx, test_idx = df_sorted.index[:split], df_sorted.index[split:]
    X_train, X_test = X.loc[train_idx], X.loc[test_idx]
    y_train, y_test = y.loc[train_idx], y.loc[test_idx]

    model = GradientBoostingClassifier(
        n_estimators=200, max_depth=3, learning_rate=0.05, subsample=0.8, random_state=42,
    )
    model.fit(X_train, y_train)

    proba = model.predict_proba(X_test)[:, 1]
    print(classification_report(y_test, model.predict(X_test)))
    if y_test.nunique() > 1:
        print(f"ROC-AUC: {roc_auc_score(y_test, proba):.3f}")

    importance = pd.Series(model.feature_importances_, index=cols).sort_values(ascending=False)
    print("\nFeature importance:")
    print(importance.to_string())

    with Path(args.model).open("wb") as fh:
        pickle.dump({"model": model, "features": cols}, fh)
    print(f"\nmodel saved to {args.model}")


if __name__ == "__main__":
    main()
