"""A searchable record of what the bot has said.

Telegram keeps the messages but is a poor place to look things up: a
signal from Tuesday is a thousand lines of scrolling away, and its own
search matches raw text without knowing which instrument a message was
about. So every message the bot sends is also filed here, with the symbols
and the kind of message worked out from the text itself.

Deriving those from the text rather than from the call site is deliberate.
A new kind of message added later is archived correctly without anyone
remembering to register it, which is the failure mode that leaves an
archive quietly incomplete.

The file is separate from the signal state because it changes a handful of
times a day rather than on every scan, so it is committed rarely and the
repository does not carry a new copy every five minutes.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ARCHIVE_FILE = Path(__file__).resolve().parent.parent / "message_log.json"

# Bounded on purpose: this lives in git, and an unbounded log would grow
# the repository forever.
MAX_ENTRIES = 200
MAX_TEXT = 700

_TAGS = re.compile(r"<[^>]+>")
_SYMBOL = re.compile(r"\b(XAU(?:USD)?|[A-Z]{3}(?:USD|JPY|GBP|CHF|CAD|AUD|NZD))\b")

# What each message is, read from its opening lines only. Matching against
# the whole body looked simpler and was wrong: a full signal ticket names
# a stop loss, a take profit and an expiry somewhere in its risks, so it
# matched half the other kinds before reaching its own.
KINDS = (
    ("tp", ("TP1 Hit", "TP2 Hit", "TP3 Hit")),
    ("sl", ("Stop Loss Hit", "SL Hit", "โดน SL")),
    ("trail", ("เลื่อน Stop Loss", "เลื่อน SL", "Trailing")),
    ("cancel", ("Signal Cancelled", "ยกเลิกสัญญาณ")),
    ("signal", ("สินทรัพย์:", "Scalp", "Day Trade", "Run Trend", "Intraday")),
    ("news", ("ข่าวเศรษฐกิจวันนี้",)),
    ("review", ("สรุปผลรอบ", "สรุปผลประจำวัน")),
    ("session", ("คู่ที่มีแผนรอบนี้", "รอบเช้า", "รอบบ่าย", "รอบค่ำ",
                 "บทวิเคราะห์ตลาด")),
    ("pulse", ("เช็คตลาด",)),
)

THAI_KIND = {
    "signal": "สัญญาณเข้า", "tp": "ถึง TP", "sl": "โดน SL",
    "trail": "เลื่อน SL", "cancel": "ยกเลิก", "news": "ข่าว",
    "review": "สรุปผล", "session": "บทวิเคราะห์", "pulse": "เช็คตลาด",
    "other": "อื่น ๆ",
}


def plain(text: str) -> str:
    """Message text with the HTML markup taken out."""
    return _TAGS.sub("", text).replace("&lt;", "<").replace("&gt;", ">") \
                              .replace("&amp;", "&")


HEAD_LINES = 4


def classify(text: str) -> str:
    """The kind of message, from its heading rather than its whole body."""
    head = "\n".join(text.split("\n")[:HEAD_LINES])
    for kind, markers in KINDS:
        if any(m in head for m in markers):
            return kind
    return "other"


def symbols_in(text: str) -> list:
    """Instruments named in the message, in first-seen order."""
    seen: list = []
    for match in _SYMBOL.findall(text):
        name = "XAUUSD" if match == "XAU" else match
        if name not in seen:
            seen.append(name)
    return seen


@dataclass
class Entry:
    at: str                    # ISO-8601 UTC
    kind: str
    title: str                 # first meaningful line
    symbols: list = field(default_factory=list)
    text: str = ""             # plain text, truncated

    def when(self) -> datetime:
        return datetime.fromisoformat(self.at)


def make(text: str, now: datetime) -> Entry:
    body = plain(text)
    lines = [ln.strip() for ln in body.split("\n") if ln.strip()]
    return Entry(at=now.isoformat(timespec="seconds"), kind=classify(body),
                 title=lines[0][:90] if lines else "",
                 symbols=symbols_in(body), text=body[:MAX_TEXT])


class Archive:
    """The stored messages, loaded and saved as one JSON document."""

    def __init__(self, entries=None):
        self.entries: list = list(entries or [])

    @classmethod
    def load(cls, path: Path = ARCHIVE_FILE) -> "Archive":
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        out = []
        for item in raw.get("messages", []):
            try:
                out.append(Entry(**item))
            except TypeError:
                continue           # a field this version does not know
        return cls(out)

    def save(self, path: Path = ARCHIVE_FILE, keep_days: int = 30) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
        kept = [e for e in self.entries if e.when() >= cutoff][-MAX_ENTRIES:]
        path.write_text(json.dumps(
            {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "messages": [asdict(e) for e in kept]},
            ensure_ascii=False, indent=1), encoding="utf-8")

    def add(self, text: str, now: datetime) -> Entry:
        entry = make(text, now)
        self.entries.append(entry)
        return entry

    # --- searching ----------------------------------------------------
    def search(self, query: str, limit: int = 5) -> tuple:
        """(matches, total) - newest first.

        Words are ANDed, because two words usually means narrowing rather
        than widening. A symbol name matches the message's symbol list as
        well as its text, so "ทอง" and "XAUUSD" find the same messages.
        """
        words = [w for w in query.lower().split() if w]
        if not words:
            return [], 0
        hits = [e for e in self.entries if _matches(e, words)]
        hits.sort(key=lambda e: e.at, reverse=True)
        return hits[:limit], len(hits)


# Thai words a reader would actually type, mapped onto what the messages
# contain. Without this "ทอง" finds nothing, because the messages say
# XAUUSD.
ALIASES = {
    "ทอง": ("xauusd", "xau"),
    "gold": ("xauusd", "xau"),
    "ยูโร": ("eurusd", "eur"),
    "ปอนด์": ("gbpusd", "gbp"),
    "เยน": ("jpy",),
    "ซื้อ": ("buy",),
    "ขาย": ("sell",),
    "กำไร": ("tp1 hit", "tp2 hit", "tp3 hit"),
    "ขาดทุน": ("โดน sl", "sl hit"),
    "ข่าว": ("ข่าวเศรษฐกิจ",),
}


def _matches(entry: Entry, words) -> bool:
    hay = (entry.text + " " + " ".join(entry.symbols) + " "
           + entry.kind + " " + THAI_KIND.get(entry.kind, "")).lower()
    for word in words:
        options = (word,) + ALIASES.get(word, ())
        if not any(opt in hay for opt in options):
            return False
    return True


def merge(mine: dict, theirs: dict) -> dict:
    """Combine two archives without either losing messages.

    Two scans can each send something before either has saved, so this is
    a union rather than one file replacing the other. Identity is the
    timestamp plus the title, which is as close to a message id as a log
    written by two processes can get.
    """
    by_key: dict = {}
    for item in theirs.get("messages", []) + mine.get("messages", []):
        by_key[(item.get("at"), item.get("title"))] = item
    ordered = sorted(by_key.values(), key=lambda m: m.get("at") or "")
    return {"updated": max(mine.get("updated", ""), theirs.get("updated", "")),
            "messages": ordered[-MAX_ENTRIES:]}
