"""Telegram delivery for the signal bot."""
from __future__ import annotations

import html
from datetime import datetime, timezone

import requests

API = "https://api.telegram.org/bot{token}/{method}"


class Telegram:
    """Minimal Telegram Bot API client."""

    def __init__(self, token: str, chat_id: str, dry_run: bool = False):
        self.token = token
        self.chat_id = chat_id
        self.dry_run = dry_run or not (token and chat_id)

    def send(self, text: str) -> bool:
        """Send a message, splitting it if Telegram would reject the length.

        Telegram caps a message at 4096 characters and answers anything
        longer with "message is too long" - which is how the whole chart
        briefing went missing. Splitting happens on line boundaries so no
        HTML tag is ever cut in half.
        """
        chunks = _split(text)
        return all(self._send_one(part) for part in chunks)

    def _send_one(self, text: str) -> bool:
        """Send one HTML message. Prints instead of sending in dry-run mode."""
        if self.dry_run:
            print("--- TELEGRAM (dry run) ---")
            print(text)
            return True

        resp = requests.post(
            API.format(token=self.token, method="sendMessage"), timeout=20,
            json={"chat_id": self.chat_id, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
        )
        if resp.ok:
            return True

        # Telegram returns a helpful description - surface it in the log
        try:
            detail = resp.json().get("description", resp.text)
        except ValueError:
            detail = resp.text
        print(f"Telegram error {resp.status_code}: {detail}")
        if resp.status_code == 401:
            print("  -> bot token ผิดหรือถูก revoke")
        elif resp.status_code == 400:
            print("  -> chat id ผิด หรือยังไม่เคยกด Start คุยกับบอท")
        elif resp.status_code == 403:
            print("  -> บอทถูกบล็อก หรือถูกเตะออกจากกลุ่ม")
        return False


MAX_LEN = 4000            # Telegram's hard limit is 4096; leave headroom


def _split(text: str, limit: int = MAX_LEN) -> list:
    """Break a message into Telegram-sized parts on line boundaries.

    A single line longer than the limit is hard-split as a last resort;
    none of the formatters produce one, but a long error string could.
    """
    if len(text) <= limit:
        return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        if not current:
            current = line
        elif len(current) + 1 + len(line) <= limit:
            current += "\n" + line
        else:
            parts.append(current)
            current = line
    if current:
        parts.append(current)
    return parts


def _esc(value) -> str:
    """Escape user-facing text for Telegram HTML mode."""
    return html.escape(str(value), quote=False)


def format_section(horizon: str, count: int) -> str:
    """The banner that opens a group of signals.

    Short-term and long-hold setups are announced under separate banners
    because they are managed completely differently - a run-trend position
    that gets scalped out at TP1 was never a run-trend position.
    """
    if horizon == "long":
        return ("🚀 <b>สัญญาณสายถือยาว / Run Trend</b>\n"
                f"<i>{count} สัญญาณ · ถือข้ามวันถึงสัปดาห์</i>\n"
                "แยกส่วนจากไม้สั้น — ปิดบางส่วนที่ TP1 แล้วปล่อยที่เหลือวิ่ง\n"
                "━━━━━━━━━━━━━━")
    return ("⚡ <b>สัญญาณสายเก็บสั้น / Day Trade</b>\n"
            f"<i>{count} สัญญาณ · ปิดภายในวัน</i>\n"
            "ตั้ง SL/TP ทันทีที่เข้า และเลื่อน SL เป็น BE เมื่อถึง TP1\n"
            "━━━━━━━━━━━━━━")


def format_signal(cand, signal_id: int, prof=None) -> str:
    """The full signal message."""
    arrow = "📈" if cand.direction > 0 else "📉"
    head = (f"{prof.emoji} <b>{_esc(prof.label)}</b> · TF {_esc(cand.timeframe)}"
            if prof else f"TF {_esc(cand.timeframe)}")
    # repeat the section on the signal itself: messages get forwarded and
    # read on their own, and holding a swing trade like a scalp is costly
    if prof is not None:
        head += ("\n🚀 <i>สายถือยาว (Run Trend)</i>" if prof.is_long_hold
                 else "\n⚡ <i>สายเก็บสั้น (Day Trade)</i>")
    lines = [
        head,
        f"📊 <b>สินทรัพย์: {_esc(cand.symbol)}</b>",
        f"{arrow} <b>ประเภท: {cand.side}</b>",
        "━━━━━━━━━━━━━━",
        f"🎯 ราคาเข้า (Entry Zone): <b>{cand.entry_low} – {cand.entry_high}</b>",
        f"🛑 Stop Loss: <b>{cand.sl}</b>",
        f"🎯 Take Profit 1: {cand.tp1}",
        f"🎯 Take Profit 2: {cand.tp2}",
        f"🎯 Take Profit 3: {cand.tp3}",
        f"📉 Risk : Reward = 1 : {cand.rr:.1f}",
        f"⭐ Confidence Score: <b>{cand.score:.0f}%</b>  "
        f"· เกรด <b>{_esc(getattr(cand, 'grade', '-'))}</b>",
        f"💰 {_esc(_GRADE_ADVICE.get(getattr(cand, 'grade', ''), ''))}",
        f"⏱ ระยะเวลาถือที่คาด: {_esc(cand.hold_time or '-')}",
        "━━━━━━━━━━━━━━",
        "🧠 <b>เหตุผลในการวิเคราะห์:</b>",
    ]
    lines += [f"• {_esc(r)}" for r in cand.reasons]
    if getattr(cand, "risks", None):
        lines += ["", "⚠️ <b>ปัจจัยที่อาจทำให้แผนนี้ล้มเหลว:</b>"]
        lines += [f"• {_esc(r)}" for r in cand.risks]
    lines += ["", "📌 <b>หมายเหตุ:</b>"]
    lines += [f"• {_esc(n)}" for n in cand.notes]
    lines += [
        "━━━━━━━━━━━━━━",
        f"🆔 Signal ID: {signal_id}",
        "⚠️ <i>บริหารความเสี่ยงเอง ไม่เกิน 1% ต่อไม้ | ไม่ใช่คำแนะนำการลงทุน</i>",
    ]
    return "\n".join(lines)


def format_tp(symbol: str, side: str, level: int, price: float,
              signal_id: int) -> str:
    """TP1/TP2/TP3 hit notification."""
    if level == 1:
        head = "✅ <b>TP1 Hit</b> (1R)"
        hint = "💡 แนะนำ: ปิดบางส่วน + เลื่อน SL มาจุดเข้า (Break Even)"
    elif level == 2:
        head = "✅ <b>TP2 Hit</b> (2R)"
        hint = "💡 แนะนำ: ปิดเพิ่ม หรือเลื่อน SL ตามกำไร"
    else:
        head = "🎯 <b>TP3 Hit</b> (3R) — สัญญาณจบสมบูรณ์"
        hint = "💡 ปิดไม้ที่เหลือทั้งหมด"
    return (f"{head}\n{_esc(symbol)} {side} @ <b>{price}</b>\n{hint}\n"
            f"🆔 Signal ID: {signal_id}")


def format_sl(symbol: str, side: str, price: float, signal_id: int) -> str:
    return (f"🛑 <b>Stop Loss Hit</b>\n{_esc(symbol)} {side} @ <b>{price}</b>\n"
            f"🆔 Signal ID: {signal_id}")


def format_cancel(symbol: str, side: str, reason: str, signal_id: int) -> str:
    return (f"❌ <b>Signal Cancelled</b>\n{_esc(symbol)} {side}\n"
            f"เหตุผล: {_esc(reason)}\n"
            f"💡 ถ้ายังไม่เข้า = ไม่ต้องเข้าแล้ว / ถ้าเข้าแล้ว = พิจารณาปิดก่อนถึง SL\n"
            f"🆔 Signal ID: {signal_id}")


def format_status(rejections, active: int, today: int, errors) -> str:
    """Optional heartbeat so you can see the bot is alive and why it waits."""
    lines = ["🔍 <b>รายงานสถานะ</b>",
             f"สัญญาณวันนี้: {today} | กำลังติดตาม: {active}", ""]
    if rejections:
        lines.append("<b>รอที่ขั้นตอน:</b>")
        for rej in rejections[:12]:
            lines.append(f"• {_esc(rej.symbol)}: {_esc(rej.stage)} — {_esc(rej.detail)}")
    if errors:
        lines.append("")
        lines.append("<b>ข้อผิดพลาด:</b>")
        for err in errors[:5]:
            lines.append(f"• {_esc(err)}")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Market briefing: what the chart looks like right now, sent on a timer
# whether or not any setup qualifies.
# ----------------------------------------------------------------------
_TREND_LABEL = {"UP": "ขาขึ้น ▲", "DOWN": "ขาลง ▼", "SIDE": "ออกข้าง ↔"}
_PROFILE_TAG = {"turbo": "🔥M1", "scalp": "⚡M5", "day": "📊M15", "trend": "🚀H1"}
_GRADE_ADVICE = {
    "S": "คุณภาพสูงสุด — ขนาดไม้ปกติได้เต็มที่",
    "A": "คุณภาพดี — ขนาดไม้ปกติ",
    "B": "คุณภาพปานกลาง — ลดขนาดไม้เหลือ 50-70%",
    "C": "คุณภาพพอใช้ — ลดขนาดไม้เหลือ 30-50%",
    "D": "คุณภาพต่ำสุดที่ระบบยอมส่ง — ลดขนาดไม้เหลือ 20-30% หรือข้ามไม้นี้",
}
_TOTAL_STEPS = 11


def _fmt(value: float, symbol: str) -> str:
    """Price formatting that suits the instrument."""
    digits = 2 if "JPY" in symbol or symbol == "XAUUSD" else 5
    return f"{value:.{digits}f}"


def _trend(label: str) -> str:
    return _TREND_LABEL.get(label, label)


def _is_long_hold(profile_name: str) -> bool:
    from .profiles import ALL as _PROFILES
    prof = _PROFILES.get(profile_name)
    return bool(prof and prof.is_long_hold)


TF_ORDER = ("M1", "M5", "M15", "H1", "H4", "D1")
# the long-hold style never reads anything below H1, so listing M1-M15 here
# would only print empty cells
TF_ORDER_LONG = ("H1", "H4", "D1", "W1")
_ARROW = {"UP": "▲", "DOWN": "▼", "SIDE": "↔"}


def _tf_trends(views) -> dict:
    """Merge every profile's ladder into one timeframe -> trend map.

    Each MarketView only carries the four timeframes its own style reads,
    so the full M1..W1 picture only exists once the styles are combined.
    """
    out = {}
    for v in views:
        names = getattr(v, "tf_names", ())
        trends = (v.trend_d1, v.trend_h4, v.trend_h1, v.trend_entry)
        for name, trend in zip(names, trends):
            out.setdefault(name, trend)
    return out


def _tf_row(trends: dict, order) -> str:
    """One compact line: M1▲ M5▲ M15↔ ..., '·' where a timeframe is missing."""
    cells = []
    for tf in order:
        arrow = _ARROW.get(trends.get(tf, ""), "·")
        cells.append(f"{tf}{arrow}")
    return " ".join(cells)


def _bias(views) -> str:
    """Agreed direction across a symbol's styles, or '—' when they conflict."""
    dirs = {v.direction for v in views if v.direction}
    if dirs == {1}:
        return "BUY"
    if dirs == {-1}:
        return "SELL"
    return "—"


def _symbol_lines(symbol, views, order, gold: bool) -> list:
    """The 2-3 line summary for one symbol inside one set."""
    best = max(views, key=lambda v: v.steps_passed)
    mark = "🥇" if gold else "•"
    zone_icon = {"Discount": "🟢", "Premium": "🔴"}.get(best.zone, "⚪")
    tag = _PROFILE_TAG.get(getattr(best, "profile", ""), "")

    lines = [f"{mark} <b>{_esc(symbol)}</b> {_fmt(best.price, symbol)} → "
             f"<b>{_bias(views)}</b>",
             _tf_row(_tf_trends(views), order)]

    detail = (f"{zone_icon} {best.zone} {best.range_pos:.2f} · {tag} "
              f"{best.steps_passed}/{_TOTAL_STEPS}")
    if best.score:
        detail += f" · {best.score:.0f}"
    lines.append(detail)

    # levels and warnings are worth the extra line on the headline symbol
    if gold and (best.swing_high or best.swing_low):
        hi = _fmt(best.swing_high, symbol) if best.swing_high else "-"
        lo = _fmt(best.swing_low, symbol) if best.swing_low else "-"
        lines.append(f"🔻 {lo} · 🔺 {hi}")
    if getattr(best, "price_note", ""):
        lines.append(f"⚠️ <i>{_esc(best.price_note)}</i>")
    elif getattr(best, "data_stale", False):
        lines.append(f"⚠️ <i>ข้อมูลช้า {best.data_age_min:.0f} นาที</i>")
    return lines


def _set_section(views, order, primary, header: str, note: str) -> list:
    """One setup set: every symbol summarised, gold first."""
    if not views:
        return []
    by_symbol = {}
    for v in views:
        by_symbol.setdefault(v.symbol, []).append(v)

    def rank(item):
        symbol, group = item
        # gold always leads, then whoever is closest to a signal
        return (symbol not in primary, -max(v.steps_passed for v in group))

    lines = ["", header, f"<i>{note}</i>", "━━━━━━━━━━━━━━"]
    for symbol, group in sorted(by_symbol.items(), key=rank):
        lines.extend(_symbol_lines(symbol, group, order, symbol in primary))
    return lines


def format_briefing(views, active: int, today: int, counts=None,
                    primary=None) -> str:
    """A one-page trend summary: every symbol, every timeframe.

    Two sets, because they are traded differently: short-term setups that
    close within the day, and long-hold setups carried across days. Each
    symbol takes three lines - price and agreed direction, the trend on
    every timeframe, then zone and pipeline progress.

    `counts` is an optional (gold_today, gold_target, pair_today,
    pair_target) tuple. `primary` is the headline symbol set (gold), which
    always leads its section.
    """
    primary = set(primary or ())
    stamp = datetime.now(timezone.utc).strftime("%d/%m %H:%M")

    lines = [f"📊 <b>สรุปแนวโน้มทุกไทม์เฟรม</b> · {stamp} UTC",
             f"<i>สัญญาณวันนี้ {today} · กำลังติดตาม {active}</i>"]
    if counts:
        gold_today, gold_target, pair_today, pair_target = counts
        lines.append(f"<i>ทอง {gold_today}/{gold_target} · "
                     f"คู่เงิน {pair_today}/{pair_target}</i>")

    if primary and not any(v.symbol in primary for v in views):
        lines.append("⚠️ <i>รอบนี้ยังไม่มีข้อมูลทอง — ดึงราคาไม่สำเร็จ</i>")

    short_views = [v for v in views
                   if not _is_long_hold(getattr(v, "profile", ""))]
    long_views = [v for v in views
                  if _is_long_hold(getattr(v, "profile", ""))]

    lines += _set_section(
        short_views, TF_ORDER, primary,
        "⚡ <b>เซ็ตอัพสายเก็บสั้น</b>", "ปิดภายในวัน · M1-D1")
    lines += _set_section(
        long_views, TF_ORDER_LONG, primary,
        "🚀 <b>เซ็ตอัพสายถือยาว</b>", "ถือข้ามวัน-สัปดาห์ · H1-W1")

    lines.append("━━━━━━━━━━━━━━")
    lines.append("<i>▲ ขาขึ้น · ▼ ขาลง · ↔ ออกข้าง · · ไม่มีข้อมูล</i>")
    lines.append("<i>รายงานภาพรวม ไม่ใช่สัญญาณเข้าเทรด</i>")
    return "\n".join(lines)


def format_no_setup(news_ctx, views) -> str:
    """The message the spec asks for when nothing qualifies.

    Also carries the news state, because "no setup" is only meaningful
    if you know whether the system could see the calendar at all.
    """
    lines = ["🔎 <b>ยังไม่มีจุดเข้า</b>",
             "ขณะนี้ยังไม่มีจุดเข้า Buy หรือ Sell ที่มีคุณภาพสูง "
             "ระบบกำลังติดตามตลาดและข่าวล่าสุดอย่างต่อเนื่อง"]

    if news_ctx is not None:
        lines.append("")
        if news_ctx.verified():
            lines.append("📰 <b>สถานะข่าว:</b>")
            for note in news_ctx.notes[:4]:
                lines.append(f"• {_esc(note)}")
            if news_ctx.sentiment:
                summary = " · ".join(
                    f"{cur}: {_SENTIMENT_TH.get(val, val)}"
                    for cur, val in sorted(news_ctx.sentiment.items()))
                lines.append(f"• ทิศทางจากข่าว → {summary}")
        else:
            lines.append("📰 <i>ไม่สามารถยืนยันข้อมูลข่าวล่าสุดได้ "
                         "— ระบบจะไม่ใช้ข่าวประกอบการตัดสินใจจนกว่าจะเข้าถึงข้อมูลได้</i>")

    # the symbol that came closest, so there is something to watch
    if views:
        best = max(views, key=lambda v: v.steps_passed)
        if best.steps_passed >= 6:
            tag = _PROFILE_TAG.get(getattr(best, "profile", ""), "")
            lines.append("")
            lines.append(f"👀 ใกล้ที่สุด: <b>{_esc(best.symbol)}</b> {tag} "
                         f"({best.steps_passed}/11) — {_esc(best.waiting)}")
    return "\n".join(lines)


_SENTIMENT_TH = {"bullish": "แข็ง", "bearish": "อ่อน", "neutral": "กลาง"}
