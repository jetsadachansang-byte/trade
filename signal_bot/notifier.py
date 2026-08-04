"""Telegram delivery for the signal bot."""
from __future__ import annotations

import html
from datetime import datetime, timezone

import requests

from . import voters as VOTERS

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


_VERDICT_ICON = {"สนับสนุน": "✅", "ค้าน": "❌", "เป็นกลาง": "➖"}


def _vote_block(con, show_reasons: bool = True) -> list:
    """The Strategy Voting System, shown as the ballot it actually is.

    Every technique the regime selected gets a line with its own score and
    its own reason, so a reader can see which ones carried the decision and
    which ones argued against it - not just the number they averaged to.
    """
    if con is None:
        return []
    out = [
        "━━━━━━━━━━━━━━",
        "🗳 <b>การลงคะแนนของแต่ละเทคนิค</b>",
        f"<i>{con.selection_why}</i>",
    ]
    for v in con.votes:
        icon = _VERDICT_ICON.get(v.verdict, "➖")
        out.append(f"{icon} <b>{v.label}</b> — {v.score:.0f}/100 ({v.verdict})")
        if show_reasons and v.reasons:
            out.append(f"     └ {v.reasons[0]}")
    out.append(f"📊 คะแนนรวม <b>{con.score:.0f}</b> · "
               f"ความสอดคล้อง <b>{con.agreement:.0%}</b> · "
               f"ความมั่นใจหลังปรับ <b>{con.confidence:.0f}</b>")
    if con.supporters:
        out.append("✅ สนับสนุน: " + ", ".join(con.supporters))
    if con.dissenters:
        out.append("❌ ไม่สนับสนุน: " + ", ".join(con.dissenters))
    out += ["", "🧠 <b>AI Reasoning — ทำไมถึงสรุปแบบนี้</b>"]
    out += [f"• {r}" for r in con.reasoning]
    out += ["", "🚫 <b>เทคนิคที่ระบบไม่ใช้ (ไม่เดา):</b>"]
    out += [f"• <i>{u}</i>" for u in VOTERS.UNAVAILABLE]
    return out


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
        "🎯 <b>แผนออก (คำนวณใหม่ทุกครั้ง ไม่ใช่ค่าตายตัว)</b>",
        f"• รูปแบบ: <b>{_esc(getattr(cand, 'exit_label', '') or '-')}</b>"
        + ("  🔒 ลาก SL ตาม" if getattr(cand, 'exit_mode', '') == 'trailing' else ""),
        f"• โอกาสถึง TP1/TP2/TP3: "
        f"{'/'.join(f'{x:.0f}%' for x in getattr(cand, 'prob_tp', (0, 0, 0)))}",
        f"• โอกาสโดน SL: <b>{getattr(cand, 'prob_sl', 0):.0f}%</b>"
        f" · Expected Drawdown {getattr(cand, 'expected_drawdown', 0):.2f}R",
        "━━━━━━━━━━━━━━",
        "🏛 <b>บริบทสถาบัน</b>",
        f"• สภาพตลาด: <b>{_esc(getattr(cand, 'regime', '-') or '-')}</b>"
        f" (มั่นใจ {getattr(cand, 'regime_confidence', 0):.0f}%)",
        f"• กลยุทธ์ที่ใช้: {_esc(', '.join(getattr(cand, 'strategies', [])) or '-')}",
        f"• โอกาสชนะ: <b>{getattr(cand, 'win_probability', 0):.0f}%</b>"
        f" ({'จากสถิติจริง' if getattr(cand, 'prob_source', '') == 'history' else 'ค่าประเมิน ไม่ใช่สถิติจริง'})",
        f"• Expected Value: <b>{getattr(cand, 'expected_value', 0):+.2f}R</b>"
        f" · RR เฉลี่ยที่คาด {getattr(cand, 'expected_rr', 0):.2f}",
        f"• มหภาค: {_esc(getattr(cand, 'macro_note', '') or '-')}",
        f"• สถิติสภาพตลาดนี้: {_esc(getattr(cand, 'memory_note', '') or '-')}",
        f"• ผ่านการอนุมัติ: {_esc(getattr(cand, 'approval', '') or '-')}",
        f"🚫 <b>จุดยกเลิก:</b> {_esc(getattr(cand, 'invalidation', '') or '-')}",
        "━━━━━━━━━━━━━━",
        "🧠 <b>เหตุผลในการวิเคราะห์:</b>",
    ]
    lines += [f"• {_esc(r)}" for r in cand.reasons]
    lines += _vote_block(getattr(cand, "consensus", None))
    if getattr(cand, "risks", None):
        lines += ["", "⚠️ <b>ปัจจัยที่อาจทำให้แผนนี้ล้มเหลว:</b>"]
        lines += [f"• {_esc(r)}" for r in cand.risks]
    if getattr(cand, "exit_reasons", None):
        lines += ["", "🎯 <b>ทำไมถึงเลือกแผนออกนี้:</b>"]
        lines += [f"• {_esc(r)}" for r in cand.exit_reasons[:5]]
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


def format_trail(symbol: str, side: str, old_sl: float, new_sl: float,
                 signal_id: int) -> str:
    """The stop moved up behind the trade."""
    return (f"🔒 <b>เลื่อน Stop Loss ตามกำไร</b>\n"
            f"{_esc(symbol)} {side}\n"
            f"SL: {old_sl} → <b>{new_sl}</b>\n"
            f"💡 ล็อกกำไรไว้แล้ว ปล่อยไม้ที่เหลือวิ่งต่อ\n"
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


def _smc_story(v, direction: int) -> list:
    """What smart money has actually done on this chart, in plain Thai.

    Everything here is read off the structure - never off an indicator,
    which by design can only confirm, never lead.
    """
    out = []
    if v.recent_bos:
        out.append("<b>BOS</b> — โครงสร้างถูกเบรกต่อเนื่อง แรงฝั่งเดิมยังอยู่")
    if v.recent_choch:
        out.append("<b>CHoCH</b> — โครงสร้างเปลี่ยนทิศ เป็นสัญญาณกลับตัว "
                   "(ถ้าไม่ไปต่ออาจเป็นสัญญาณหลอก)")
    if v.sweep_bull:
        out.append("<b>กวาดสภาพคล่องฝั่งล่างแล้ว</b> — ลากลง ล้าง stop "
                   "ใต้แนวรับ แล้วดึงกลับ มักตามด้วยการขึ้น")
    if v.sweep_bear:
        out.append("<b>กวาดสภาพคล่องฝั่งบนแล้ว</b> — ดันขึ้น ล้าง stop "
                   "เหนือแนวต้าน แล้วกดกลับ มักตามด้วยการลง")
    if v.equal_highs:
        out.append("มี <b>Equal Highs</b> ด้านบน — เป็นกองสภาพคล่องที่ราคามักวิ่งไปกวาด")
    if v.equal_lows:
        out.append("มี <b>Equal Lows</b> ด้านล่าง — เป็นกองสภาพคล่องที่ราคามักวิ่งไปกวาด")
    if v.ob_zone:
        state = ("ราคาเข้ามาในโซนแล้ว" if v.ob_mitigating
                 else "ราคายังไม่กลับมาที่โซน")
        out.append(f"<b>Order Block</b> คุณภาพ {v.ob_zone[2]:.0f}/100 — {state}")
    if v.fvg:
        state = "ถูกเติมเต็มแล้ว" if v.fvg_mitigated else "ยังไม่ถูกเติมเต็ม"
        out.append(f"<b>Fair Value Gap</b> ในทิศทางเทรด — {state}")
    zone_note = {
        "Discount": "ราคาอยู่ <b>ครึ่งล่างของกรอบ</b> — ฝั่งได้เปรียบของคนซื้อ",
        "Premium": "ราคาอยู่ <b>ครึ่งบนของกรอบ</b> — ฝั่งได้เปรียบของคนขาย",
    }.get(v.zone, "ราคาอยู่ <b>กลางกรอบ</b> — ยังไม่ได้เปรียบฝั่งไหน")
    out.append(f"{zone_note} (rangePos {v.range_pos:.2f})")
    if not direction:
        out.append("⚠️ ไทม์เฟรมใหญ่ยังขัดกัน — โครงสร้างยังไม่เลือกทาง")
    return out


def _levels(v, symbol: str) -> list:
    """The prices that decide what happens next."""
    out = []
    if v.swing_high:
        out.append(f"🔺 แนวต้าน / สภาพคล่องฝั่งบน: <b>{_fmt(v.swing_high, symbol)}</b>")
    if v.swing_low:
        out.append(f"🔻 แนวรับ / สภาพคล่องฝั่งล่าง: <b>{_fmt(v.swing_low, symbol)}</b>")
    if v.ob_zone:
        bottom, top, _ = v.ob_zone
        out.append(f"🎯 โซน Order Block: <b>{_fmt(bottom, symbol)} – {_fmt(top, symbol)}</b>")
    if v.atr:
        out.append(f"📏 ATR ({v.tf_names[3]}): {_fmt(v.atr, symbol)}")
    return out


def _candle_rules(entry_tf: str, bullish: bool) -> list:
    """The candle that has to close before a zone counts as confirmed."""
    if bullish:
        return [f"　• แท่งเขียวกลืนแท่งแดงก่อนหน้า (Bullish Engulfing)",
                f"　• หรือไส้ล่างยาว ปิดกลับเข้าโซน (Pin Bar / ค้อน)",
                f"　• หรือปิดสูงกว่ายอดแท่งก่อนหน้า"]
    return [f"　• แท่งแดงกลืนแท่งเขียวก่อนหน้า (Bearish Engulfing)",
            f"　• หรือไส้บนยาว ปิดกลับเข้าโซน (Pin Bar / ดาวตก)",
            f"　• หรือปิดต่ำกว่าก้นแท่งก่อนหน้า"]


def _playbook(v, symbol: str, direction: int) -> list:
    """Zone by zone: may I buy, may I sell, and what must the candle do.

    Written as explicit permissions rather than commentary, because
    "price is at resistance" does not tell anyone whether to press a
    button. Nothing here places an order - the decision stays with the
    person reading it.
    """
    tf = v.tf_names[3]
    hi = _fmt(v.swing_high, symbol) if v.swing_high else None
    lo = _fmt(v.swing_low, symbol) if v.swing_low else None
    ob_lo = _fmt(v.ob_zone[0], symbol) if v.ob_zone else None
    ob_hi = _fmt(v.ob_zone[1], symbol) if v.ob_zone else None
    out = []

    if direction > 0:
        if ob_lo:
            out += [f"🟢 <b>ถ้าลงมาที่ {ob_lo} – {ob_hi}</b> (โซน Order Block)",
                    "✅ <b>BUY ได้</b> — เป็นโซนที่ระบบรออยู่",
                    "❌ ห้าม SELL ที่โซนนี้ (สวนโครงสร้าง)",
                    f"📍 ต้องรอแท่ง {tf} <b>ปิด</b> แบบใดแบบหนึ่งก่อน:"]
            out += _candle_rules(tf, True)
            out.append("❌ ห้ามเข้าตอนแท่งยังไม่ปิด / ห้ามเข้าตอนราคากำลังดิ่งลง")
            if lo:
                out.append(f"🛑 ถ้าเข้าแล้ว วาง SL ใต้ <b>{lo}</b>")
            out.append("")
        if hi:
            out += [f"🔴 <b>ถ้าขึ้นไปที่ {hi}</b> (แนวต้าน / สภาพคล่องฝั่งบน)",
                    "❌ <b>BUY ไม่ได้</b> — ไล่ราคา เหลือระยะวิ่งน้อย",
                    "❌ <b>SELL ไม่ได้</b> — สวนโครงสร้างขาขึ้น",
                    "👉 <b>รออย่างเดียว</b>",
                    f"　⬆️ ถ้าทะลุขึ้น ({tf} ปิดเหนือ {hi})",
                    f"　　→ ขาขึ้นไปต่อ <b>อย่าไล่ซื้อ</b> รอย่อกลับมาทดสอบ {hi}",
                    "　　→ เห็นแท่งเขียวเด้งจากแนวนี้ค่อย BUY",
                    f"　⬇️ ถ้าเด้งลง (ไส้บนยาว ปิดต่ำกว่า {hi})",
                    f"　　→ กลับไปรอที่โซน Order Block ข้างล่าง", ""]
        if lo:
            out += [f"⛔ <b>ถ้า {tf} ปิดต่ำกว่า {lo}</b>",
                    "→ <b>แผน BUY ยกเลิกทั้งหมด</b> ห้ามถัว ห้ามเพิ่มไม้",
                    "→ อยาก SELL ต้องรอ CHoCH ขาลงยืนยันก่อน ไม่ใช่เข้าทันที"]

    elif direction < 0:
        if ob_lo:
            out += [f"🔴 <b>ถ้าเด้งขึ้นมาที่ {ob_lo} – {ob_hi}</b> (โซน Order Block)",
                    "✅ <b>SELL ได้</b> — เป็นโซนที่ระบบรออยู่",
                    "❌ ห้าม BUY ที่โซนนี้ (สวนโครงสร้าง)",
                    f"📍 ต้องรอแท่ง {tf} <b>ปิด</b> แบบใดแบบหนึ่งก่อน:"]
            out += _candle_rules(tf, False)
            out.append("❌ ห้ามเข้าตอนแท่งยังไม่ปิด / ห้ามเข้าตอนราคากำลังพุ่งขึ้น")
            if hi:
                out.append(f"🛑 ถ้าเข้าแล้ว วาง SL เหนือ <b>{hi}</b>")
            out.append("")
        if lo:
            out += [f"🟢 <b>ถ้าลงไปที่ {lo}</b> (แนวรับ / สภาพคล่องฝั่งล่าง)",
                    "❌ <b>SELL ไม่ได้</b> — ไล่ราคา เหลือระยะวิ่งน้อย",
                    "❌ <b>BUY ไม่ได้</b> — สวนโครงสร้างขาลง",
                    "👉 <b>รออย่างเดียว</b>",
                    f"　⬇️ ถ้าทะลุลง ({tf} ปิดต่ำกว่า {lo})",
                    f"　　→ ขาลงไปต่อ <b>อย่าไล่ขาย</b> รอเด้งกลับมาทดสอบ {lo}",
                    "　　→ เห็นแท่งแดงกดจากแนวนี้ค่อย SELL",
                    f"　⬆️ ถ้าเด้งขึ้น (ไส้ล่างยาว ปิดสูงกว่า {lo})",
                    f"　　→ กลับไปรอที่โซน Order Block ข้างบน", ""]
        if hi:
            out += [f"⛔ <b>ถ้า {tf} ปิดสูงกว่า {hi}</b>",
                    "→ <b>แผน SELL ยกเลิกทั้งหมด</b> ห้ามถัว ห้ามเพิ่มไม้",
                    "→ อยาก BUY ต้องรอ CHoCH ขาขึ้นยืนยันก่อน ไม่ใช่เข้าทันที"]

    else:
        if hi and lo:
            out += [f"⚪ <b>ตอนนี้ราคาแกว่งในกรอบ {lo} – {hi}</b>",
                    "❌ <b>ห้าม BUY</b> ❌ <b>ห้าม SELL</b>",
                    "　เพราะไทม์เฟรมใหญ่ยังขัดกัน เข้าตอนนี้คือการเดา",
                    "👉 <b>รอให้ราคาปิดออกนอกกรอบก่อน</b>", "",
                    f"　⬆️ ถ้า {tf} ปิดเหนือ {hi}",
                    f"　　→ เอียงขาขึ้น รอย่อกลับมาทดสอบ {hi} แล้วค่อยหาจังหวะ BUY",
                    "　　→ ต้องเห็นแท่งเขียวเด้งจากแนวนี้ ไม่ใช่ไล่ซื้อทันที",
                    f"　⬇️ ถ้า {tf} ปิดต่ำกว่า {lo}",
                    f"　　→ เอียงขาลง รอเด้งกลับมาทดสอบ {lo} แล้วค่อยหาจังหวะ SELL",
                    "　　→ ต้องเห็นแท่งแดงกดจากแนวนี้ ไม่ใช่ไล่ขายทันที"]
        else:
            out += ["⚪ ยังไม่มีแนวรับ/แนวต้านที่ชัดพอจะวางแผน",
                    "❌ <b>ห้าม BUY</b> ❌ <b>ห้าม SELL</b> — รอโครงสร้างก่อน"]
    return out


def _verdict(v, direction: int) -> tuple:
    """(label, reason) - what to do with this symbol right now."""
    if v.steps_passed >= _TOTAL_STEPS:
        side = "BUY" if direction > 0 else "SELL"
        return (f"เข้า {side} ได้",
                "ผ่านครบทุกขั้น — ดูรายละเอียดในข้อความสัญญาณที่ส่งแยก")
    if not direction:
        return ("รอ", "ไทม์เฟรมใหญ่ยังขัดกัน ยังไม่มีทิศทางที่เชื่อถือได้")
    side = "BUY" if direction > 0 else "SELL"
    if v.steps_passed >= 9:
        return (f"เตรียม {side}",
                f"ผ่าน {v.steps_passed}/{_TOTAL_STEPS} ขั้น — "
                f"{v.waiting or 'รอเงื่อนไขสุดท้าย'}")
    return ("รอ", f"ผ่าน {v.steps_passed}/{_TOTAL_STEPS} ขั้น — "
                  f"{v.waiting or 'ยังไม่ครบเงื่อนไข'}")


def format_symbol_report(symbol: str, views, counts=None,
                         primary: bool = False) -> str:
    """One message for one instrument: what is happening and what to do.

    Sent per symbol rather than merged, so each pair can be read - and
    acted on - on its own without scrolling past the others.
    """
    short = [v for v in views if not _is_long_hold(getattr(v, "profile", ""))]
    long_ = [v for v in views if _is_long_hold(getattr(v, "profile", ""))]
    best = max(short or views, key=lambda v: v.steps_passed)
    direction = best.direction

    mark = "🥇" if primary else "📊"
    stamp = datetime.now(timezone.utc).strftime("%d/%m %H:%M")
    lines = [f"{mark} <b>{_esc(symbol)}</b> · <b>{_fmt(best.price, symbol)}</b>",
             f"<i>{stamp} UTC</i>"]
    if counts:
        gold_today, gold_target, pair_today, pair_target = counts
        lines.append(f"<i>สัญญาณวันนี้ · ทอง {gold_today}/{gold_target} · "
                     f"คู่เงิน {pair_today}/{pair_target}</i>")
    if getattr(best, "price_note", ""):
        lines.append(f"⚠️ <i>{_esc(best.price_note)}</i>")
    elif getattr(best, "data_stale", False):
        lines.append(f"⚠️ <i>ข้อมูลช้า {best.data_age_min:.0f} นาที — เทียบราคากับกระดานก่อน</i>")

    # --- trend across every timeframe --------------------------------
    lines += ["", "📈 <b>แนวโน้มแต่ละไทม์เฟรม</b>",
              _tf_row(_tf_trends(short or views), TF_ORDER)]
    if long_:
        lines.append(_tf_row(_tf_trends(long_), TF_ORDER_LONG) + "  <i>(สายยาว)</i>")
    bias = {1: "BUY", -1: "SELL"}.get(direction, "ยังไม่ชัด")
    lines.append(f"ทิศทางที่โครงสร้างบอก: <b>{bias}</b>")

    # --- what smart money did ----------------------------------------
    lines += ["", "🧠 <b>ตอนนี้เกิดอะไรขึ้น</b>"]
    lines += [f"• {s}" for s in _smc_story(best, direction)]

    # --- levels -------------------------------------------------------
    levels = _levels(best, symbol)
    if levels:
        lines += ["", "📍 <b>ระดับราคาสำคัญ</b>"] + levels

    # --- conditional plan ---------------------------------------------
    lines += ["", "🗺️ <b>แผนชัด ๆ — โซนไหนทำอะไรได้</b>"]
    lines += _playbook(best, symbol, direction)

    # --- verdict -------------------------------------------------------
    label, reason = _verdict(best, direction)
    lines += ["", f"⚖️ <b>ตอนนี้ควร: {label}</b>", f"<i>{_esc(reason)}</i>"]

    if long_:
        lv = max(long_, key=lambda v: v.steps_passed)
        llabel, lreason = _verdict(lv, lv.direction)
        lines.append(f"🚀 <i>สายถือยาว: {llabel} — {_esc(lreason)}</i>")

    if not getattr(best, "news_verified", True):
        lines.append("📰 <i>ไม่สามารถยืนยันข่าวล่าสุดได้ — ไม่ใช้ข่าวประกอบการตัดสินใจ</i>")

    lines += ["━━━━━━━━━━━━━━",
              "<i>วิเคราะห์เพื่อประกอบการตัดสินใจ ไม่ใช่คำแนะนำการลงทุน · "
              "ระบบไม่เปิดออเดอร์เอง · เสี่ยงไม่เกิน 1% ต่อไม้</i>"]
    return "\n".join(lines)


def format_macro(view, memory_line: str = "") -> str:
    """LEVEL 1 - the global tape and the day's narrative."""
    lines = ["🌍 <b>ภาพรวมตลาดโลก</b>",
             f"<i>{datetime.now(timezone.utc).strftime('%d/%m %H:%M')} UTC</i>"]

    if not view.available:
        lines += ["", "⚠️ <b>ดึงข้อมูลภาพรวมตลาดไม่ได้</b>",
                  "<i>ระบบจะไม่ใช้ปัจจัยมหภาคประกอบการตัดสินใจรอบนี้ "
                  "และจะไม่เดาแทน</i>"]
        for err in view.errors[:4]:
            lines.append(f"• {_esc(err)}")
        return "\n".join(lines)

    risk_icon = {"Risk On": "🟢", "Risk Off": "🔴"}.get(view.risk, "⚪")
    lines.append(f"{risk_icon} <b>{_esc(view.risk)}</b> "
                 f"(คะแนน {view.risk_score:+.0f})")

    lines += ["", "📉 <b>เปลี่ยนแปลงวันนี้</b>"]
    for name in ("DXY", "US10Y", "VIX", "SP500", "NASDAQ", "DOW",
                 "OIL", "SILVER", "BTC"):
        if name in view.changes:
            ch = view.changes[name]
            arrow = "▲" if ch > 0 else "▼" if ch < 0 else "↔"
            lines.append(f"{name:<7} {arrow} {ch:+.2f}%")

    lines += ["", "🧭 <b>Market Narrative</b>"]
    lines += [f"• {n}" for n in view.narrative]

    lines += ["", "🚧 <b>ข้อมูลที่ระบบเข้าไม่ถึง</b>"]
    lines += [f"• {_esc(g)}" for g in view.gaps]
    if view.errors:
        lines.append(f"• ดึงไม่ได้รอบนี้: {_esc(', '.join(e.split(':')[0] for e in view.errors))}")

    if memory_line:
        lines += ["", f"📚 <i>{_esc(memory_line)}</i>"]
    lines += ["━━━━━━━━━━━━━━",
              "<i>ภาพรวมเพื่อประกอบการวิเคราะห์ ไม่ใช่สัญญาณเข้าเทรด</i>"]
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


# ----------------------------------------------------------------------
# Daily Market Analysis - one planning report, sent once each morning.
# ----------------------------------------------------------------------
_BIAS_ICON = {"BUY": "🟢", "SELL": "🔴", "WAIT": "⚪"}
_RISK_ICON = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}


def _score_bar(score: float) -> str:
    """A ten-cell bar so a number can be read at a glance."""
    filled = int(round(max(0.0, min(100.0, score)) / 10))
    return "█" * filled + "░" * (10 - filled)


def _plan_block(plan, symbol: str, title: str) -> list:
    """One prepared plan, or the reason it is not on the table."""
    if plan.side == "WAIT":
        lines = [f"<b>{title}</b>", "⏸ รอ — ยังไม่เข้าเงื่อนไข"]
        if plan.waiting_for:
            lines.append("ต้องเห็นสิ่งเหล่านี้ก่อน:")
            lines += [f"　• {_esc(w)}" for w in plan.waiting_for]
        return lines

    head = "🟢" if plan.side == "BUY" else "🔴"
    ok = "✅ พร้อมใช้" if plan.viable else "⚠️ ยังไม่ครบเงื่อนไข — เตรียมไว้ก่อน"
    lines = [f"<b>{title}</b>  {head} {plan.side}  {ok}",
             f"เข้า: <b>{plan.entry_low} – {plan.entry_high}</b>",
             f"🛑 SL: <b>{plan.sl}</b>",
             f"🎯 TP1 {plan.tp[0]} · TP2 {plan.tp[1]} · TP3 {plan.tp[2]}",
             f"RR 1:{plan.rr:.1f} · โอกาสถึง TP1 {plan.win_probability:.0f}% · "
             f"EV {plan.expected_value:+.2f}R",
             f"⏱ ถือประมาณ {_esc(plan.hold_time)} · คะแนน {plan.confidence:.0f}/100"]
    if plan.exit_mode == "trailing":
        lines.append("🔒 แผนออก: ปิดบางส่วนแล้วลาก SL ตาม")
    if plan.why:
        lines.append("เหตุผล:")
        lines += [f"　• {_esc(w)}" for w in plan.why[:5]]
    if not plan.viable and plan.waiting_for:
        lines.append("ยังขาด:")
        lines += [f"　• {_esc(w)}" for w in plan.waiting_for[:3]]
    return lines


def format_daily_overview(macro_view, reports, risk, memory_line: str = "") -> str:
    """Part one: what the world is pricing this morning."""
    from .daily import BANGKOK
    stamp = datetime.now(timezone.utc).astimezone(BANGKOK).strftime("%d/%m/%Y")
    lines = [f"📅 <b>บทวิเคราะห์ตลาดประจำวัน</b>",
             f"<i>{stamp} · 06:00 น. (เวลาไทย)</i>",
             "<i>รายงานเพื่อวางแผน ไม่ใช่สัญญาณเข้าออเดอร์ — "
             "สัญญาณจะส่งแยกเมื่อเงื่อนไขครบ</i>", ""]

    # --- the tape ------------------------------------------------------
    if macro_view is not None and macro_view.available:
        icon = {"Risk On": "🟢", "Risk Off": "🔴"}.get(macro_view.risk, "⚪")
        lines += [f"🌍 <b>ภาพรวมตลาดโลก</b>",
                  f"{icon} <b>{_esc(macro_view.risk)}</b> "
                  f"(คะแนน {macro_view.risk_score:+.0f})"]
        row = []
        for name in ("DXY", "US10Y", "VIX", "SP500", "NASDAQ", "DOW",
                     "OIL", "SILVER", "BTC"):
            if name in macro_view.changes:
                ch = macro_view.changes[name]
                row.append(f"{name} {'▲' if ch > 0 else '▼' if ch < 0 else '↔'}{ch:+.2f}%")
        lines += [" · ".join(row[:5]), " · ".join(row[5:])]
        lines += ["", "🧭 <b>Market Narrative</b>"]
        lines += [f"• {n}" for n in macro_view.narrative]
    else:
        lines += ["🌍 <b>ภาพรวมตลาดโลก</b>",
                  "⚠️ <b>ไม่สามารถยืนยันข้อมูลล่าสุดได้</b>",
                  "<i>ระบบจะไม่ใช้ปัจจัยมหภาคประกอบการวิเคราะห์วันนี้ และไม่เดาแทน</i>"]

    # --- what cannot be checked ---------------------------------------
    if macro_view is not None and macro_view.gaps:
        lines += ["", "🚧 <b>ข้อมูลที่ระบบเข้าไม่ถึง</b>"]
        lines += [f"• {_esc(g)}" for g in macro_view.gaps]

    label, why = risk
    lines += ["", f"⚠️ <b>ระดับความเสี่ยงของตลาดวันนี้: "
                  f"{_RISK_ICON.get(label, '')} {label}</b>",
              f"<i>{_esc(why)}</i>"]

    # --- the one-line table -------------------------------------------
    lines += ["", "📊 <b>สรุปทุกคู่</b>"]
    for r in reports:
        if r.error:
            lines.append(f"{_esc(r.symbol)}: ⚠️ {_esc(r.error)}")
            continue
        lines.append(f"{_BIAS_ICON.get(r.bias, '')} <b>{_esc(r.symbol)}</b> "
                     f"{r.bias} · BUY {r.buy_score:.0f} / SELL {r.sell_score:.0f} "
                     f"· {_esc(r.regime)}")
    if memory_line:
        lines += ["", f"📚 <i>{_esc(memory_line)}</i>"]
    return "\n".join(lines)


def _mark(score: float) -> str:
    return "✅" if score >= 65 else "❌" if score <= 35 else "➖"


def _vote_table(rep) -> list:
    """Both sides of the ballot, side by side, for the planning report.

    A daily plan has to hold BUY and SELL open at once, so showing each
    technique's score for both directions answers the question the report
    exists to answer: what would have to change for the other side to win.
    """
    buy, sell = getattr(rep, "vote_buy", None), getattr(rep, "vote_sell", None)
    if buy is None or sell is None:
        return []
    sell_by = {v.name: v for v in sell.votes}
    # ASCII header: Thai glyphs do not hold a monospace column on mobile
    rows = [f"{'Technique':<18}{'BUY':>5}  {'SELL':>5}"]
    for v in buy.votes:
        s = sell_by.get(v.name)
        s_txt = f"{s.score:3.0f} {_mark(s.score)}" if s else "  -   "
        rows.append(f"{v.label[:18]:<18}{v.score:3.0f} {_mark(v.score)}  {s_txt}")

    out = ["<b>🗳 การลงคะแนนของแต่ละเทคนิค</b>",
           f"<i>{buy.selection_why}</i>",
           "<pre>" + "\n".join(html.escape(r) for r in rows) + "</pre>",
           f"📊 BUY รวม <b>{buy.confidence:.0f}</b> (สอดคล้อง {buy.agreement:.0%})"
           f" · SELL รวม <b>{sell.confidence:.0f}</b> (สอดคล้อง {sell.agreement:.0%})"]

    chosen = getattr(rep, "consensus", None) or buy
    out += ["", "<b>🧠 AI Reasoning — เหตุผลเบื้องหลังคำตัดสิน</b>"]
    out += [f"• {r}" for r in chosen.reasoning]
    if chosen.dissenters:
        out.append("• ความเสี่ยงของแผน: "
                   + ", ".join(chosen.dissenters)
                   + " ยังไม่สนับสนุน หากราคาไม่ไปตามแผนเร็ว ให้ถอยก่อน")
    out += ["<i>ไม่ใช้: " + " · ".join(u.split(" — ")[0] for u in VOTERS.UNAVAILABLE)
            + " (ตีความได้หลายแบบ/ไม่มีข้อมูลจริง ระบบไม่เดา)</i>"]
    return out


def format_daily_symbol(rep) -> str:
    """One instrument: structure, zones, and the three plans."""
    if rep.error:
        return (f"📊 <b>{_esc(rep.symbol)}</b>\n"
                f"⚠️ <i>{_esc(rep.error)} — ไม่วิเคราะห์คู่นี้วันนี้</i>")

    icon = _BIAS_ICON.get(rep.bias, "")
    lines = [f"{icon} <b>{_esc(rep.symbol)}</b> · <b>{_fmt(rep.price, rep.symbol)}</b>",
             f"<b>Bias วันนี้: {rep.bias}</b>", ""]

    # --- scores --------------------------------------------------------
    lines += ["<b>คะแนนสองฝั่ง</b>",
              f"🟢 BUY  {_score_bar(rep.buy_score)} {rep.buy_score:.0f}/100",
              f"🔴 SELL {_score_bar(rep.sell_score)} {rep.sell_score:.0f}/100", ""]

    # --- regime and timeframes -----------------------------------------
    lines += [f"<b>สภาพตลาด:</b> {_esc(rep.regime)} "
              f"(มั่นใจ {rep.regime_confidence:.0f}%) · ผันผวน {rep.volatility}",
              f"<b>กลยุทธ์ที่เหมาะ:</b> {_esc(', '.join(rep.strategies))}", ""]

    tf_row = " ".join(
        f"{tf}{_ARROW.get(rep.trends.get(tf, ''), '·')}"
        for tf in ("W1", "D1", "H4", "H1", "M15", "M5"))
    lines += ["<b>แนวโน้มแต่ละไทม์เฟรม</b>", tf_row,
              f"<i>{_esc(rep.htf_support)}</i>", ""]

    # --- zones ---------------------------------------------------------
    lines.append("<b>📍 โซนราคาสำคัญ</b>")
    if rep.strong_buy:
        lines.append(f"🟢 Strong Buy Zone: <b>{rep.strong_buy[0]} – {rep.strong_buy[1]}</b> (Order Block)")
    if rep.weak_buy:
        lines.append(f"🟩 Weak Buy Zone: {rep.weak_buy[0]} – {rep.weak_buy[1]} (แนวรับ)")
    if rep.weak_sell:
        lines.append(f"🟥 Weak Sell Zone: {rep.weak_sell[0]} – {rep.weak_sell[1]} (แนวต้าน)")
    if rep.strong_sell:
        lines.append(f"🔴 Strong Sell Zone: <b>{rep.strong_sell[0]} – {rep.strong_sell[1]}</b> (Order Block)")
    lines += [f"🔺 Swing High: {_fmt(rep.swing_high, rep.symbol)}" if rep.swing_high else "",
              f"🔻 Swing Low: {_fmt(rep.swing_low, rep.symbol)}" if rep.swing_low else "",
              f"💧 สภาพคล่อง: {_esc(rep.liquidity_note)}",
              f"⚡ FVG: {_esc(rep.fvg_note)}",
              f"📏 ATR (H1): {_fmt(rep.atr, rep.symbol)} · "
              f"ตำแหน่งในกรอบ {rep.range_pos:.2f}", ""]

    # --- the three plans ----------------------------------------------
    lines += _plan_block(rep.plan_buy, rep.symbol, "PLAN A — BUY") + [""]
    lines += _plan_block(rep.plan_sell, rep.symbol, "PLAN B — SELL") + [""]
    if rep.plan_wait.side == "WAIT" and rep.plan_wait.waiting_for:
        lines += _plan_block(rep.plan_wait, rep.symbol, "PLAN C — WAIT") + [""]

    # --- the ballot ------------------------------------------------------
    lines += _vote_table(rep) + [""]

    # --- right now ------------------------------------------------------
    lines += ["<b>💡 ถ้าเข้าตอนนี้เลย</b>",
              f"{_BIAS_ICON.get(rep.now_verdict, '')} <b>{rep.now_verdict}</b>",
              f"<i>{_esc(rep.now_why)}</i>", ""]

    # --- managing it ----------------------------------------------------
    lines += ["<b>🎛 การจัดการไม้</b>",
              "• ถึง TP1 → ปิด 1/3 แล้วเลื่อน SL มาที่จุดเข้า (Break Even)",
              "• ถึง TP2 → ปิดอีก 1/3 ล็อกกำไรไว้",
              "• ถึง TP3 → ปิดที่เหลือ หรือถ้าเทรนด์ยังแรงให้ลาก SL ตามแทน",
              "━━━━━━━━━━━━━━",
              "<i>▲ ขาขึ้น · ▼ ขาลง · ↔ ออกข้าง · ทุกระดับราคาอ้างอิงโครงสร้างจริง</i>"]
    return "\n".join(l for l in lines if l != "" or True)


def format_daily_watchlist(reports, risk) -> str:
    """The closing summary: what to watch, what to avoid, and when."""
    ok = [r for r in reports if not r.error]
    buys = sorted(ok, key=lambda r: -r.buy_score)[:3]
    sells = sorted(ok, key=lambda r: -r.sell_score)[:3]
    avoid = [r for r in ok if r.bias == "WAIT" and
             abs(r.buy_score - r.sell_score) <= 3][:4]

    lines = ["📋 <b>Daily Watchlist</b>", ""]
    lines.append("🟢 <b>Top 3 ฝั่ง BUY</b>")
    lines += [f"{i}. {_esc(r.symbol)} — {r.buy_score:.0f}/100 · {_esc(r.regime)}"
              for i, r in enumerate(buys, 1)] or ["—"]
    lines += ["", "🔴 <b>Top 3 ฝั่ง SELL</b>"]
    lines += [f"{i}. {_esc(r.symbol)} — {r.sell_score:.0f}/100 · {_esc(r.regime)}"
              for i, r in enumerate(sells, 1)] or ["—"]

    lines += ["", "⛔ <b>คู่ที่ควรหลีกเลี่ยงวันนี้</b>"]
    if avoid:
        lines += [f"• {_esc(r.symbol)} — สองฝั่งคะแนนใกล้กันมาก "
                  f"({r.buy_score:.0f}/{r.sell_score:.0f}) เข้าไปคือการเดา"
                  for r in avoid]
    else:
        lines.append("• ไม่มีคู่ที่สับสนจนควรเลี่ยงทั้งหมด")

    lines += ["", "⏰ <b>ช่วงเวลาที่ควรเทรด</b>",
              "• 14:00 – 18:00 น. — ลอนดอนเปิด สภาพคล่องเริ่มมา",
              "• 19:00 – 23:00 น. — ลอนดอนทับนิวยอร์ก ช่วงที่ราคาเดินดีที่สุด",
              "", "🚫 <b>ช่วงที่ไม่ควรเทรด</b>",
              "• 05:00 – 13:00 น. — ช่วงเอเชีย สภาพคล่องบาง ราคามักแกว่งในกรอบ",
              "• ก่อน–หลังข่าวแรง 30 นาที — สเปรดกว้างและราคากระชาก"]

    label, why = risk
    lines += ["", f"⚠️ <b>ความเสี่ยงตลาดวันนี้: "
                  f"{_RISK_ICON.get(label, '')} {label}</b>",
              f"<i>{_esc(why)}</i>",
              "━━━━━━━━━━━━━━",
              "<i>บทวิเคราะห์เพื่อวางแผน ไม่ใช่คำแนะนำการลงทุน · "
              "ระบบไม่เปิดออเดอร์เอง · เสี่ยงไม่เกิน 1% ต่อไม้</i>"]
    return "\n".join(lines)


def format_daily_review(rev) -> str:
    """The 05:00 result review of the session that just closed."""
    span = (f"{rev.start.strftime('%d/%m %H:%M')} – "
            f"{rev.end.strftime('%d/%m %H:%M')} น.")
    lines = [
        "📋 <b>สรุปผลประจำวัน</b>",
        f"<i>รอบ {span} (เวลาไทย · ปิดรอบตามตลาดนิวยอร์ก)</i>",
        "━━━━━━━━━━━━━━",
        f"📨 สัญญาณที่ส่งในรอบนี้: <b>{rev.issued}</b> ไม้",
        f"✅ ปิดแล้ว: <b>{len(rev.closed)}</b> ไม้ · "
        f"⏳ ยังถืออยู่: <b>{len(rev.still_open)}</b> ไม้",
    ]

    if rev.closed:
        sign = "🟢" if rev.total_r > 0 else "🔴" if rev.total_r < 0 else "⚪"
        lines += [
            "━━━━━━━━━━━━━━",
            "<b>📊 ผลของไม้ที่ปิดในรอบนี้</b>",
            f"🎯 ชนะ {rev.wins} · แพ้ {rev.losses} · เสมอ {rev.breakeven}"
            + (f" · หมดอายุ {rev.expired}" if rev.expired else ""),
            f"📈 อัตราชนะ: <b>{rev.win_rate:.0f}%</b>"
            + (f" (จาก {rev.wins + rev.losses} ไม้ที่ตัดสินผลได้)"
               if rev.wins + rev.losses else ""),
            f"{sign} ผลรวม: <b>{rev.total_r:+.2f}R</b> "
            f"(โมเดลคาดไว้ {rev.expected_r:+.2f}R)",
            "",
            "<b>รายไม้</b>",
        ]
        for out in rev.closed:
            s = out.signal
            mark = "🟢" if out.result_r > 0.05 else "🔴" if out.result_r < -0.05 else "⚪"
            lines.append(
                f"{mark} <b>{_esc(s.symbol)}</b> {s.side} · <b>{out.result_r:+.2f}R</b>")
            lines.append(f"     └ {_esc(out.label)} · เข้า {s.entry} · "
                         f"คะแนนตอนส่ง {s.score:.0f}")

    if rev.still_open:
        lines += ["━━━━━━━━━━━━━━", "<b>⏳ ไม้ที่ยังถืออยู่</b>"]
        for out in rev.still_open:
            s = out.signal
            lines.append(f"• <b>{_esc(s.symbol)}</b> {s.side} · {_esc(out.label)}"
                         f" · SL ปัจจุบัน {s.sl}")

    if rev.by_symbol and rev.closed:
        # ASCII header: Thai glyphs do not hold a monospace column on mobile
        rows = [f"{'Symbol':<9}{'N':>4}{'R':>9}"]
        for sym, (n, r) in sorted(rev.by_symbol.items(), key=lambda kv: -kv[1][1]):
            rows.append(f"{sym:<9}{n:>4}{r:>+9.2f}")
        lines += ["━━━━━━━━━━━━━━", "<b>📌 แยกตามคู่เทรด</b>",
                  "<pre>" + "\n".join(html.escape(r) for r in rows) + "</pre>"]

    if rev.by_regime and rev.closed:
        lines += ["<b>🌐 แยกตามสภาพตลาด</b>"]
        for name, (n, r) in sorted(rev.by_regime.items(), key=lambda kv: -kv[1][1]):
            lines.append(f"• {_esc(name)}: {n} ไม้ · <b>{r:+.2f}R</b>")

    if rev.notes:
        lines += ["━━━━━━━━━━━━━━", "<b>🧠 อ่านผลรอบนี้ยังไง</b>"]
        lines += [f"• {_esc(n)}" for n in rev.notes]

    if rev.all_time:
        lines += ["", f"📚 <i>{_esc(rev.all_time)}</i>"]

    lines += [
        "━━━━━━━━━━━━━━",
        "<i>ตัวเลข R คิดตามกฎจัดการไม้ที่ระบบแนะนำ — ปิด 1/3 ทุก TP และเลื่อน SL "
        "มาที่จุดเข้าหลัง TP1 ไม่ใช่ผลจากบัญชีจริง ถ้าคุณจัดการไม้ต่างจากนี้ "
        "ผลจริงของคุณจะต่างออกไป</i>",
        "<i>ระบบไม่มีสิทธิ์เปิดออเดอร์เอง — วิเคราะห์และส่งสัญญาณเท่านั้น</i>",
    ]
    return "\n".join(lines)
