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
from . import news as news_feed
from . import notifier
from .analyzer import analyse
from .config import Settings
from .profiles import resolve as resolve_profiles
from .state import ACTIVE, CANCELLED, SL_HIT, State, Signal, TP1, TP2, TP3


def in_kill_zone(cfg: Settings, now: datetime) -> bool:
    """ICT kill zones, expressed in UTC (London ~07-10, New York ~13-16)."""
    if not cfg.use_kill_zones:
        return True
    hour = now.hour
    return (cfg.london_kz[0] <= hour < cfg.london_kz[1]
            or cfg.ny_kz[0] <= hour < cfg.ny_kz[1])


def current_session(now: datetime) -> str:
    """Which session is open, in UTC. Overlap is the highest-quality window."""
    hour = now.hour
    london = 7 <= hour < 16
    newyork = 12 <= hour < 21
    if london and newyork:
        return "Overlap"
    if london:
        return "London"
    if newyork:
        return "NewYork"
    if 23 <= hour or hour < 7:
        return "Asian"
    return ""


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
                       cache: market_data.Cache) -> None:
    """Update every live signal against the latest price of its symbol."""
    for sig in state.live():
        try:
            # each signal is tracked on the timeframe it was issued from
            df = cache.get(sig.symbol, sig.timeframe)
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
            expiry = sig.expiry_hours or cfg.signal_expiry_hours
            if expiry > 0 and age_hours >= expiry:
                sig.status = CANCELLED
                tg.send(notifier.format_cancel(
                    sig.symbol, sig.side,
                    f"เกินเวลา {expiry} ชม. โดยไม่ถึง TP1", sig.id))


class Hold:
    """A qualifying setup held back by a rate limit (for the status report)."""

    def __init__(self, symbol: str, detail: str, profile: str = ""):
        self.symbol, self.stage, self.detail = symbol, "hold", detail
        self.profile = profile


def scan(state: State, tg: notifier.Telegram, cfg: Settings,
         now: datetime, cache: market_data.Cache,
         news_ctx=None, session: str = "",
         in_kz: bool = False) -> tuple[list, list[str], list]:
    """Analyse the universe and issue any qualifying signals.

    Returns (rejections, errors, views) - views always covers every
    symbol that loaded, so the briefing can report on all of them.
    """
    rejections, errors, views = [], [], []

    cap_reached = (cfg.max_signals_per_day > 0
                   and state.issued_today(now) >= cfg.max_signals_per_day)
    in_cooldown = (cfg.cooldown_minutes > 0
                   and state.minutes_since_last(now) < cfg.cooldown_minutes)
    sent = 0
    profiles = resolve_profiles(cfg.profiles)

    for symbol, tier in cfg.universe():
        for prof in profiles:
            try:
                frames = cache.frames(symbol, prof.timeframes())
            except market_data.DataError as exc:
                errors.append(str(exc))
                continue

            cand, rejection, view = analyse(
                symbol, tier, frames, cfg, prof, news_ctx, session, in_kz)
            views.append(view)
            if rejection:
                rejections.append(rejection)
                continue
            run_full = (cfg.max_signals_per_run > 0
                        and sent >= cfg.max_signals_per_run)
            live = state.has_live(symbol, prof.name)
            same_bar = state.signalled_this_bar(symbol, prof.name, cand.bar_time)
            if run_full or cap_reached or in_cooldown or live or same_bar:
                # a qualifying setup we deliberately hold back this run
                reason = ("ครบโควตาสัญญาณของรอบนี้" if run_full else
                          "ครบโควตาสัญญาณของวันนี้" if cap_reached else
                          "อยู่ในช่วง cooldown" if in_cooldown else
                          "มีสัญญาณ active ของสไตล์นี้อยู่แล้ว" if live else
                          "ส่งสัญญาณของแท่งนี้ไปแล้ว")
                rejections.append(Hold(symbol, reason, prof.name))
                continue

            signal_id = state.next_id(now)
            state.signals.append(Signal(
                id=signal_id, symbol=cand.symbol, tier=cand.tier,
                direction=cand.direction, entry=cand.entry,
                entry_low=cand.entry_low, entry_high=cand.entry_high,
                sl=cand.sl, tp1=cand.tp1, tp2=cand.tp2, tp3=cand.tp3,
                rr=cand.rr, score=cand.score, timeframe=cand.timeframe,
                profile=prof.name, expiry_hours=prof.expiry_hours,
                created=now.isoformat(timespec="seconds"), bar_time=cand.bar_time,
                reasons=list(cand.reasons),
            ))
            state.last_signal_at = now.isoformat(timespec="seconds")
            tg.send(notifier.format_signal(cand, signal_id, prof))
            print(f"SIGNAL [{prof.name}] {symbol} {cand.side} score {cand.score}")
            sent += 1

    return rejections, errors, views


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="print messages instead of sending to Telegram")
    parser.add_argument("--status", action="store_true",
                        help="always send the status report this run")
    parser.add_argument("--brief", action="store_true",
                        help="always send the chart briefing this run")
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
    cache = market_data.Cache(cfg.twelvedata_key, cfg.request_pause)
    if market_open(now) or args.ignore_hours:
        track_open_signals(state, tg, cfg, now, cache)
    else:
        print("market closed - tracking skipped")

    # --- news context: fetched once per run, shared by every symbol ---
    news_ctx = None
    if cfg.use_news:
        symbols = [sym for sym, _ in cfg.universe()]
        news_ctx = news_feed.build(
            now, news_feed.currencies_for(symbols),
            cfg.news_pre_min, cfg.news_post_min, cfg.news_soft_min)
        if news_ctx.available:
            print(f"news: ok · score {news_ctx.score:.0f}"
                  + (f" · BLOCKING: {news_ctx.blocking_reason}" if news_ctx.blocking else ""))
        else:
            print(f"news: unavailable ({news_ctx.error[:80]}) - not used as evidence")

    session = current_session(now)
    in_kz = in_kill_zone(cfg, now)

    rejections, errors, views = [], [], []
    if not (market_open(now) or args.ignore_hours):
        print("market closed - no scan")
    elif not (in_kz or args.ignore_hours):
        print(f"outside kill zones (UTC hour {now.hour}) - no scan")
    elif news_ctx is not None and news_ctx.blocking and not args.ignore_hours:
        print(f"news blackout: {news_ctx.blocking_reason} - no scan")
    else:
        rejections, errors, views = scan(
            state, tg, cfg, now, cache, news_ctx, session, in_kz)

    # --- chart briefing: the running commentary on the market ---------
    due = (cfg.briefing_minutes > 0
           and state.minutes_since_briefing(now) >= cfg.briefing_minutes)
    if views and (due or args.brief):
        tg.send(notifier.format_briefing(
            views, len(state.live()), state.issued_today(now)))
        # the spec's explicit "nothing qualifies right now" statement
        if cfg.no_setup_notice and not any(v.steps_passed >= 11 for v in views):
            tg.send(notifier.format_no_setup(news_ctx, views))
        state.last_briefing_at = now.isoformat(timespec="seconds")
        print(f"briefing sent for {len(views)} symbol(s)")

    state.save()

    if args.status or cfg.send_status_report:
        tg.send(notifier.format_status(
            rejections, len(state.live()), state.issued_today(now), errors))

    for err in errors:
        print(f"data: {err}")
    print(f"done: {len(state.live())} live signal(s), "
          f"{state.issued_today(now)} issued today · {cache.stats()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
