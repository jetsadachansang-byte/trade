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

from . import chart as chart_img
from . import daily as daily_report
from . import data as market_data
from . import macro as macro_feed
from . import memory as memory_bank
from . import news as news_feed
from . import notifier
from . import archive as msg_archive
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


def weekend_key(now: datetime) -> str:
    """Which weekend this moment belongs to, as the Saturday's date.

    Saturday and Sunday are one closed window, not two: the chart stops
    at Friday's close and does not move again until Sunday night, so an
    analysis run on Sunday reads exactly the same candles as one run on
    Saturday. Keying both days to the same date is what makes "once over
    the weekend" mean once rather than twice.

    Empty string while the market is open.
    """
    from datetime import timedelta as _td
    weekday = now.weekday()          # Mon=0 ... Sun=6
    if weekday == 4 and now.hour >= 22:          # Friday after the close
        return (now + _td(days=1)).date().isoformat()
    if weekday == 5:                             # Saturday
        return now.date().isoformat()
    if weekday == 6 and now.hour < 22:           # Sunday before the reopen
        return (now - _td(days=1)).date().isoformat()
    return ""


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
    """Update every live signal against every bar since it was last checked.

    This used to read the newest bar only. A one-minute signal scanned every
    five minutes therefore had four bars a run that nobody ever looked at,
    and a target reached inside one of them was simply never announced -
    the alert was lost, not late. Scans also get throttled, which widens the
    gap further. So each run walks forward through every bar that has
    appeared since the last check and applies the same rules to each.

    Within a single bar the order of the high and the low is unknowable from
    OHLC alone, so the stop is always tested first. That reports the worse
    of the two outcomes, which is the only safe way to be wrong.
    """
    for sig in state.live():
        try:
            # each signal is tracked on the timeframe it was issued from
            df = cache.get(sig.symbol, sig.timeframe)
        except market_data.DataError as exc:
            print(f"track {sig.symbol}: {exc}")
            continue

        bars = _bars_since(df, sig.checked_to)
        if bars.empty:
            continue
        sig.checked_to = str(bars.index[-1])
        is_buy = sig.direction > 0

        for stamp, bar in bars.iterrows():
            if not sig.is_live:
                break
            high, low = float(bar["high"]), float(bar["low"])

            # --- stop loss (checked first: worst case wins) ------------
            if (is_buy and low <= sig.sl) or (not is_buy and high >= sig.sl):
                sig.status = SL_HIT
                _close(sig, now, "โดน SL")
                tg.send(notifier.format_sl(sig, sig.sl))
                break

            # --- trailing stop: move the stop up behind the move -------
            if sig.exit_mode == "trailing" and sig.trail_distance > 0:
                peak = sig.trail_peak or sig.entry
                peak = max(peak, high) if is_buy else min(peak, low)
                if peak != sig.trail_peak:
                    sig.trail_peak = peak
                moved = (peak - sig.trail_distance if is_buy
                         else peak + sig.trail_distance)
                # never widen a stop, and never trail backwards
                if (is_buy and moved > sig.sl) or (not is_buy and moved < sig.sl):
                    old_sl, sig.sl = sig.sl, round(float(moved), 5)
                    tg.send(notifier.format_trail(sig, old_sl, sig.sl))

            # --- take profits, announced once each, in order -----------
            for level, target, already in (
                (1, sig.tp1, sig.tp1_hit), (2, sig.tp2, sig.tp2_hit),
                (3, sig.tp3, sig.tp3_hit)
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
                tg.send(notifier.format_tp(sig, level, target))

        # --- expiry: never reached TP1 within the window ---------------
        if sig.status == ACTIVE:
            age_hours = (now - sig.created_at()).total_seconds() / 3600
            expiry = sig.expiry_hours or cfg.signal_expiry_hours
            if expiry > 0 and age_hours >= expiry:
                sig.status = CANCELLED
                _close(sig, now, f"หมดเวลา {expiry} ชม. โดยไม่ถึง TP1")
                tg.send(notifier.format_cancel(
                    sig, f"เกินเวลา {expiry} ชม. โดยไม่ถึง TP1"))


def _bars_since(df, checked_to: str):
    """Bars newer than the last one already examined.

    With no marker - a signal issued before this existed, or one just sent -
    only the newest bar is taken, so a fresh signal cannot be closed by
    history that happened before it was ever issued.
    """
    if df is None or df.empty:
        return df.iloc[0:0] if df is not None else df
    if not checked_to:
        return df.iloc[-1:]
    newer = df.loc[df.index.astype(str) > checked_to]
    # A long outage can leave hundreds of bars; the recent ones are what
    # a stop or a target would have been hit in.
    return newer.iloc[-500:]


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
    # With no daily quota there is no pace to keep, so the bar simply sits
    # at the floor: every setup the system considers tradable is sent, and
    # the grade on each one says how good it actually is.
    if cfg.unlimited:
        return cfg.min_score_floor
    if not cfg.adaptive_threshold or shortfall <= 0:
        return base
    give = (base - cfg.min_score_floor) * shortfall * prof.pace_weight
    return max(cfg.min_score_floor, base - give)


class ChartSender:
    """Sends the picture that goes with each analysis, if one is configured.

    Holds two pieces of state for the run: whether the quota is spent -
    once it is, sixteen more attempts help nobody - and whether the
    reader has already been told that images are failing, so a broken key
    produces one visible note rather than seventeen.
    """

    def __init__(self, cfg: Settings, tg: notifier.Telegram):
        self.cfg, self.tg = cfg, tg
        self.stopped = ""
        self.told = False
        self.sent = 0

    def send(self, view, now: datetime) -> str:
        """Returns a note to append to the text message, usually empty."""
        symbol = view.symbol
        if not self.cfg.chart_wanted(symbol) or self.stopped:
            return ""
        tf = self.cfg.chart_timeframe
        try:
            image = chart_img.fetch(
                symbol, tf, self.cfg.chart_img_key,
                theme=self.cfg.chart_theme, width=self.cfg.chart_width,
                height=self.cfg.chart_height)
        except chart_img.ChartError as exc:
            reason = str(exc)
            print(f"chart {symbol}: {reason}")
            if chart_img.quota_exhausted(reason):
                self.stopped = reason
                print("chart: quota looks spent - no more images this run")
            if self.told:
                return ""
            self.told = True
            return f"\n🖼 <i>ดึงรูปกราฟไม่สำเร็จรอบนี้ ({notifier._esc(reason[:90])})</i>"

        ok = self.tg.send_photo(
            image, caption=notifier.format_chart_caption(view, tf, now),
            filename=f"{symbol}_{tf}.png")
        if ok:
            self.sent += 1
        return ""


def _week_events(news_ctx, now: datetime, symbols) -> tuple:
    """Next week's calendar for the instruments covered, and any error.

    Forex Factory's "this week" file rolls over on Sunday, so the Sunday
    round reads the week that is about to start rather than the one that
    just finished. Nothing is invented: with no readable calendar the
    briefing says so and offers no news reasoning at all.
    """
    wanted = news_feed.currencies_for(list(symbols))
    try:
        raw = news_feed.fetch()
    except Exception as exc:            # noqa: BLE001 - degraded, not fatal
        # One fallback: the run's own context covers today only, which is
        # better than nothing but must not be passed off as the week.
        if news_ctx is not None and getattr(news_ctx, "available", False):
            partial = [e for e in news_ctx.day_events if e.currency in wanted]
            if partial:
                return (sorted(partial, key=lambda e: e.when),
                        "ดึงปฏิทินทั้งสัปดาห์ไม่ได้ — แสดงเท่าที่มีของวันนี้")
        return [], str(exc)[:120]
    events = [e for e in raw if e.currency in wanted and e.when >= now]
    return sorted(events, key=lambda e: e.when), ""


def build_outlook(symbol: str, cache: market_data.Cache, cfg: Settings,
                  news_ctx, macro_view, session: str, now: datetime):
    """The full trend read on one instrument, or one carrying its error.

    A dead feed on one symbol must not take the other thirteen with it,
    so the failure is carried inside the outlook and reported in that
    pair's own message.
    """
    from . import outlook as outlook_engine
    try:
        frames = cache.frames(symbol, list(daily_report.LADDER))
    except market_data.DataError as exc:
        rep = daily_report.SymbolReport(symbol=symbol, error=str(exc)[:110])
        return outlook_engine.build(rep, {}, news_ctx, now, cfg.swing_bars)
    rep = daily_report.analyse_symbol(
        symbol, frames, cfg,
        bool(news_ctx is not None and getattr(news_ctx, "upcoming", None)),
        macro_view, news_ctx, session)
    return outlook_engine.build(rep, frames, news_ctx, now, cfg.swing_bars)


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
    if cfg.unlimited:
        print("signals: ไม่จำกัดจำนวนต่อวัน — "
              f"เกณฑ์คะแนนยืนที่ {cfg.min_score_floor:.0f} ทุกสไตล์")
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
            # Spread makes a fast entry on the crosses a losing proposition
            # before the trade even starts, so anything below the symbol's
            # minimum entry timeframe is skipped outright. The smaller
            # charts are still read - they just cannot be entered on.
            if not cfg.entry_allowed(symbol, prof):
                continue
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
                # start tracking from the bar the setup formed on, so the
                # next run examines everything after it and nothing before
                checked_to=cand.bar_time,
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


def chart_test(symbol: str, dry_run: bool = False) -> int:
    """Fetch and send one chart, printing exactly what happened.

    The sandbox this was written in cannot reach chart-img at all, so the
    first proof that the key, the endpoint and the symbol mapping are
    right has to come from a real run. This is that check, and it costs
    one image instead of a session's worth.
    """
    cfg = Settings()
    print(f"chart test: {symbol} -> {chart_img.tv_symbol(symbol)} "
          f"@ {cfg.chart_timeframe}")
    if not cfg.chart_img_key:
        print("chart test: CHART_IMG_KEY ยังไม่ได้ตั้ง (ใส่ใน GitHub Secrets)")
        return 1
    try:
        image = chart_img.fetch(symbol, cfg.chart_timeframe, cfg.chart_img_key,
                                theme=cfg.chart_theme, width=cfg.chart_width,
                                height=cfg.chart_height)
    except chart_img.ChartError as exc:
        print(f"chart test: FAILED - {exc}")
        return 1
    print(f"chart test: ok, {len(image):,} bytes")
    tg = notifier.Telegram(cfg.telegram_token, cfg.telegram_chat_id, dry_run)
    sent = tg.send_photo(
        image,
        caption=f"🧪 <b>ทดสอบรูปกราฟ</b> · {symbol} · {cfg.chart_timeframe}\n"
                f"<i>{chart_img.tv_symbol(symbol)} · chart-img.com</i>",
        filename=f"{symbol}.png")
    print("chart test: sent to Telegram" if sent else
          "chart test: image fetched but Telegram refused it")
    return 0 if sent else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="print messages instead of sending to Telegram")
    parser.add_argument("--status", action="store_true",
                        help="always send the status report this run")
    parser.add_argument("--daily", action="store_true",
                        help="send the daily market analysis now, ignoring the clock")
    parser.add_argument("--summary", action="store_true",
                        help="send the daily result review now, ignoring the clock")
    parser.add_argument("--ignore-hours", action="store_true",
                        help="skip market-hours and kill-zone checks (for testing)")
    parser.add_argument("--chart-test", metavar="SYMBOL", nargs="?",
                        const="XAUUSD",
                        help="fetch one chart image and send it, then stop "
                             "(checks the chart-img key and symbol mapping)")
    args = parser.parse_args(argv)

    if args.chart_test:
        return chart_test(args.chart_test, args.dry_run)

    cfg = Settings()
    problems = cfg.validate()
    if problems and not args.dry_run:
        for problem in problems:
            print(f"config: {problem}")
        return 1

    now = datetime.now(timezone.utc)
    archive = msg_archive.Archive.load() if cfg.message_archive else None
    tg = notifier.Telegram(cfg.telegram_token, cfg.telegram_chat_id,
                           args.dry_run, archive=archive, now=now)
    state = State.load()
    charts = ChartSender(cfg, tg)
    if cfg.charts_on:
        print(f"charts: on · {cfg.chart_timeframe} · "
              + (", ".join(cfg.chart_symbols) if cfg.chart_symbols
                 else "ทุกตัวที่วิเคราะห์"))

    # --- answer anything typed into the chat --------------------------
    # Done before anything else so a search is answered on the run it was
    # noticed, and against an archive that does not yet include this run's
    # own messages - searching for what was just sent is not what was asked.
    if archive is not None:
        queries, next_offset = tg.poll(state.last_update_id)
        state.last_update_id = next_offset
        for query in queries[-3:]:          # a burst of typing is not a queue
            matches, total = archive.search(query, cfg.search_results)
            tg.send(notifier.format_search(query, matches, total,
                                           cfg.archive_days), archive=False)
            print(f"search {query!r}: {total} match(es)")

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

    # Which weekend this run falls in, empty while the market is open.
    # Everything that repeats on a clock - the analysis and the plan board -
    # goes out once for the whole closed window rather than on its usual
    # cadence, because nothing underneath either of them can change.
    charts_live = market_open(now) or args.ignore_hours
    weekend = "" if charts_live else weekend_key(now)
    # The one weekend round lands on Sunday: it is a look at the week
    # about to start, not a post-mortem of the one that just ended, and a
    # plan read on Saturday morning has two days to go stale before it is
    # used. Everything closed-market waits for that slot.
    weekend_slot = bool(weekend) and (
        now.astimezone(daily_report.BANGKOK).weekday()
        == cfg.weekend_report_weekday)

    # Whether this run is one that loads gold at all. Read before the scan,
    # because the scan stamps the clock it is derived from - afterwards the
    # answer is always "no", which would defer the gold outlook forever.
    gold_fresh = (cfg.gold_scan_minutes <= 0
                  or state.minutes_since_gold_scan(now) >= cfg.gold_scan_minutes)

    rejections, errors, views = [], [], []
    scanned = False
    if not cfg.signals:
        # The bot analyses the market; it does not call entries any more.
        # Positions already issued are still tracked to their conclusion
        # above - closing the door does not mean walking away from what is
        # already through it - but nothing new is opened.
        print("signals: off (SIGNALS=false) - analysis only, no entry tickets")
    elif not (market_open(now) or args.ignore_hours):
        print("market closed - no scan")
    elif not (in_kz or args.ignore_hours):
        print(f"outside kill zones (UTC hour {now.hour}) - no scan")
    elif news_ctx is not None and news_ctx.blocking and not args.ignore_hours:
        print(f"news blackout: {news_ctx.blocking_reason} - no scan")
    else:
        scanned = True
        rejections, errors, views = scan(
            state, tg, cfg, now, cache, news_ctx, session, in_kz,
            macro_view=macro_view, learned=learned)

    # --- Today's economic calendar, once each morning ------------------
    # Straight from the same Forex Factory feed the signal engine already
    # reads, so the agenda and the no-trade windows cannot disagree with
    # each other. Fetched on its own only when news scoring is switched
    # off - the agenda was asked for explicitly and should not vanish
    # because of an unrelated setting.
    local_now = now.astimezone(daily_report.BANGKOK)
    if (cfg.news_agenda and local_now.hour >= cfg.news_agenda_hour
            and state.last_news_date != local_now.date().isoformat()):
        if news_ctx is not None and news_ctx.available:
            events, news_error = news_ctx.day_events, ""
        else:
            events, news_error = news_feed.agenda_for(now)
        tg.send(notifier.format_news_agenda(
            events, news_error, cfg.news_pre_min, cfg.news_post_min))
        state.last_news_date = local_now.date().isoformat()
        high = sum(1 for e in events if e.high)
        print(f"news agenda sent: {len(events)} event(s), {high} high impact"
              + (f" · error: {news_error[:60]}" if news_error else ""))

    # --- The plan status board, hourly --------------------------------
    # Status only, no reasoning: this answers "where does everything
    # stand" and nothing else. Reads the stored signals, so it works even
    # on a run where the market data never loaded.
    #
    # Over the weekend it goes out once instead of hourly. Tracking is not
    # running - there are no bars to check - so every hourly board from
    # Friday's close to Monday's open would be a byte-for-byte copy of the
    # one before it. One snapshot of what is being carried over the
    # weekend is the whole of the information.
    status_due = day_review.status_due(state, now, cfg.plan_status_hours)
    if weekend:
        status_due = weekend_slot and state.last_weekend_status != weekend
    if status_due:
        plans = day_review.board(state, now, cfg.plan_status_window)
        tg.send(notifier.format_plan_status(plans, now, closed=bool(weekend)))
        state.last_status_at = now.isoformat(timespec="seconds")
        if weekend:
            state.last_weekend_status = weekend
        print(f"plan status{' (weekend, once)' if weekend else ''}: "
              f"{len(plans.running)} running, "
              f"{len(plans.won)} won, {len(plans.lost)} lost, "
              f"{len(plans.cancelled)} cancelled")

    # --- The week, closed off on Sunday morning ------------------------
    if cfg.weekly_summary and day_review.weekly_due(
            state, now, cfg.weekly_summary_hour):
        week = day_review.weekly(state, now, cfg.weekly_summary_hour)
        tg.send(notifier.format_weekly(week, memory_bank.summary(state.signals)))
        state.last_weekly_date = now.astimezone(
            daily_report.BANGKOK).date().isoformat()
        print(f"weekly sent: {week.issued} issued, {week.won}W/{week.lost}L, "
              f"{week.tp_total} TP(s), {week.total_r:+.2f}R")

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

    # --- Market Analysis: one planning report per trading session -----
    # A plan drawn before Tokyo cannot explain a London breakout eight
    # hours later, so the report runs once per session rather than once a
    # day, and says which session it belongs to.
    slot = daily_report.due(state, now, cfg.daily_report_hours)
    if cfg.daily_report and args.daily and slot is None:
        slot = (daily_report.slot_for(now, cfg.daily_report_hours)
                or min(cfg.daily_report_hours or [6]))
    # Sessions run as normal on a trading day. Over the weekend the last
    # candle simply stops moving, so repeating the analysis every session
    # would send the same reading three times a day as though it were
    # news. One weekend edition goes out instead, off Friday's close.
    weekend_due = weekend_slot and state.last_weekend_date != weekend
    if slot is not None and not charts_live and not weekend_due:
        print("market closed - weekend analysis already sent" if weekend_slot
              else "market closed - weekend round goes out on Sunday")
        slot = None
    if cfg.daily_report and slot is not None:
        # One pair per message, every pair, nothing summarised away. A
        # table with fourteen instruments on it answers "what is the
        # market doing" and nothing else; this answers "what is *this*
        # pair doing, where are its levels, and what happens if it gets
        # there" - which is the question a plan is actually made from.
        label = daily_report.SESSIONS.get(slot, ("", ""))[0]
        symbols = cfg.outlook_universe() if cfg.outlook else cfg.daily_symbols
        ok = 0
        if cfg.outlook and weekend:
            # Sunday: the week ahead - what the calendar holds and where
            # each instrument stands going into it.
            week_events, week_error = _week_events(news_ctx, now, symbols)
            tg.send(notifier.format_week_ahead(
                week_events, week_error, symbols, macro_view, now))
        if cfg.outlook:
            if not weekend:
                tg.send(notifier.format_outlook_header(
                    label, slot, symbols, macro_view, now))
            for symbol in symbols:
                view = build_outlook(symbol, cache, cfg, news_ctx, macro_view,
                                     session, now)
                # Crypto never closes, so its weekend price is live and
                # must not be labelled as Friday's close.
                shut = bool(weekend) and not market_data.always_open(symbol)
                note = charts.send(view, now)
                tg.send(notifier.format_outlook(view, now, closed=shut) + note)
                ok += 0 if view.error else 1
                if cfg.is_gold(symbol):
                    state.last_gold_outlook_at = now.isoformat(timespec="seconds")
        else:
            # OUTLOOK=false falls back to the compact two-message report:
            # one line per pair instead of a page each.
            reports = []
            for symbol in symbols:
                try:
                    frames = cache.frames(symbol, list(daily_report.LADDER))
                except market_data.DataError as exc:
                    reports.append(daily_report.SymbolReport(
                        symbol=symbol, error=str(exc)[:110]))
                    continue
                reports.append(daily_report.analyse_symbol(
                    symbol, frames, cfg,
                    bool(news_ctx is not None and getattr(news_ctx, "upcoming", None)),
                    macro_view, news_ctx, session))
            risk = daily_report.risk_level(
                [r for r in reports if not r.error], macro_view)
            gold = set(cfg.gold_symbols)
            counts = (state.issued_today(now, symbols=gold), cfg.gold_daily_target,
                      state.issued_today(now, exclude=gold), cfg.pair_daily_target)
            tg.send(notifier.format_session_overview(
                macro_view, reports, risk, slot, counts))
            plans = notifier.format_session_plans(reports)
            if plans:
                tg.send(plans)
            ok = sum(1 for r in reports if not r.error)

        state.last_daily_slot = daily_report.slot_key(now, slot)
        state.last_daily_date = now.astimezone(
            daily_report.BANGKOK).date().isoformat()
        if weekend:
            state.last_weekend_date = weekend
        print(f"{'weekend' if weekend else 'session'} analysis "
              f"({label} {slot:02d}:00) sent for {ok}/{len(symbols)} symbol(s)")

    # --- Gold on its own hourly clock ---------------------------------
    # Gold is the instrument that moves fastest and is watched closest, so
    # it gets a full outlook every hour rather than three times a day. It
    # is held to a run that already loaded gold: its data comes from a
    # metered feed, and re-fetching six series on a run that did not scan
    # gold would spend the daily budget on nothing new. The wait is at
    # most one scan interval, and an overdue outlook goes anyway.
    elif (cfg.outlook and cfg.outlook_gold_hours > 0 and cfg.gold_symbols
            and charts_live):
        since = state.minutes_since_gold_outlook(now)
        due = since >= cfg.outlook_gold_hours * 60
        overdue = since >= cfg.outlook_gold_hours * 90
        if due and not (gold_fresh or overdue):
            print("gold outlook: deferred to the next run that loads gold")
        elif due:
            for symbol in cfg.gold_symbols:
                view = build_outlook(symbol, cache, cfg, news_ctx, macro_view,
                                     session, now)
                note = charts.send(view, now)
                tg.send(notifier.format_outlook(view, now) + note)
                print(f"gold outlook sent: {symbol}"
                      + (f" (error: {view.error[:60]})" if view.error else ""))
            state.last_gold_outlook_at = now.isoformat(timespec="seconds")

    # --- Market pulse: where every pair stands, every few hours -------
    # Costs nothing: the scan already built a view of every symbol this
    # run, it was simply never sent anywhere. Skipped when a session
    # report has just gone out, since that answers the same question in
    # more detail and two messages a minute apart is noise.
    pulse_due = (cfg.pulse_hours > 0
                 and state.minutes_since_pulse(now) >= cfg.pulse_hours * 60)

    # Gold runs on a fifteen-minute clock of its own to stay inside the
    # Twelve Data request budget, so only one scan in three carries it. A
    # three-hourly check landing on one of the other two would report every
    # pair except the primary instrument. Hold the pulse back until a run
    # that actually has gold in it - at most a fifteen-minute wait - unless
    # it is so overdue that waiting costs more than the missing row, which
    # is what a gold feed outage would look like.
    gold_syms = set(cfg.gold_symbols)
    gold_missing = bool(gold_syms) and not any(v.symbol in gold_syms for v in views)
    pulse_overdue = (cfg.pulse_hours > 0 and state.minutes_since_pulse(now)
                     >= cfg.pulse_hours * 90)          # 1.5x the interval
    if pulse_due and scanned and gold_missing and not pulse_overdue:
        print("pulse: deferred to the next run that includes gold")
        pulse_due = False
    # Sent whenever a scan actually ran, even if it produced nothing: an
    # empty result means the feed is down, and going silent about that is
    # exactly how a broken bot looks like a quiet market.
    if pulse_due and scanned and slot is None:
        tg.send(notifier.format_pulse(
            views, macro_view, session, len(state.live()),
            state.issued_today(now), primary=tuple(cfg.gold_symbols)))
        state.last_pulse_at = now.isoformat(timespec="seconds")
        ready = sum(1 for v in views if v.steps_passed >= 11)
        print(f"pulse sent: {len({v.symbol for v in views})} symbol(s), "
              f"{ready} ready to enter")
    elif pulse_due and slot is not None:
        # the session report covers it; move the clock on so the pulse does
        # not fire again a minute later
        state.last_pulse_at = now.isoformat(timespec="seconds")

    # The rolling chart briefing used to run on its own clock and was the
    # only thing left that could fire on a scan cadence. Reports now belong
    # to sessions, so it is retired rather than left switched off: a stale
    # BRIEFING_MINUTES repository variable must not be able to bring the
    # five-minute flood back.

    state.save()
    if archive is not None:
        archive.save(keep_days=cfg.archive_days)

    # Status goes out only when explicitly asked for. It used to honour a
    # repository variable, which meant one setting turned every scan into a
    # message.
    if args.status:
        tg.send(notifier.format_status(
            rejections, len(state.live()), state.issued_today(now), errors))

    for err in errors:
        print(f"data: {err}")
    if cache.sources:
        print(f"price source: {cache.source_report()}")
    gold_today = state.issued_today(now, symbols=set(cfg.gold_symbols))
    pair_today = state.issued_today(now, exclude=set(cfg.gold_symbols))
    quota = ("ไม่จำกัด" if cfg.unlimited else
             f"ทอง {gold_today}/{cfg.gold_daily_target}, "
             f"คู่เงิน {pair_today}/{cfg.pair_daily_target}")
    print(f"done: {len(state.live())} live signal(s), "
          f"{state.issued_today(now)} issued today "
          f"(ทอง {gold_today}, คู่เงิน {pair_today} · {quota}) · {cache.stats()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
