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

from . import daily as daily_report
from . import data as market_data
from . import macro as macro_feed
from . import memory as memory_bank
from . import news as news_feed
from . import notifier
from . import review as day_review
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


def _close(sig: Signal, now: datetime, reason: str) -> None:
    """Stamp a finished signal so the daily review can date its result.

    Without this the review can only see when a signal was issued, which
    files a Monday entry that stopped out on Wednesday under Monday.
    """
    if not sig.closed_at:
        sig.closed_at = now.isoformat(timespec="seconds")
        sig.close_reason = reason


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
            _close(sig, now, "โดน SL")
            tg.send(notifier.format_sl(sig.symbol, sig.side, sig.sl, sig.id))
            continue

        # --- trailing stop: move the stop up behind the move -----------
        # A trailing signal is only trailing if something actually moves
        # the stop, so this is where the exit plan earns its name.
        if sig.exit_mode == "trailing" and sig.trail_distance > 0:
            peak = sig.trail_peak or sig.entry
            peak = max(peak, high) if is_buy else min(peak, low)
            if peak != sig.trail_peak:
                sig.trail_peak = peak
            moved = (peak - sig.trail_distance if is_buy
                     else peak + sig.trail_distance)
            # never widen a stop, and never trail past break-even backwards
            if (is_buy and moved > sig.sl) or (not is_buy and moved < sig.sl):
                old_sl, sig.sl = sig.sl, round(moved, 5)
                tg.send(notifier.format_trail(sig.symbol, sig.side, old_sl,
                                              sig.sl, sig.id))

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
                _close(sig, now, "ถึง TP3 ครบแผน")
            tg.send(notifier.format_tp(sig.symbol, sig.side, level, target, sig.id))

        # --- expiry: never reached TP1 within the window ---------------
        if sig.status == ACTIVE:
            age_hours = (now - sig.created_at()).total_seconds() / 3600
            expiry = sig.expiry_hours or cfg.signal_expiry_hours
            if expiry > 0 and age_hours >= expiry:
                sig.status = CANCELLED
                _close(sig, now, f"หมดเวลา {expiry} ชม. โดยไม่ถึง TP1")
                tg.send(notifier.format_cancel(
                    sig.symbol, sig.side,
                    f"เกินเวลา {expiry} ชม. โดยไม่ถึง TP1", sig.id))


def pacing_shortfall(now: datetime, target: int, issued: int) -> tuple:
    """How far behind its daily target one group of symbols is running.

    Returns (shortfall, explanation) where shortfall is 0.0 when the group
    is on or ahead of pace and 1.0 when it is a whole day's target behind.
    The expectation is prorated by the hours elapsed so the bar is not
    thrown wide open at 00:30 just because nothing has been sent yet.
    """
    if target <= 0:
        return 0.0, ""
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    hours_elapsed = max(0.5, (now - day_start).total_seconds() / 3600)
    expected = target * min(1.0, hours_elapsed / 24.0)
    deficit = expected - issued
    if deficit <= 0:
        return 0.0, f"on pace ({issued} issued, {expected:.1f} expected)"
    shortfall = min(1.0, deficit / target)
    return shortfall, f"behind pace ({issued}/{expected:.1f}, gap {shortfall:.0%})"


def profile_threshold(cfg: Settings, prof, shortfall: float) -> float:
    """The score bar for one style, after daily pacing.

    Only the score requirement moves - every structural gate still has to
    pass - and it never goes below `min_score_floor`. `pace_weight` is what
    biases the day-trade and scalp styles: they take the full relaxation,
    while a multi-day position barely moves off its own bar.

    This is a pacing aid, not a guarantee. On a quiet day the pipeline
    gates alone can keep the count under target, and that is the correct
    outcome rather than something to force.
    """
    base = cfg.number("SCORE_THRESHOLD", prof.score_threshold)
    if not cfg.adaptive_threshold or shortfall <= 0:
        return base
    give = (base - cfg.min_score_floor) * shortfall * prof.pace_weight
    return max(cfg.min_score_floor, base - give)


class Hold:
    """A qualifying setup held back by a rate limit (for the status report)."""

    def __init__(self, symbol: str, detail: str, profile: str = ""):
        self.symbol, self.stage, self.detail = symbol, "hold", detail
        self.profile = profile


def scan(state: State, tg: notifier.Telegram, cfg: Settings,
         now: datetime, cache: market_data.Cache,
         news_ctx=None, session: str = "", in_kz: bool = False,
         macro_view=None, learned=None) -> tuple[list, list[str], list]:
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

    # --- daily pacing, tracked separately for gold and for the pairs ---
    gold_gap, gold_why = pacing_shortfall(
        now, cfg.gold_daily_target,
        state.issued_today(now, symbols=set(cfg.gold_symbols)))
    pair_gap, pair_why = pacing_shortfall(
        now, cfg.pair_daily_target,
        state.issued_today(now, exclude=set(cfg.gold_symbols)))
    if cfg.adaptive_threshold:
        if gold_why:
            print(f"pacing[gold]: {gold_why}")
        if pair_why:
            print(f"pacing[pairs]: {pair_why}")

    # Signals are collected first and sent grouped at the end, so long-hold
    # setups are announced in their own section instead of being scattered
    # among the short-term ones.
    short_batch, long_batch = [], []

    # Gold spot comes from Twelve Data, which has a daily request budget,
    # so it runs on a slower clock than the pairs instead of every scan.
    gold_due = (cfg.gold_scan_minutes <= 0
                or state.minutes_since_gold_scan(now) >= cfg.gold_scan_minutes)
    if not gold_due:
        print(f"gold: skipped this run "
              f"(next in {cfg.gold_scan_minutes - state.minutes_since_gold_scan(now):.0f} min)")

    for symbol, tier in cfg.universe():
        is_gold = cfg.is_gold(symbol)
        if is_gold and not gold_due:
            continue
        gap = gold_gap if is_gold else pair_gap
        for prof in profiles:
            try:
                frames = cache.frames(symbol, prof.timeframes())
            except market_data.DataError as exc:
                errors.append(str(exc))
                continue

            bar = profile_threshold(cfg, prof, gap)
            cand, rejection, view = analyse(
                symbol, tier, frames, cfg, prof, news_ctx, session, in_kz,
                bar if cfg.adaptive_threshold else None,
                macro_view=macro_view, learned=learned, signals=state.signals)
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
                regime=cand.regime, strategies=list(cand.strategies),
                scores=dict(cand.scores), win_probability=cand.win_probability,
                expected_value=cand.expected_value,
            ))
            state.last_signal_at = now.isoformat(timespec="seconds")
            batch = long_batch if prof.is_long_hold else short_batch
            batch.append((cand, signal_id, prof))
            print(f"SIGNAL [{prof.name}] {symbol} {cand.side} "
                  f"score {cand.score} grade {cand.grade} (bar {bar:.0f})")
            sent += 1

    if gold_due:
        state.last_gold_scan_at = now.isoformat(timespec="seconds")

    _send_batches(tg, short_batch, long_batch)
    return rejections, errors, views


def _send_batches(tg: notifier.Telegram, short_batch: list,
                  long_batch: list) -> None:
    """Announce short-term and long-hold signals as two separate sections."""
    for batch, horizon in ((short_batch, "short"), (long_batch, "long")):
        if not batch:
            continue
        tg.send(notifier.format_section(horizon, len(batch)))
        # best grade first, so the strongest setup of the section leads
        for cand, signal_id, prof in sorted(batch, key=lambda x: -x[0].score):
            tg.send(notifier.format_signal(cand, signal_id, prof))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="print messages instead of sending to Telegram")
    parser.add_argument("--status", action="store_true",
                        help="always send the status report this run")
    parser.add_argument("--brief", action="store_true",
                        help="always send the chart briefing this run")
    parser.add_argument("--daily", action="store_true",
                        help="send the daily market analysis now, ignoring the clock")
    parser.add_argument("--summary", action="store_true",
                        help="send the daily result review now, ignoring the clock")
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
    cache = market_data.Cache(cfg.twelvedata_key, cfg.request_pause,
                              cfg.allow_gold_futures, set(cfg.gold_symbols))
    if cfg.gold_symbols and not cfg.twelvedata_key:
        print("gold: no TWELVEDATA_API_KEY - Yahoo has no spot gold, "
              "so gold will be skipped (see docs/MOBILE_SETUP_TH.md)")
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

    # --- LEVEL 1: the global tape, refreshed on its own slow clock ----
    macro_view = macro_feed.MacroView.from_dict(state.macro)
    if cfg.use_macro and macro_view.age_minutes(now) >= cfg.macro_refresh_minutes:
        macro_view = macro_feed.build(now)
        state.macro = macro_view.to_dict()
        if macro_view.available:
            print(f"macro: {macro_view.risk} (score {macro_view.risk_score:+.0f}) · "
                  f"USD {macro_view.usd_bias:+d} · gold {macro_view.gold_bias:+d}")
        else:
            print(f"macro: unavailable ({len(macro_view.errors)} ticker error(s)) "
                  f"- not used as evidence")

    # --- LEVEL 7: what the closed trades have taught, if anything ------
    learned = memory_bank.learn(state.signals) if cfg.self_learning else {}
    if learned:
        print(f"learning: adjusted {len(learned)} weight(s) from closed trades")

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
            state, tg, cfg, now, cache, news_ctx, session, in_kz,
            macro_view=macro_view, learned=learned)

    # --- Daily Result Review: how yesterday's signals actually did ----
    # Sent before the planning report, so the morning reads in the order a
    # desk works: what happened, then what to do about it.
    if cfg.daily_summary and (args.summary
                              or day_review.due(state, now, cfg.daily_summary_hour)):
        rev = day_review.build(state, now, cfg.daily_summary_hour,
                               memory_bank.summary(state.signals))
        tg.send(notifier.format_daily_review(rev))
        state.last_summary_date = day_review.window(
            now, cfg.daily_summary_hour)[1].date().isoformat()
        print(f"daily review sent: {rev.issued} issued, "
              f"{len(rev.closed)} closed, {rev.total_r:+.2f}R")

    # --- Daily Market Analysis: one planning report each morning ------
    if cfg.daily_report and (args.daily
                             or daily_report.due(state, now, cfg.daily_report_hour)):
        reports = []
        for symbol in cfg.daily_symbols:
            try:
                frames = cache.frames(symbol, list(daily_report.LADDER))
            except market_data.DataError as exc:
                rep = daily_report.SymbolReport(symbol=symbol, error=str(exc)[:110])
                reports.append(rep)
                continue
            reports.append(daily_report.analyse_symbol(
                symbol, frames, cfg,
                bool(news_ctx is not None and getattr(news_ctx, "upcoming", None)),
                macro_view, news_ctx, session))

        risk = daily_report.risk_level([r for r in reports if not r.error], macro_view)
        tg.send(notifier.format_daily_overview(
            macro_view, reports, risk, memory_bank.summary(state.signals)))
        for rep in reports:
            tg.send(notifier.format_daily_symbol(rep))
        tg.send(notifier.format_daily_watchlist(reports, risk))

        state.last_daily_date = now.astimezone(
            daily_report.BANGKOK).date().isoformat()
        ok = sum(1 for r in reports if not r.error)
        print(f"daily analysis sent for {ok}/{len(reports)} symbol(s)")

    # --- chart briefing: the running commentary on the market ---------
    gold = set(cfg.gold_symbols)
    due = (cfg.briefing_minutes > 0
           and state.minutes_since_briefing(now) >= cfg.briefing_minutes)

    # Gold runs on a 15-minute clock while the briefing runs on its own, so
    # the two only coincide on one run in three - which is why gold kept
    # missing from the report entirely. Hold the briefing back until a run
    # that actually carries gold, unless it is so overdue that waiting would
    # cost more than the missing section (gold data down, for instance).
    gold_missing = bool(gold) and not any(v.symbol in gold for v in views)
    overdue = (cfg.briefing_minutes > 0
               and state.minutes_since_briefing(now) >= cfg.briefing_minutes * 2)
    if due and gold_missing and not overdue and not args.brief:
        print("briefing: deferred to the next run that includes gold")
        due = False

    if views and (due or args.brief):
        counts = (state.issued_today(now, symbols=gold), cfg.gold_daily_target,
                  state.issued_today(now, exclude=gold), cfg.pair_daily_target)

        # One message per instrument, not one combined report: each pair is
        # a separate decision and reads better without the others in the way.
        by_symbol: dict = {}
        for v in views:
            by_symbol.setdefault(v.symbol, []).append(v)

        def order(item):
            symbol, group = item
            # gold leads, then whichever symbol is closest to a signal
            return (symbol not in gold, -max(v.steps_passed for v in group))

        # LEVEL 1 leads the round: the global picture before any chart
        if cfg.use_macro:
            tg.send(notifier.format_macro(
                macro_view, memory_bank.summary(state.signals)))

        first = True
        for symbol, group in sorted(by_symbol.items(), key=order):
            tg.send(notifier.format_symbol_report(
                symbol, group,
                counts=counts if first else None,   # daily tally once, not 8x
                primary=symbol in gold))
            first = False

        # the spec's explicit "nothing qualifies right now" statement
        if cfg.no_setup_notice and not any(v.steps_passed >= 11 for v in views):
            tg.send(notifier.format_no_setup(news_ctx, views))
        state.last_briefing_at = now.isoformat(timespec="seconds")
        print(f"briefing sent for {len(by_symbol)} symbol(s)")

    state.save()

    if args.status or cfg.send_status_report:
        tg.send(notifier.format_status(
            rejections, len(state.live()), state.issued_today(now), errors))

    for err in errors:
        print(f"data: {err}")
    if cache.sources:
        print(f"price source: {cache.source_report()}")
    gold_today = state.issued_today(now, symbols=set(cfg.gold_symbols))
    pair_today = state.issued_today(now, exclude=set(cfg.gold_symbols))
    print(f"done: {len(state.live())} live signal(s), "
          f"{state.issued_today(now)} issued today "
          f"(ทอง {gold_today}/{cfg.gold_daily_target}, "
          f"คู่เงิน {pair_today}/{cfg.pair_daily_target}) · {cache.stats()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
