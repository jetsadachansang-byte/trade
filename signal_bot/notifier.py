"""Telegram delivery for the signal bot."""
from __future__ import annotations

import html

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


def format_briefing(views, active: int, today: int, counts=None) -> str:
    """One message covering every analysed symbol.

    Reports trend across timeframes, the levels that matter, and how far
    each symbol has moved through the entry pipeline - so the market can
    be followed continuously instead of only when a signal fires.

    `counts` is an optional (gold_today, gold_target, pair_today,
    pair_target) tuple, shown so the daily pacing is visible.
    """
    lines = ["📈 <b>วิเคราะห์กราฟ — ภาพรวมตลาด</b>",
             f"<i>สัญญาณวันนี้ {today} · กำลังติดตาม {active}</i>"]
    if counts:
        gold_today, gold_target, pair_today, pair_target = counts
        lines.append(f"<i>ทอง {gold_today}/{gold_target} · "
                     f"คู่เงิน {pair_today}/{pair_target}</i>")

    short_views = [v for v in views if not _is_long_hold(getattr(v, "profile", ""))]
    long_views = [v for v in views if _is_long_hold(getattr(v, "profile", ""))]

    for group, header, top in ((short_views, "⚡ <b>สายเก็บสั้น (Day Trade / Scalp)</b>", 5),
                               (long_views, "🚀 <b>สายถือยาว (Run Trend)</b>", 3)):
        if not group:
            continue
        lines.append("")
        lines.append(header)
        # 8 symbols x 4 styles is far too much detail to read hourly, so
        # only the ones closest to a signal get a full block; the rest are
        # summarised one per line so nothing disappears silently
        ranked = sorted(group, key=lambda x: -x.steps_passed)
        lines.extend(_briefing_block(ranked[:top]))
        rest = ranked[top:]
        if rest:
            lines.append("━━━━━━━━━━━━━━")
            lines.append(f"<i>อีก {len(rest)} รายการที่ยังไม่ใกล้จุดเข้า:</i>")
            for v in rest:
                tag = _PROFILE_TAG.get(getattr(v, "profile", ""), "")
                lines.append(f"• {_esc(v.symbol)} {tag} "
                             f"{v.steps_passed}/{_TOTAL_STEPS} — {_esc(v.waiting)}")

    lines.append("━━━━━━━━━━━━━━")
    lines.append("<i>รายงานภาพรวม ไม่ใช่สัญญาณเข้าเทรด — "
                 "ระบบจะแจ้งแยกต่างหากเมื่อมีจุดเข้าครบเงื่อนไข</i>")
    return "\n".join(lines)


def _briefing_block(views) -> list:
    """The per-symbol detail rows of the briefing, closest to a signal first."""
    lines = []
    for v in sorted(views, key=lambda x: -x.steps_passed):
        style = _PROFILE_TAG.get(getattr(v, "profile", ""), "")
        lines.append("━━━━━━━━━━━━━━")
        lines.append(f"📊 <b>{_esc(v.symbol)}</b> {style} @ <b>{_fmt(v.price, v.symbol)}</b>")
        age = getattr(v, "data_age_min", 0.0)
        if getattr(v, "data_stale", False):
            lines.append(f"⚠️ <i>ข้อมูลช้า {age:.0f} นาที — ราคาอาจไม่ตรงกระดาน</i>")
        elif age:
            lines.append(f"<i>ข้อมูลอัปเดตเมื่อ {age:.0f} นาทีที่แล้ว</i>")
        if getattr(v, "price_note", ""):
            lines.append(f"⚠️ <i>{_esc(v.price_note)}</i>")

        # --- trend across this profile's own timeframe ladder ---------
        t1, t2, t3, t4 = getattr(v, "tf_names", ("D1", "H4", "H1", "เข้า"))
        lines.append(f"แนวโน้ม: {t1} {_trend(v.trend_d1)} | {t2} {_trend(v.trend_h4)}")
        lines.append(f"　　　　 {t3} {_trend(v.trend_h1)} | {t4} {_trend(v.trend_entry)}")
        if v.direction:
            lines.append(f"ทิศทางที่ระบบมอง: <b>{'BUY' if v.direction > 0 else 'SELL'}</b>")
        else:
            lines.append("ทิศทาง: <i>ยังไม่ชัด (TF ไม่ตรงกัน)</i>")

        # --- levels that matter --------------------------------------
        if v.swing_high > 0 or v.swing_low > 0:
            hi = _fmt(v.swing_high, v.symbol) if v.swing_high else "-"
            lo = _fmt(v.swing_low, v.symbol) if v.swing_low else "-"
            lines.append(f"🔺 แนวต้าน/BSL: <b>{hi}</b>" + ("  (Equal Highs)" if v.equal_highs else ""))
            lines.append(f"🔻 แนวรับ/SSL: <b>{lo}</b>" + ("  (Equal Lows)" if v.equal_lows else ""))

        # --- premium / discount --------------------------------------
        zone_icon = {"Discount": "🟢", "Premium": "🔴"}.get(v.zone, "⚪")
        lines.append(f"{zone_icon} โซน: <b>{v.zone}</b> (rangePos {v.range_pos:.2f})")

        # --- smart money state ---------------------------------------
        marks = []
        if v.recent_bos:
            marks.append("BOS ✔")
        if v.recent_choch:
            marks.append("CHoCH ✔")
        if v.sweep_bull:
            marks.append("Sweep ล่าง ✔")
        if v.sweep_bear:
            marks.append("Sweep บน ✔")
        if v.fvg:
            marks.append("FVG ✔" + (" (mitigated)" if v.fvg_mitigated else ""))
        if marks:
            lines.append("🧠 " + " · ".join(marks))

        if v.ob_zone:
            bottom, top, quality = v.ob_zone
            state = "ราคาอยู่ในโซนแล้ว" if v.ob_mitigating else "รอราคากลับมา"
            lines.append(f"🎯 Order Block: <b>{_fmt(bottom, v.symbol)} – {_fmt(top, v.symbol)}</b>")
            lines.append(f"　 คุณภาพ {quality:.0f}/100 · {state}")

        if v.atr:
            lines.append(f"📏 ATR: {_fmt(v.atr, v.symbol)}")

        # --- pipeline progress ---------------------------------------
        bar = "█" * v.steps_passed + "░" * (_TOTAL_STEPS - v.steps_passed)
        lines.append(f"ความคืบหน้า: {bar} {v.steps_passed}/{_TOTAL_STEPS}")
        if v.score:
            lines.append(f"คะแนนล่าสุด: <b>{v.score:.0f}</b>/100")
        if v.waiting:
            near = "🔥 " if v.steps_passed >= 8 else "⏳ "
            lines.append(f"{near}{_esc(v.waiting)}")
    return lines


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
