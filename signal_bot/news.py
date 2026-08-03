"""Economic calendar and news context.

Reads the Forex Factory weekly calendar feed, which is public, free and
verifiable. Everything here is derived from that feed - nothing is
invented.

The distinction that matters: when the feed cannot be reached the module
reports `available=False` and the rest of the system must say so plainly
rather than reasoning about news it never saw. A signal is still allowed
in that state, but it may not cite news as support.

Sentiment is computed only from events that already have an `actual`
value, by comparing it with the forecast. No prediction, no narrative.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

BULLISH, BEARISH, NEUTRAL = "bullish", "bearish", "neutral"

# Currencies whose releases matter to the instruments we analyse.
WATCHED = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD"}

# For these releases a higher-than-forecast reading is positive for the
# home currency (hot inflation and strong growth support the currency).
HIGHER_IS_STRONG = (
    "cpi", "ppi", "pce", "gdp", "retail sales", "pmi", "nfp",
    "non-farm", "payroll", "interest rate", "rate decision",
    "employment change", "durable goods", "ism", "confidence",
    "trade balance", "industrial production",
)
# For these a higher reading is negative for the home currency.
HIGHER_IS_WEAK = (
    "unemployment rate", "jobless claims", "unemployment claims",
    "continuing claims",
)

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass
class Event:
    """One calendar entry."""
    title: str
    currency: str
    when: datetime
    impact: str                    # "High" / "Medium" / "Low"
    forecast: str = ""
    previous: str = ""
    actual: str = ""

    @property
    def high(self) -> bool:
        return self.impact.lower() == "high"

    def minutes_from(self, now: datetime) -> float:
        """Signed minutes: negative before the release, positive after."""
        return (now - self.when).total_seconds() / 60.0


@dataclass
class NewsContext:
    """Everything the rest of the system needs to know about news."""
    available: bool = False
    error: str = ""
    blocking: bool = False              # inside the hard no-trade window
    blocking_reason: str = ""
    upcoming: list = field(default_factory=list)   # high impact, soon
    recent: list = field(default_factory=list)     # high impact, just released
    sentiment: dict = field(default_factory=dict)  # currency -> bullish/bearish/neutral
    notes: list = field(default_factory=list)      # human-readable findings
    score: float = 50.0                            # 0-100 for the score engine

    def verified(self) -> bool:
        """True only when real calendar data backed this context."""
        return self.available

    def for_currency(self, currency: str) -> str:
        return self.sentiment.get(currency, NEUTRAL)

    def bias_for(self, symbol: str) -> tuple:
        """(direction, explanation) that news implies for a symbol.

        Gold trades inversely to the dollar; an FX pair follows its base
        currency against its quote. Returns direction 0 when news says
        nothing usable.
        """
        if not self.available:
            return 0, "ไม่สามารถยืนยันข้อมูลข่าวล่าสุดได้"

        if symbol == "XAUUSD":
            usd = self.for_currency("USD")
            if usd == BULLISH:
                return -1, "USD แข็งจากข่าว → กดดันทอง"
            if usd == BEARISH:
                return 1, "USD อ่อนจากข่าว → หนุนทอง"
            return 0, "ข่าวยังไม่ให้ทิศทางชัดกับทอง"

        base, quote = symbol[:3], symbol[3:]
        sb, sq = self.for_currency(base), self.for_currency(quote)
        if sb == BULLISH and sq != BULLISH:
            return 1, f"{base} แข็งจากข่าว"
        if sb == BEARISH and sq != BEARISH:
            return -1, f"{base} อ่อนจากข่าว"
        if sq == BULLISH and sb != BULLISH:
            return -1, f"{quote} แข็งจากข่าว"
        if sq == BEARISH and sb != BEARISH:
            return 1, f"{quote} อ่อนจากข่าว"
        return 0, "ข่าวยังไม่ให้ทิศทางชัดกับคู่นี้"


def _parse_number(text: str):
    """Pull the leading number out of a calendar value like '0.3%' or '-1.2K'."""
    if not text:
        return None
    match = _NUM.search(text.replace(",", ""))
    return float(match.group()) if match else None


def _direction_for(event: Event):
    """Does this release beat or miss, and what does that mean for its currency?

    Returns +1 (currency-positive), -1 (currency-negative) or 0 when the
    reading cannot be judged - no actual value, no forecast, or a release
    whose interpretation is not encoded here.
    """
    actual = _parse_number(event.actual)
    forecast = _parse_number(event.forecast)
    if actual is None or forecast is None or actual == forecast:
        return 0

    title = event.title.lower()
    if any(word in title for word in HIGHER_IS_WEAK):
        polarity = -1
    elif any(word in title for word in HIGHER_IS_STRONG):
        polarity = 1
    else:
        return 0                       # unknown release: stay silent
    return polarity if actual > forecast else -polarity


def fetch(timeout: int = 15) -> list:
    """Load this week's calendar. Raises on any failure."""
    resp = requests.get(
        FF_URL, timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0 (compatible; CapitalGuard/1.0)"},
    )
    resp.raise_for_status()
    events = []
    for row in resp.json():
        currency = (row.get("country") or "").upper()
        if currency not in WATCHED:
            continue
        raw_date = row.get("date") or ""
        try:
            when = datetime.fromisoformat(raw_date).astimezone(timezone.utc)
        except ValueError:
            continue
        events.append(Event(
            title=row.get("title") or "?",
            currency=currency,
            when=when,
            impact=row.get("impact") or "",
            forecast=row.get("forecast") or "",
            previous=row.get("previous") or "",
            actual=row.get("actual") or "",
        ))
    return events


def build(now: datetime, currencies: set, pre_minutes: int = 45,
          post_minutes: int = 45, soft_minutes: int = 120,
          recent_hours: int = 12) -> NewsContext:
    """Assemble the news context for this moment.

    `pre/post_minutes` is the hard window around a high-impact release
    where no signal may be issued. `soft_minutes` is the wider window
    that only reduces the score. `recent_hours` is how far back released
    figures still colour sentiment.
    """
    ctx = NewsContext()
    try:
        events = fetch()
        ctx.available = True
    except Exception as exc:            # noqa: BLE001 - degraded, not fatal
        ctx.error = str(exc)
        ctx.score = 50.0                # neutral: absence of data is not a signal
        ctx.notes.append("ไม่สามารถยืนยันข้อมูลข่าวล่าสุดได้ — จะไม่ใช้ข่าวประกอบการตัดสินใจ")
        return ctx

    relevant = [e for e in events if e.currency in currencies]
    tally: dict = {}

    for event in relevant:
        offset = event.minutes_from(now)     # <0 before, >0 after

        if event.high:
            # hard block: just before or just after the release
            if -pre_minutes <= offset <= post_minutes:
                ctx.blocking = True
                ctx.blocking_reason = (
                    f"{event.title} ({event.currency}) "
                    f"{'อีก ' + str(int(-offset)) + ' นาที' if offset < 0 else 'ผ่านมา ' + str(int(offset)) + ' นาที'}")
            # soft window: coming up but not yet blocking
            elif -soft_minutes <= offset < -pre_minutes:
                ctx.upcoming.append(event)

        # released figures still shape sentiment for a while
        if 0 <= offset <= recent_hours * 60 and event.actual:
            direction = _direction_for(event)
            if direction:
                tally.setdefault(event.currency, []).append((event, direction))
            if event.high:
                ctx.recent.append(event)

    # --- sentiment per currency, from actual vs forecast only ---------
    for currency, rows in tally.items():
        net = sum(direction for _, direction in rows)
        if net > 0:
            ctx.sentiment[currency] = BULLISH
        elif net < 0:
            ctx.sentiment[currency] = BEARISH
        else:
            ctx.sentiment[currency] = NEUTRAL
        for event, direction in rows[:3]:
            arrow = "สูงกว่าคาด" if direction * (1 if any(
                w in event.title.lower() for w in HIGHER_IS_STRONG) else -1) > 0 else "ต่ำกว่าคาด"
            ctx.notes.append(
                f"{event.currency} {event.title}: {event.actual} "
                f"(คาด {event.forecast}) → {arrow}")

    # --- score ---------------------------------------------------------
    if ctx.blocking:
        ctx.score = 0.0
    elif ctx.upcoming:
        soonest = min(abs(e.minutes_from(now)) for e in ctx.upcoming)
        ctx.score = 40.0 if soonest <= 60 else 65.0
        names = ", ".join(f"{e.title} ({e.currency})" for e in ctx.upcoming[:3])
        ctx.notes.append(f"มีข่าวแรงใน {int(soonest)} นาที: {names}")
    else:
        ctx.score = 100.0
        ctx.notes.append("ไม่มีข่าว impact สูงในกรอบเวลาใกล้เคียง")

    return ctx


def currencies_for(symbols) -> set:
    """Every currency touched by the symbols under analysis."""
    out = set()
    for symbol in symbols:
        if symbol == "XAUUSD":
            out.add("USD")
            continue
        out.add(symbol[:3])
        out.add(symbol[3:])
    return out & WATCHED
