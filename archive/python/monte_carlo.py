"""CapitalGuard - Monte Carlo simulation of trade outcomes.

Bootstrap-resamples the historical trade P/L sequence to estimate the
distribution of final equity, maximum drawdown, and risk of hitting the
15% drawdown circuit breaker. If the strategy only looks good in its
one historical ordering of trades, it is fragile - this reveals that.

Usage:
    python monte_carlo.py --features features.csv --balance 30 --sims 5000
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd


def simulate(profits: np.ndarray, balance: float, sims: int, dd_limit: float,
             rng: np.random.Generator) -> pd.DataFrame:
    """Resample the trade sequence `sims` times and measure outcomes."""
    n = len(profits)
    results = []
    for _ in range(sims):
        # sample trades with replacement, same count as history
        seq = rng.choice(profits, size=n, replace=True)
        equity = balance + np.cumsum(seq)
        peak = np.maximum.accumulate(np.maximum(equity, balance))
        drawdown = (peak - equity) / peak
        results.append({
            "final_equity": equity[-1],
            "max_drawdown": drawdown.max(),
            "ruined": bool((drawdown >= dd_limit).any() or (equity <= 0).any()),
        })
    return pd.DataFrame(results)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", required=True, help="features.csv with a 'profit' column")
    parser.add_argument("--balance", type=float, default=30.0, help="starting balance (USD)")
    parser.add_argument("--sims", type=int, default=5000, help="number of simulations")
    parser.add_argument("--dd-limit", type=float, default=0.15, help="drawdown circuit breaker")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    df = pd.read_csv(args.features)
    profits = df["profit"].dropna().to_numpy()
    if len(profits) < 30:
        print(f"WARNING: only {len(profits)} trades - Monte Carlo results are unreliable")

    rng = np.random.default_rng(args.seed)
    res = simulate(profits, args.balance, args.sims, args.dd_limit, rng)

    pct = res["final_equity"].quantile([0.05, 0.25, 0.50, 0.75, 0.95])
    print(f"Simulations: {args.sims}  Trades per run: {len(profits)}  Start: {args.balance:.2f} USD")
    print("\nFinal equity distribution:")
    for q, v in pct.items():
        print(f"  p{int(q * 100):02d}: {v:8.2f} USD")
    print("\nMax drawdown distribution:")
    ddq = res["max_drawdown"].quantile([0.50, 0.75, 0.95, 0.99])
    for q, v in ddq.items():
        print(f"  p{int(q * 100):02d}: {v * 100:6.2f}%")
    ruin = res["ruined"].mean()
    print(f"\nP(hit {args.dd_limit * 100:.0f}% drawdown breaker): {ruin * 100:.2f}%")
    print(f"P(final equity < start):    {(res['final_equity'] < args.balance).mean() * 100:.2f}%")
    if ruin > 0.05:
        print("\nVERDICT: risk of ruin above 5% - reduce risk per trade before going live")
    else:
        print("\nVERDICT: risk profile acceptable under resampling")


if __name__ == "__main__":
    main()
