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


def _esc(value) -> str:
    """Escape user-facing text for Telegram HTML mode."""
    return html.escape(str(value), quote=False)


def format_signal(cand, signal_id: int, prof=None) -> str:
    """The full signal message."""
    arrow = "📈" if cand.direction > 0 else "📉"
    head = (f"{prof.emoji} <b>{_esc(prof.label)}</b> · TF {_esc(cand.timeframe)}"
            if prof else f"TF {_esc(cand.timeframe)}")
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
        f"⭐ Confidence Score: <b>{cand.score:.0f}%</b>",
        "━━━━━━━━━━━━━━",
        "🧠 <b>เหตุผลในการวิเคราะห์:</b>",
    ]
    lines += [f"• {_esc(r)}" for r in cand.reasons]
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
_TOTAL_STEPS = 11


def _fmt(value: float, symbol: str) -> str:
    """Price formatting that suits the instrument."""
    digits = 2 if "JPY" in symbol or symbol == "XAUUSD" else 5
    return f"{value:.{digits}f}"


def _trend(label: str) -> str:
    return _TREND_LABEL.get(label, label)


def format_briefing(views, active: int, today: int) -> str:
    """One message covering every analysed symbol.

    Reports trend across timeframes, the levels that matter, and how far
    each symbol has moved through the entry pipeline - so the market can
    be followed continuously instead of only when a signal fires.
    """
    lines = ["📈 <b>วิเคราะห์กราฟ — ภาพรวมตลาด</b>",
             f"<i>สัญญาณวันนี้ {today} · กำลังติดตาม {active}</i>"]

    # symbols closest to a signal first - that is what needs attention
    for v in sorted(views, key=lambda x: -x.steps_passed):
        style = _PROFILE_TAG.get(getattr(v, "profile", ""), "")
        lines.append("━━━━━━━━━━━━━━")
        lines.append(f"📊 <b>{_esc(v.symbol)}</b> {style} @ <b>{_fmt(v.price, v.symbol)}</b>")
        age = getattr(v, "data_age_min", 0.0)
        if getattr(v, "data_stale", False):
            lines.append(f"⚠️ <i>ข้อมูลช้า {age:.0f} นาที — ราคาอาจไม่ตรงกระดาน</i>")
        elif age:
            lines.append(f"<i>ข้อมูลอัปเดตเมื่อ {age:.0f} นาทีที่แล้ว</i>")

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

    lines.append("━━━━━━━━━━━━━━")
    lines.append("<i>รายงานภาพรวม ไม่ใช่สัญญาณเข้าเทรด — "
                 "ระบบจะแจ้งแยกต่างหากเมื่อมีจุดเข้าครบเงื่อนไข</i>")
    return "\n".join(lines)
