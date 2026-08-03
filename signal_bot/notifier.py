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


def format_signal(cand, signal_id: int) -> str:
    """The full signal message."""
    arrow = "📈" if cand.direction > 0 else "📉"
    lines = [
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
