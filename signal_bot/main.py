"""Entry point: one full scan of the symbol universe.

Designed to be run on a schedule (GitHub Actions cron). Each run:
  1. loads the persisted signal state
  2. updates every live signal against fresh prices (TP1/2/3, SL, expiry)
  3. analyses the universe in priority order and issues at most one new
     signal per run - the highest-priority symbol wins
  4. saves the state back so the next run continues tracking

Run locally with:  python -m signal_bot.main
Add --dry-run to print messages instead of sending them.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from . import data as market_data
from . import notifier
from .analyzer import analyse
from .config import Settings
from .state import ACTIVE, CANCELLED, SL_HIT, State, Signal, TP1, TP2, TP3

TIMEFRAMES = ["D1", "H4", "H1"]


def in_kill_zone(cfg: Settings, now: datetime) -> bool:
    """ICT kill zones, expressed in UTC (London ~07-10, New York ~13-16)."""
    if not cfg.use_kill_zones:
        return True
    hour = now.hour
    return (cfg.london_kz[0] <= hour < cfg.london_kz[1]
            or cfg.ny_kz[0] <= hour < cfg.ny_kz[1])


def market_open(now: datetime) -> bool:
    """Forex is closed from Friday 22:00 UTC to Sunday 22:00 UTC."""
    weekday = now.weekday()          # Mon=0 ... Sun=6
    if weekday == 5:                 # Saturday
        return False
    if weekday == 4 and now.hour >= 22:
        return False
    if weekday == 6 and now.hour < 22:
        return False
    return True


def track_open_signals(state: State, tg: notifier.Telegram,
                       cfg: Settings, now: datetime,
                       api_key: str) -> None:
    """Update every live signal against the latest price of its symbol."""
    for sig in state.live():
        try:
            df = market_data.load(sig.symbol, cfg.entry_timeframe, api_key)
        except market_data.DataError as exc:
            print(f"track {sig.symbol}: {exc}")
            continue

        high = float(df["high"].iloc[-1])
        low = float(df["low"].iloc[-1])
        is_buy = sig.direction > 0

        # --- stop loss (checked first: worst case wins) ----------------
        if (is_buy and low <= sig.sl) or (not is_buy and high >= sig.sl):
            sig.status = SL_HIT
            tg.send(notifier.format_sl(sig.symbol, sig.side, sig.sl, sig.id))
            continue

        # --- take profits, announced once each, in order ---------------
        for level, target, already in (
            (1, sig.tp1, sig.tp1_hit), (2, sig.tp2, sig.tp2_hit), (3, sig.tp3, sig.tp3_hit)
        ):
            if already:
                continue
            reached = high >= target if is_buy else low <= target
            if not reached:
                break                # cannot hit TP3 before TP2
            if level == 1:
                sig.tp1_hit, sig.status = True, TP1
            elif level == 2:
                sig.tp2_hit, sig.status = True, TP2
            else:
                sig.tp3_hit, sig.status = True, TP3
            tg.send(notifier.format_tp(sig.symbol, sig.side, level, target, sig.id))

        # --- expiry: never reached TP1 within the window ---------------
        if sig.status == ACTIVE:
            age_hours = (now - sig.created_at()).total_seconds() / 3600
            if cfg.signal_expiry_hours > 0 and age_hours >= cfg.signal_expiry_hours:
                sig.status = CANCELLED
                tg.send(notifier.format_cancel(
                    sig.symbol, sig.side,
                    f"เกินเวลา {cfg.signal_expiry_hours} ชม. โดยไม่ถึง TP1", sig.id))


class Hold:
    """A qualifying setup held back by a rate limit (for the status report)."""

    def __init__(self, symbol: str, detail: str):
        self.symbol, self.stage, self.detail = symbol, "hold", detail


def scan(state: State, tg: notifier.Telegram, cfg: Settings,
         now: datetime) -> tuple[list, list[str]]:
    """Analyse the universe; issue at most one signal. Returns (rejections, errors)."""
    rejections, errors = [], []

    cap_reached = (cfg.max_signals_per_day > 0
                   and state.issued_today(now) >= cfg.max_signals_per_day)
    in_cooldown = (cfg.cooldown_minutes > 0
                   and state.minutes_since_last(now) < cfg.cooldown_minutes)
    sent = 0

    for symbol, tier in cfg.universe():
        try:
            frames = market_data.load_multi(
                symbol, TIMEFRAMES + [cfg.entry_timeframe],
                cfg.twelvedata_key, cfg.request_pause)
        except market_data.DataError as exc:
            errors.append(str(exc))
            continue

        cand, rejection = analyse(symbol, tier, frames, cfg)
        if rejection:
            rejections.append(rejection)
            continue
        run_full = (cfg.max_signals_per_run > 0 and sent >= cfg.max_signals_per_run)
        same_bar = state.signalled_this_bar(symbol, cand.bar_time)
        if run_full or cap_reached or in_cooldown or state.has_live(symbol) or same_bar:
            # a qualifying setup we deliberately hold back this run
            reason = ("ครบโควตาสัญญาณของรอบนี้" if run_full else
                      "ครบโควตาสัญญาณของวันนี้" if cap_reached else
                      "อยู่ในช่วง cooldown" if in_cooldown else
                      "มีสัญญาณ active ของ symbol นี้อยู่แล้ว" if state.has_live(symbol) else
                      "ส่งสัญญาณของแท่งนี้ไปแล้ว")
            rejections.append(Hold(symbol, reason))
            continue

        signal_id = state.next_id(now)
        state.signals.append(Signal(
            id=signal_id, symbol=cand.symbol, tier=cand.tier, direction=cand.direction,
            entry=cand.entry, entry_low=cand.entry_low, entry_high=cand.entry_high,
            sl=cand.sl, tp1=cand.tp1, tp2=cand.tp2, tp3=cand.tp3,
            rr=cand.rr, score=cand.score, timeframe=cand.timeframe,
            created=now.isoformat(timespec="seconds"), bar_time=cand.bar_time,
            reasons=list(cand.reasons),
        ))
        state.last_signal_at = now.isoformat(timespec="seconds")
        tg.send(notifier.format_signal(cand, signal_id))
        print(f"SIGNAL {symbol} {cand.side} score {cand.score}")
        sent += 1

    return rejections, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="print messages instead of sending to Telegram")
    parser.add_argument("--status", action="store_true",
                        help="always send the status report this run")
    parser.add_argument("--ignore-hours", action="store_true",
                        help="skip market-hours and kill-zone checks (for testing)")
    args = parser.parse_args(argv)

    cfg = Settings()
    problems = cfg.validate()
    if problems and not args.dry_run:
        for problem in problems:
            print(f"config: {problem}")
        return 1

    now = datetime.now(timezone.utc)
    tg = notifier.Telegram(cfg.telegram_token, cfg.telegram_chat_id, args.dry_run)
    state = State.load()

    # tracking runs even outside kill zones - an open signal must be
    # followed to its conclusion whatever the hour
    if market_open(now) or args.ignore_hours:
        track_open_signals(state, tg, cfg, now, cfg.twelvedata_key)
    else:
        print("market closed - tracking skipped")

    rejections, errors = [], []
    if not (market_open(now) or args.ignore_hours):
        print("market closed - no scan")
    elif not (in_kill_zone(cfg, now) or args.ignore_hours):
        print(f"outside kill zones (UTC hour {now.hour}) - no scan")
    else:
        rejections, errors = scan(state, tg, cfg, now)

    state.save()

    if args.status or cfg.send_status_report:
        tg.send(notifier.format_status(
            rejections, len(state.live()), state.issued_today(now), errors))

    for err in errors:
        print(f"data: {err}")
    print(f"done: {len(state.live())} live signal(s), "
          f"{state.issued_today(now)} issued today")
    return 0


if __name__ == "__main__":
    sys.exit(main())
