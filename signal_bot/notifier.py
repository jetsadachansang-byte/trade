"""Telegram delivery for the signal bot."""
from __future__ import annotations

import html
from datetime import datetime, timezone

import requests

API = "https://api.telegram.org/bot{token}/{method}"


class Telegram:
    """Minimal Telegram Bot API client."""

    def __init__(self, token: str, chat_id: str, dry_run: bool = False,
                 archive=None, now=None):
        self.token = token
        self.chat_id = chat_id
        self.dry_run = dry_run or not (token and chat_id)
        # Archiving happens here rather than at each call site: a message
        # type added later is then filed without anyone remembering to,
        # which is how an archive quietly ends up incomplete.
        self.archive = archive
        self.now = now

    def poll(self, offset: int = 0, limit: int = 20) -> tuple:
        """(updates, next_offset) - text messages sent to the bot.

        Only messages from the configured chat are returned. The bot never
        acts on an instruction from anywhere else, and it does not act on
        instructions at all - the text is treated as a search query and
        nothing more.
        """
        if self.dry_run:
            return [], offset
        try:
            resp = requests.get(
                API.format(token=self.token, method="getUpdates"), timeout=20,
                params={"offset": offset, "timeout": 0, "limit": limit,
                        "allowed_updates": '["message"]'})
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:        # noqa: BLE001 - degraded, not fatal
            print(f"poll: {exc}")
            return [], offset

        out, highest = [], offset
        for update in payload.get("result", []):
            highest = max(highest, int(update.get("update_id", 0)) + 1)
            message = update.get("message") or {}
            chat = str((message.get("chat") or {}).get("id", ""))
            text = (message.get("text") or "").strip()
            if text and chat == str(self.chat_id):
                out.append(text)
        return out, highest

    def send(self, text: str, archive: bool = True) -> bool:
        """Send a message, splitting it if Telegram would reject the length.

        Telegram caps a message at 4096 characters and answers anything
        longer with "message is too long" - which is how the whole chart
        briefing went missing. Splitting happens on line boundaries so no
        HTML tag is ever cut in half.

        `archive=False` is for replies to a search. Filing those would let
        a second search for the same word find the answer to the first,
        and the quoted snippet inside would file the reply under whatever
        kind of message it happened to quote.
        """
        chunks = _split(text)
        sent = all(self._send_one(part) for part in chunks)
        if sent and archive and self.archive is not None:
            from datetime import datetime as _dt
            self.archive.add(text, self.now or _dt.now(timezone.utc))
        return sent

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
    """The ballot, compressed to what a reader acts on.

    The full per-technique breakdown ran to twenty lines on every ticket,
    which is a page of scrolling before the reader reaches the stop. What
    matters is who backed the trade, who argued with it, how aligned they
    were, and why those techniques were chosen at all - that fits in four
    lines without dropping any of it.
    """
    if con is None:
        return []
    out = ["━━━━━━━━━━━━━━",
           f"🗳 <b>มติเทคนิค {con.confidence:.0f}</b>/100 · "
           f"สอดคล้อง {con.agreement:.0%} · {con.selection_why}"]
    line = _vote_line(con)
    if line:
        out.append(line)
    neutral = [v.label for v in con.votes if not v.supports and not v.opposes]
    if neutral:
        out.append("➖ เป็นกลาง: " + ", ".join(neutral))
    if con.dissenters:
        out.append(f"⚠️ <i>{', '.join(con.dissenters)} ไม่สนับสนุน — "
                   f"ถ้าราคาไม่ไปตามแผนเร็ว ให้ถอยก่อน</i>")
    out.append("<i>ไม่ใช้ Harmonic · Elliott Wave · Order Flow "
               "(ตีความได้หลายแบบ/ไม่มีข้อมูลจริง ระบบไม่เดา)</i>")
    return out


def _small_tf_line(cand) -> list:
    """What the faster charts say - context on the ticket, never a gate.

    Entries below H1 are reserved for gold because spread eats a fast trade
    on the crosses, but the fast charts are still read for every symbol.
    Showing them here is the difference between "not used" and "not looked
    at", which are very different claims.
    """
    trends = getattr(cand, "analysis_trends", None)
    if not trends:
        return []
    row = " · ".join(f"{tf}{_ARROW.get(t, '·')}" for tf, t in trends.items())
    return [f"🔎 ไทม์เฟรมเล็ก (ใช้ดูจังหวะ ไม่ใช่จุดเข้า): {row}"]


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
        f"💹 ราคาล่าสุด <b>{cand.entry}</b>"
        + (f" <i>(จาก {_esc(cand.quote_tf)} · เก่า {cand.price_age_min:.0f} นาที)</i>"
           if getattr(cand, "quote_tf", "") else ""),
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
        *_small_tf_line(cand),
        "━━━━━━━━━━━━━━",
        "🎯 <b>แผนออก (คำนวณใหม่ทุกครั้ง ไม่ใช่ค่าตายตัว)</b>",
        f"• รูปแบบ: <b>{_esc(getattr(cand, 'exit_label', '') or '-')}</b>"
        + ("  🔒 ลาก SL ตาม" if getattr(cand, 'exit_mode', '') == 'trailing' else ""),
        f"• โอกาส TP1/2/3: "
        f"{'/'.join(f'{x:.0f}%' for x in getattr(cand, 'prob_tp', (0, 0, 0)))}"
        f" · โอกาสโดน SL <b>{getattr(cand, 'prob_sl', 0):.0f}%</b>",
        f"• ตลาด: <b>{_esc(getattr(cand, 'regime', '-') or '-')}</b>"
        f" ({getattr(cand, 'regime_confidence', 0):.0f}%) · "
        f"{_esc(', '.join(getattr(cand, 'strategies', [])) or '-')}",
        f"• ชนะ <b>{getattr(cand, 'win_probability', 0):.0f}%</b>"
        f" ({'สถิติจริง' if getattr(cand, 'prob_source', '') == 'history' else 'ค่าประเมิน'})"
        f" · EV <b>{getattr(cand, 'expected_value', 0):+.2f}R</b>",
        f"🚫 <b>ยกเลิกถ้า:</b> {_esc(getattr(cand, 'invalidation', '') or '-')}",
    ]
    lines += _vote_block(getattr(cand, "consensus", None))
    if getattr(cand, "risks", None):
        lines += ["⚠️ <b>ความเสี่ยง:</b>"]
        lines += [f"• {_esc(r)}" for r in cand.risks[:3]]
    if cand.notes:
        lines.append(f"📌 <i>{_esc(cand.notes[0])}</i>")
    lines += [
        "━━━━━━━━━━━━━━",
        f"🆔 Signal ID: {signal_id}",
        "⚠️ <i>บริหารความเสี่ยงเอง ไม่เกิน 1% ต่อไม้ | ไม่ใช่คำแนะนำการลงทุน</i>",
    ]
    return "\n".join(lines)


def _plan_lines(sig, reached: int = 0) -> list:
    """The whole plan, with a mark against the levels already taken.

    A tracking alert used to carry only the level that had just been hit,
    which is the one number the reader can see on their own screen. What
    they cannot see from the alert is where the rest of the plan sits -
    and "move the stop to break-even" is not actionable without the entry
    price in front of them.
    """
    return ["<pre>" + "\n".join(html.escape(r) for r in _plan_rows(sig, reached))
            + "</pre>"]


def _plan_rows(sig, reached: int = 0) -> list:
    """The plan as one labelled number per line.

    One number per line is the only shape that survives a narrow screen:
    the label sits next to the price it belongs to, so nothing depends on
    a column heading that may have wrapped away from it.
    """
    sym = sig.symbol
    rows = [f"{'Entry':<6}{_fmt(sig.entry, sym):>9}",
            f"{'SL':<6}{_fmt(sig.sl, sym):>9}"]
    for level, price, hit in ((1, sig.tp1, sig.tp1_hit),
                              (2, sig.tp2, sig.tp2_hit),
                              (3, sig.tp3, sig.tp3_hit)):
        mark = "  ถึงแล้ว" if hit or level <= reached else ""
        rows.append(f"{'TP' + str(level):<6}{_fmt(price, sym):>9}{mark}")
    return rows


def _outcome_tag(sig) -> str:
    """Where a finished plan ended, short enough to live in a table cell."""
    if sig.tp3_hit:
        return "TP3"
    if getattr(sig, "status", "") == "SL_HIT":
        return "SL·TP2" if sig.tp2_hit else "SL·TP1" if sig.tp1_hit else "SL"
    if getattr(sig, "status", "") == "CANCELLED":
        return "ยกเลิก"
    if sig.tp2_hit:
        return "TP2"
    if sig.tp1_hit:
        return "TP1"
    return "-"


# Regime names run to sixteen characters, which pushes the overview table
# past what a phone holds. These are the same names, abbreviated.
_REGIME_SHORT = {
    "Strong Bull": "S.Bull", "Weak Bull": "W.Bull",
    "Strong Bear": "S.Bear", "Weak Bear": "W.Bear",
    "Range": "Range", "Compression": "Squeeze", "Expansion": "Expand",
    "Liquidity Hunt": "LiqHunt", "Trend Exhaustion": "Exhaust",
    "Mean Reversion": "MeanRev", "True Breakout": "Brk.OK",
    "False Breakout": "Brk.Fake", "News Driven": "News",
}


def format_tp(sig, level: int, price: float) -> str:
    """TP1/TP2/TP3 hit, with the rest of the plan alongside it."""
    if level == 1:
        head = "✅ <b>TP1 Hit</b>"
        hint = ("💡 ปิดบางส่วน + เลื่อน SL มาที่ "
                f"<b>{_fmt(sig.entry, sig.symbol)}</b> (จุดเข้า)")
    elif level == 2:
        head = "✅ <b>TP2 Hit</b>"
        hint = "💡 ปิดเพิ่ม หรือเลื่อน SL ตามกำไร"
    else:
        head = "🎯 <b>TP3 Hit</b> — สัญญาณจบสมบูรณ์"
        hint = "💡 ปิดไม้ที่เหลือทั้งหมด"
    lines = [f"{head} @ <b>{_fmt(price, sig.symbol)}</b>",
             f"📊 <b>{_esc(sig.symbol)} {sig.side}</b>"]
    lines += _plan_lines(sig, reached=level)
    lines += [hint, f"🆔 {sig.id}"]
    return "\n".join(lines)


def format_trail(sig, old_sl: float, new_sl: float) -> str:
    """The stop moved up behind the trade."""
    return "\n".join([
        "🔒 <b>เลื่อน Stop Loss ตามกำไร</b>",
        f"📊 <b>{_esc(sig.symbol)} {sig.side}</b>",
        f"SL: {_fmt(old_sl, sig.symbol)} → <b>{_fmt(new_sl, sig.symbol)}</b>",
        *_plan_lines(sig),
        "💡 ล็อกกำไรไว้แล้ว ปล่อยไม้ที่เหลือวิ่งต่อ",
        f"🆔 {sig.id}",
    ])


def format_sl(sig, price: float) -> str:
    return "\n".join([
        f"🛑 <b>Stop Loss Hit</b> @ <b>{_fmt(price, sig.symbol)}</b>",
        f"📊 <b>{_esc(sig.symbol)} {sig.side}</b>",
        *_plan_lines(sig),
        f"🆔 {sig.id}",
    ])


def format_cancel(sig, reason: str) -> str:
    return "\n".join([
        "❌ <b>Signal Cancelled</b>",
        f"📊 <b>{_esc(sig.symbol)} {sig.side}</b>",
        *_plan_lines(sig),
        f"เหตุผล: {_esc(reason)}",
        "💡 ถ้ายังไม่เข้า = ไม่ต้องเข้าแล้ว / ถ้าเข้าแล้ว = พิจารณาปิดก่อนถึง SL",
        f"🆔 {sig.id}",
    ])


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


# A phone fits roughly this many monospace characters inside a <pre> block.
# Past it Telegram wraps the line, and the wrapped half lands under the
# wrong heading - which is how a seven-column plan table turned into three
# ragged lines of numbers with no labels near them. Every table below is
# built to stay inside this, and any new one must be measured against it.
LINE_BUDGET = 34


TF_ORDER = ("M1", "M5", "M15", "H1", "H4", "D1")
# the long-hold style never reads anything below H1, so listing M1-M15 here
# would only print empty cells
TF_ORDER_LONG = ("H1", "H4", "D1", "W1")
_ARROW = {"UP": "▲", "DOWN": "▼", "SIDE": "↔"}


_SENTIMENT_TH = {"bullish": "แข็ง", "bearish": "อ่อน", "neutral": "กลาง"}


# ----------------------------------------------------------------------
# Daily Market Analysis - one planning report, sent once each morning.
# ----------------------------------------------------------------------
_BIAS_ICON = {"BUY": "🟢", "SELL": "🔴", "WAIT": "⚪"}
_RISK_ICON = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}


def format_daily_review(rev) -> str:
    """The 05:00 result review, kept to one screen."""
    span = (f"{rev.start.strftime('%d/%m %H:%M')}–{rev.end.strftime('%d/%m %H:%M')}")
    lines = [f"📋 <b>สรุปผลรอบ {span} น.</b>",
             f"<i>ตัดรอบตามตลาดนิวยอร์กปิด ไม่ใช่เที่ยงคืน</i>"]

    if not rev.closed:
        lines += [f"📨 ส่ง {rev.issued} ไม้ · ⏳ ถืออยู่ {len(rev.still_open)} ไม้",
                  ""] + [f"• {_esc(n)}" for n in rev.notes]
        return "\n".join(lines)

    sign = "🟢" if rev.total_r > 0 else "🔴" if rev.total_r < 0 else "⚪"
    lines += [
        f"{sign} <b>{rev.total_r:+.2f}R</b> (โมเดลคาด {rev.expected_r:+.2f}R)"
        f" · ชนะ {rev.win_rate:.0f}%",
        f"📨 ส่ง {rev.issued} · ปิด {len(rev.closed)} "
        f"(ชนะ {rev.wins} แพ้ {rev.losses} เสมอ {rev.breakeven}"
        + (f" หมดอายุ {rev.expired}" if rev.expired else "") + ")"
        + (f" · ⏳ ถืออยู่ {len(rev.still_open)}" if rev.still_open else ""),
        "",
    ]
    # The full Thai outcome ("โดน SL หลัง TP1 (เสมอตัว/กำไรเล็กน้อย)") is
    # wider than a phone can hold, and a wrapped row puts the next pair's
    # name under the R column. The tag says the same thing in one cell.
    rows = [f"{'Pair':<8}{'Side':<5}{'R':>7}  จบที่"]
    for out in sorted(rev.closed, key=lambda o: -o.result_r):
        rows.append(f"{out.signal.symbol:<8}{out.signal.side:<5}"
                    f"{out.result_r:>+7.2f}  {_outcome_tag(out.signal)}")
    lines.append("<pre>" + "\n".join(html.escape(x) for x in rows) + "</pre>")

    if rev.still_open:
        lines.append("⏳ " + " · ".join(
            f"{o.signal.symbol} {o.signal.side} ({o.label})"
            for o in rev.still_open))
    if rev.notes:
        lines += [""] + [f"• {_esc(n)}" for n in rev.notes[:3]]
    if rev.all_time:
        lines.append(f"📚 <i>{_esc(rev.all_time)}</i>")
    lines.append("<i>R คิดตามกฎจัดการไม้ของระบบ (ปิด 1/3 ทุก TP · SL มา BE "
                 "หลัง TP1) ไม่ใช่ผลจากบัญชีจริง</i>")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Session report, compact. Ten long messages per session was more than
# anyone reads on a phone, so a session now fits in two: the tape with
# every pair on one line, then the detail only for pairs that actually
# have a side. A pair with nothing to do needs one line, not a page.
# ----------------------------------------------------------------------

def _tp_list(tp, symbol: str) -> str:
    return " / ".join(_fmt(x, symbol) for x in tp)


def _vote_line(con) -> str:
    """The ballot as one line: who backed it and who argued with it."""
    if con is None:
        return ""
    up = " · ".join(f"{v.label} {v.score:.0f}" for v in con.votes if v.supports)
    down = " · ".join(f"{v.label} {v.score:.0f}" for v in con.votes if v.opposes)
    bits = []
    if up:
        bits.append(f"✅ {up}")
    if down:
        bits.append(f"❌ {down}")
    return " | ".join(bits) if bits else "➖ ไม่มีเทคนิคไหนชี้ชัด"


def format_session_overview(macro_view, reports, risk, slot, counts=None) -> str:
    """Message one: the tape, the risk, and every pair on a single line."""
    from .daily import BANGKOK, SESSIONS
    stamp = datetime.now(timezone.utc).astimezone(BANGKOK).strftime("%d/%m")
    name, why = SESSIONS.get(slot, ("", ""))
    when = f"{slot:02d}:00" if slot is not None else ""
    lines = [f"📅 <b>รอบ{name} {when} น.</b> · {stamp}", f"<i>{why}</i>"]

    if macro_view is not None and macro_view.available:
        icon = {"Risk On": "🟢", "Risk Off": "🔴"}.get(macro_view.risk, "⚪")
        row = " · ".join(
            f"{n} {'▲' if macro_view.changes[n] > 0 else '▼'}{macro_view.changes[n]:+.1f}%"
            for n in ("DXY", "US10Y", "VIX") if n in macro_view.changes)
        lines.append(f"{icon} <b>{_esc(macro_view.risk)}</b> · {row}")
        if macro_view.narrative:
            lines.append(f"<i>{_esc(macro_view.narrative[0])}</i>")
    else:
        lines.append("⚪ <i>ภาพมหภาคดึงไม่ได้รอบนี้ — ไม่ใช้ประกอบการตัดสินใจ</i>")

    label, risk_why = risk
    lines.append(f"⚖️ ความเสี่ยง: <b>{_esc(label)}</b> — {_esc(risk_why)}")
    if counts:
        g, gt, p, pt = counts
        # "1/0" reads like an error; with no quota there is nothing to be
        # out of, only a count of what the market has actually offered.
        quota = (f"ทอง {g} · คู่เงิน {p} <i>(ไม่จำกัดจำนวน)</i>"
                 if gt <= 0 and pt <= 0 else
                 f"ทอง {g}/{gt} · คู่เงิน {p}/{pt}")
        lines.append(f"📨 สัญญาณวันนี้: {quota}")

    ok = [r for r in reports if not r.error]
    rows = [f"{'Pair':<7}{'Bias':<5}{'BUY':>4}{'SELL':>5} ตลาด"]
    for r in sorted(ok, key=lambda r: -max(r.buy_score, r.sell_score)):
        rows.append(f"{r.symbol:<7}{r.bias:<5}{r.buy_score:>4.0f}"
                    f"{r.sell_score:>5.0f} "
                    f"{_REGIME_SHORT.get(r.regime, r.regime[:8])}")
    lines += ["", "<pre>" + "\n".join(html.escape(x) for x in rows) + "</pre>"]

    bad = [r for r in reports if r.error]
    if bad:
        lines.append("⚠️ <i>ไม่มีข้อมูล: " + ", ".join(r.symbol for r in bad) + "</i>")
    lines.append("<i>รายงานเพื่อวางแผน · สัญญาณเข้าจริงส่งแยกเมื่อเงื่อนไขครบ</i>")
    return "\n".join(lines)


def format_session_plans(reports) -> str:
    """Message two: only the pairs with a side, and only what to do about it."""
    live = [r for r in reports if not r.error and r.bias in ("BUY", "SELL")]
    if not live:
        return ""
    lines = ["🎯 <b>คู่ที่มีแผนรอบนี้</b>"]
    for r in sorted(live, key=lambda r: -max(r.buy_score, r.sell_score)):
        plan = r.plan_buy if r.bias == "BUY" else r.plan_sell
        icon = _BIAS_ICON.get(r.bias, "")
        lines += [
            "━━━━━━━━━━━━━━",
            f"{icon} <b>{_esc(r.symbol)} {r.bias}</b> · ราคา {_fmt(r.price, r.symbol)}"
            + (f" <i>({_esc(r.quote_tf)} · {r.price_age_min:.0f} นาที)</i>"
               if getattr(r, "quote_tf", "") else ""),
            f"เข้า <b>{plan.entry_low} – {plan.entry_high}</b> · SL <b>{plan.sl}</b>",
            f"TP {_tp_list(plan.tp, r.symbol)} · RR 1:{plan.rr:.1f}"
            f" · EV {plan.expected_value:+.2f}R",
        ]
        con = getattr(r, "consensus", None)
        if con is not None:
            lines.append(f"🗳 มติ <b>{con.confidence:.0f}</b>/100 · "
                         f"สอดคล้อง {con.agreement:.0%} · {_esc(con.selection_why)}")
            lines.append(_vote_line(con))
        lines.append(f"▸ {_esc(r.now_why)}")
        if plan.waiting_for:
            lines.append("⏳ ยังขาด: " + _esc(" · ".join(plan.waiting_for[:2])))
    lines += ["━━━━━━━━━━━━━━",
              "<i>ถึง TP1 ปิด 1/3 + เลื่อน SL มาจุดเข้า · เสี่ยงไม่เกิน 1% ต่อไม้</i>"]
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Market pulse: where every pair stands right now, and whether it can be
# entered. This costs nothing extra - the scan already produces a view of
# every symbol on every run, it was just never sent anywhere. So the check
# answers the question from the engine that actually issues signals rather
# than from a second opinion computed for the report.
# ----------------------------------------------------------------------

# How far through the eleven-step pipeline a symbol got, in plain terms.
def _pulse_verdict(steps: int) -> tuple:
    if steps >= 11:
        return "🟢", "เข้าได้"
    if steps >= 8:
        return "🟡", "ใกล้แล้ว"
    if steps >= 4:
        return "🟠", "กำลังก่อตัว"
    if steps >= 1:
        return "⚪", "รอ"
    return "⚫", "ไม่มีทาง"


def _best_view(group):
    """The view that got furthest for one symbol, across its styles."""
    return max(group, key=lambda v: (v.steps_passed, v.score))


def format_pulse(views, macro_view=None, session: str = "", live: int = 0,
                 today: int = 0, primary=("XAUUSD",)) -> str:
    """One message: what the market is doing and which pairs are enterable."""
    from .daily import BANGKOK
    stamp = datetime.now(timezone.utc).astimezone(BANGKOK).strftime("%d/%m %H:%M")
    lines = [f"📡 <b>เช็คตลาด</b> · {stamp} น."
             + (f" · {_esc(session)}" if session else " · นอกเวลาหลัก")]

    if macro_view is not None and getattr(macro_view, "available", False):
        icon = {"Risk On": "🟢", "Risk Off": "🔴"}.get(macro_view.risk, "⚪")
        row = " · ".join(
            f"{n} {'▲' if macro_view.changes[n] > 0 else '▼'}{macro_view.changes[n]:+.1f}%"
            for n in ("DXY", "US10Y", "VIX") if n in macro_view.changes)
        lines.append(f"{icon} <b>{_esc(macro_view.risk)}</b> · {row}")
    else:
        lines.append("⚪ <i>ภาพมหภาคดึงไม่ได้รอบนี้</i>")
    lines.append(f"📌 ไม้ที่ถืออยู่ {live} · ส่งวันนี้ {today}")

    by_symbol: dict = {}
    for v in views:
        by_symbol.setdefault(v.symbol, []).append(v)
    if not by_symbol:
        lines.append("⚠️ <i>ไม่มีข้อมูลคู่ไหนเลยรอบนี้ — ฟีดราคาน่าจะมีปัญหา</i>")
        return "\n".join(lines)
    missing = [s for s in primary if s not in by_symbol]
    if missing:
        lines.append("⚠️ <i>ไม่มีข้อมูล " + ", ".join(missing)
                     + " รอบนี้ — ฟีดของคู่นี้น่าจะมีปัญหา</i>")

    best = {sym: _best_view(g) for sym, g in by_symbol.items()}
    ready = [v for v in best.values() if v.steps_passed >= 11]
    close = [v for v in best.values() if 8 <= v.steps_passed < 11]

    # Gold leads whatever its score: it is the primary instrument, and a
    # table sorted purely by progress buries it on a quiet morning.
    def order(item):
        sym, v = item
        return (sym not in primary, -v.steps_passed, -v.score)

    # ASCII header: a Thai heading is not one monospace cell wide, so it
    # cannot sit over the column it names. Only the last cell, which has
    # nothing to line up against, stays in Thai.
    rows = [f"{'Pair':<7}{'Price':>9} {'Dir':<5}{'Step':>5} สถานะ"]
    for sym, v in sorted(best.items(), key=order):
        icon, word = _pulse_verdict(v.steps_passed)
        side = "BUY" if v.direction > 0 else "SELL" if v.direction < 0 else "-"
        rows.append(f"{sym:<7}{_fmt(v.price, sym):>9} {side:<5}"
                    f"{v.steps_passed:>2}/11 {word}")
    lines += ["", "<pre>" + "\n".join(html.escape(r) for r in rows) + "</pre>"]

    # What is actually stopping the ones that are closest, and why.
    watch = sorted(close, key=lambda v: -v.steps_passed)[:4]
    if ready:
        lines.append("🟢 <b>เข้าได้ตอนนี้:</b> "
                     + ", ".join(f"{v.symbol} {'BUY' if v.direction > 0 else 'SELL'}"
                                 for v in ready)
                     + " <i>(ตั๋วเต็มส่งแยกแล้ว)</i>")
    if watch:
        lines.append("🟡 <b>ใกล้เข้าเงื่อนไข — ยังขาด:</b>")
        for v in watch:
            side = "BUY" if v.direction > 0 else "SELL" if v.direction < 0 else "?"
            lines.append(f"• <b>{_esc(v.symbol)}</b> {side} "
                         f"({_esc(v.regime or '-')}) — {_esc(v.waiting or '-')}")
    if not ready and not watch:
        lines.append("⚪ <i>ยังไม่มีคู่ไหนใกล้เข้าเงื่อนไข — "
                     "ตลาดไม่เข้าทาง ไม่ใช่ระบบหยุดทำงาน</i>")

    stale = [v.symbol for v in best.values() if v.data_stale]
    if stale:
        lines.append("⚠️ <i>ข้อมูลช้า: " + ", ".join(stale) + "</i>")
    lines.append("<i>ขั้น n/11 = ผ่านด่านของไพป์ไลน์เข้าเทรดกี่ด่านแล้ว · "
                 "รายงานสถานะ ไม่ใช่คำสั่งเข้า</i>")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# The morning news agenda, straight from the Forex Factory calendar.
# Times are Bangkok, because that is where the reader is; the feed
# publishes UTC. Nothing here is forecast by the bot - the figures are the
# calendar's own forecast and previous values, printed as they arrive.
# ----------------------------------------------------------------------

_IMPACT = {"high": ("🔴", "แรง"), "medium": ("🟠", "กลาง"), "low": ("🟡", "เบา")}

# Which of the instruments we watch a currency actually moves.
_TOUCHES = {
    "USD": "XAUUSD · ทุกคู่ที่มี USD",
    "EUR": "EURUSD · EURJPY · EURGBP",
    "GBP": "GBPUSD · GBPJPY · EURGBP",
    "JPY": "USDJPY · EURJPY · GBPJPY · AUDJPY · CADJPY · CHFJPY",
    "CHF": "USDCHF · CHFJPY",
    "AUD": "AUDUSD · AUDJPY",
    "NZD": "NZDUSD",
    "CAD": "USDCAD · CADJPY",
}


def format_news_agenda(events, error: str = "", pre_minutes: int = 45,
                       post_minutes: int = 45) -> str:
    """What the calendar has for today, and when not to be in the market."""
    from .daily import BANGKOK
    from .news import blackout_windows
    # Date from the events themselves when there are any: a message sent
    # either side of midnight must be headed with the day it describes,
    # not with whatever the clock said as it went out.
    when = (events[0].when.astimezone(BANGKOK) if events
            else datetime.now(timezone.utc).astimezone(BANGKOK))
    lines = [f"📰 <b>ข่าวเศรษฐกิจวันนี้</b> · {when.strftime('%d/%m/%Y')}",
             "<i>เวลาไทย · ที่มา Forex Factory</i>"]

    if error:
        lines += ["", "⚠️ <b>ดึงปฏิทินข่าวไม่สำเร็จ</b>",
                  f"<i>{_esc(error[:150])}</i>",
                  "<i>วันนี้ระบบจะไม่ใช้ข่าวประกอบการตัดสินใจ และจะไม่เดาว่ามีข่าวอะไร "
                  "— เช็คปฏิทินเองก่อนเข้าไม้</i>"]
        return "\n".join(lines)

    if not events:
        lines += ["", "✅ <b>วันนี้ไม่มีข่าวของสกุลที่ระบบติดตาม</b>",
                  "<i>ตลาดมักเดินตามเทคนิคมากกว่าปกติในวันแบบนี้</i>"]
        return "\n".join(lines)

    high = [e for e in events if e.high]
    mid = [e for e in events if e.impact.lower() == "medium"]
    lines.append(f"🔴 แรง {len(high)} · 🟠 กลาง {len(mid)} · รวม {len(events)} รายการ")

    # Low-impact releases are noise on a phone; high and medium are the day.
    shown = [e for e in events if e.impact.lower() in ("high", "medium")]
    if shown:
        lines.append("")
    for e in shown:
        icon, _ = _IMPACT.get(e.impact.lower(), ("⚪", ""))
        local = e.when.astimezone(BANGKOK).strftime("%H:%M")
        lines.append(f"{icon} <b>{local}</b> {e.currency} · {_esc(e.title)}")
        detail = []
        if e.forecast:
            detail.append(f"คาด {_esc(e.forecast)}")
        if e.previous:
            detail.append(f"ครั้งก่อน {_esc(e.previous)}")
        if detail:
            lines.append("      " + " · ".join(detail))

    windows = blackout_windows(events, pre_minutes, post_minutes)
    if windows:
        lines += ["", f"⛔ <b>ช่วงห้ามเข้าไม้</b> (±{pre_minutes} นาทีรอบข่าวแรง)"]
        for start, end in windows:
            lines.append(f"• {start.astimezone(BANGKOK).strftime('%H:%M')}"
                         f" – {end.astimezone(BANGKOK).strftime('%H:%M')} น.")
        lines.append("<i>ระบบจะไม่ส่งสัญญาณในช่วงนี้เอง — สเปรดกว้างและราคากระชาก</i>")

    hit = sorted({e.currency for e in high})
    if hit:
        lines += ["", "🎯 <b>คู่ที่ต้องระวังเป็นพิเศษ</b>"]
        for cur in hit:
            lines.append(f"• <b>{cur}</b> → {_TOUCHES.get(cur, cur)}")

    lines.append("<i>ตัวเลข 'คาด' และ 'ครั้งก่อน' มาจากปฏิทินโดยตรง "
                 "ระบบไม่ได้พยากรณ์เอง</i>")
    return "\n".join(lines)


def format_search(query: str, matches, total: int, kept_days: int = 30) -> str:
    """Answer to a keyword typed into the chat."""
    from .archive import THAI_KIND
    from .daily import BANGKOK
    if not matches:
        return ("\n".join([
            f"🔍 <b>ไม่พบข้อความที่มี “{_esc(query)}”</b>",
            f"<i>ค้นจากข้อความที่บอทส่งย้อนหลัง {kept_days} วัน</i>",
            "",
            "ลองคำอื่น เช่น <code>ทอง</code> · <code>XAUUSD</code> · "
            "<code>buy</code> · <code>ข่าว</code> · <code>สรุปผล</code>",
            "พิมพ์หลายคำได้ = ต้องมีครบทุกคำ เช่น <code>ทอง buy</code>",
        ]))

    lines = [f"🔍 <b>“{_esc(query)}”</b> — พบ {total} รายการ"
             + (f" · แสดง {len(matches)} ล่าสุด" if total > len(matches) else "")]
    for e in matches:
        when = e.when().astimezone(BANGKOK).strftime("%d/%m %H:%M")
        kind = THAI_KIND.get(e.kind, e.kind)
        lines += ["━━━━━━━━━━━━━━",
                  f"🕘 <b>{when}</b> น. · {kind}"
                  + (f" · {' '.join(e.symbols[:3])}" if e.symbols else ""),
                  f"<i>{_esc(e.title)}</i>"]
        snippet = _snippet(e.text, query)
        if snippet:
            lines.append(_esc(snippet))
    lines.append("━━━━━━━━━━━━━━")
    lines.append(f"<i>ค้นย้อนหลัง {kept_days} วัน · พิมพ์คำใหม่เพื่อค้นอีกครั้ง</i>")
    return "\n".join(lines)


def _snippet(text: str, query: str, width: int = 150) -> str:
    """The part of the message the keyword actually appears in.

    Showing the top of a long message would usually miss the reason it
    matched, which makes the result look wrong even when it is right.
    """
    body = " ".join(text.split())
    for word in query.lower().split():
        at = body.lower().find(word)
        if at >= 0:
            start = max(0, at - width // 3)
            end = min(len(body), start + width)
            return ("…" if start else "") + body[start:end] + ("…" if end < len(body) else "")
    return body[:width] + ("…" if len(body) > width else "")


def format_plan_status(board, now=None) -> str:
    """Where every plan stands, with the plan itself alongside the status.

    The status alone ("reached TP2") is only half an answer: acting on it
    means knowing where TP3 and the stop actually are, and those are on a
    ticket sent hours ago. So each row carries its own levels.
    """
    from .daily import BANGKOK
    from .review import stage_of
    stamp = (now or datetime.now(timezone.utc)).astimezone(BANGKOK)
    lines = [f"📌 <b>สถานะแผนทั้งหมด</b> · {stamp.strftime('%d/%m %H:%M')} น."]

    total = (len(board.running) + len(board.won) + len(board.lost)
             + len(board.cancelled))
    if not total:
        lines.append("<i>ยังไม่มีแผนที่เปิดอยู่หรือปิดใน 24 ชม.ที่ผ่านมา</i>")
        return "\n".join(lines)

    def rows_running(sigs):
        """An open plan is a decision to make, so it gets its levels in full."""
        rows = []
        for g in sigs:
            if rows:
                rows.append("")
            rows.append(f"{g.symbol} {g.side} · {stage_of(g)}")
            rows += _plan_rows(g)
        return rows

    def rows_closed(sigs):
        """A finished plan is history: where it went in, where it came out.

        ASCII header - Thai glyphs do not hold a monospace column on mobile.
        """
        rows = [f"{'Pair':<7}{'Side':<5}{'Entry':>9}{'Exit':>12}"]
        for g in sigs:
            sym = g.symbol
            out = (g.tp3 if g.tp3_hit else g.sl if g.status == "SL_HIT"
                   else g.tp2 if g.tp2_hit else g.tp1 if g.tp1_hit else None)
            # A cancelled plan expired without ever being filled, so there
            # is no exit price to report - saying "entry" would read as a
            # trade that went nowhere rather than one that never started.
            exit_txt = _fmt(out, sym) if out is not None else "-"
            rows.append(f"{sym:<7}{g.side:<5}{_fmt(g.entry, sym):>9}"
                        f" -> {exit_txt:>8}")
        return rows

    def block(title, sigs, with_stage=False):
        if not sigs:
            return []
        body = rows_running(sigs) if with_stage else rows_closed(sigs)
        return ["", f"<b>{title} ({len(sigs)})</b>",
                "<pre>" + "\n".join(html.escape(b) for b in body) + "</pre>"]

    lines += block("🟢 กำลังดำเนินการ", board.running, with_stage=True)
    lines += block("✅ ครบ TP3", board.won)
    lines += block("🔴 โดน SL", board.lost)
    lines += block("⚪ ยกเลิก", board.cancelled)
    lines.append("<i>ปิดแล้วแสดงย้อนหลัง 24 ชม. · ที่เปิดอยู่แสดงทั้งหมด</i>")
    return "\n".join(lines)


def format_weekly(rev, memory_line: str = "") -> str:
    """The week's tally: how many won, how many lost, how many TPs banked."""
    from .daily import BANGKOK
    span = (f"{rev.start.astimezone(BANGKOK).strftime('%d/%m')}"
            f" – {rev.end.astimezone(BANGKOK).strftime('%d/%m')}")
    lines = [f"📊 <b>สรุปสัปดาห์</b> · {span}"]

    if not rev.issued:
        lines.append("<i>สัปดาห์นี้ไม่มีแผนถูกส่งออกเลย</i>")
        return "\n".join(lines)

    sign = "🟢" if rev.total_r > 0 else "🔴" if rev.total_r < 0 else "⚪"
    lines += [
        f"ส่งแผนทั้งหมด <b>{rev.issued}</b> ไม้",
        f"✅ ชนะ <b>{rev.won}</b> · 🔴 แพ้ <b>{rev.lost}</b> · "
        f"⚪ เสมอ/ยกเลิก <b>{rev.cancelled}</b>"
        + (f" · ⏳ ยังถืออยู่ <b>{rev.still_open}</b>" if rev.still_open else ""),
        f"📈 อัตราชนะ <b>{rev.win_rate:.0f}%</b>",
        f"{sign} ผลรวม <b>{rev.total_r:+.2f}R</b>",
        "",
        "<b>เก็บ TP ได้</b>",
    ]
    t1, t2, t3 = rev.tp_counts
    rows = [f"{'TP1':<6}{t1:>4}", f"{'TP2':<6}{t2:>4}", f"{'TP3':<6}{t3:>4}",
            f"{'TOTAL':<6}{rev.tp_total:>4}"]
    lines.append("<pre>" + "\n".join(html.escape(r) for r in rows) + "</pre>")

    if rev.by_symbol:
        # ASCII header: Thai glyphs do not hold a monospace column on mobile
        srows = [f"{'Pair':<9}{'N':>4}{'R':>9}"]
        for sym, (n, r) in sorted(rev.by_symbol.items(), key=lambda kv: -kv[1][1]):
            srows.append(f"{sym:<9}{n:>4}{r:>+9.2f}")
        lines += ["<b>แยกตามคู่</b>",
                  "<pre>" + "\n".join(html.escape(r) for r in srows) + "</pre>"]
    if memory_line:
        lines.append(f"📚 <i>{_esc(memory_line)}</i>")
    lines.append("<i>R คิดตามกฎจัดการไม้ของระบบ ไม่ใช่ผลจากบัญชีจริง</i>")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Trend Outlook - the full read on one instrument, one message per pair.
# ----------------------------------------------------------------------
_SYMBOL_ICON = {"XAUUSD": "🥇"}
_VOL_TH = {"high": "สูง", "normal": "ปกติ", "low": "ต่ำ"}


def _feed_error_th(text: str) -> str:
    """What a data failure means, before the stack of URLs that proves it.

    The raw error is a hundred characters of connection pool and query
    string. It is still printed underneath - a reader who wants to know
    which feed broke should be able to see it - but the first line has to
    say what happened in words.
    """
    low = (text or "").lower()
    if "429" in low or "quota" in low or "limit" in low:
        return "ดึงราคาไม่ได้ — โควตา API ของรอบนี้เต็ม"
    if "404" in low or "not found" in low:
        return "ดึงราคาไม่ได้ — ฟีดไม่มีคู่นี้ให้"
    if any(k in low for k in ("connection", "timeout", "timed out",
                              "max retries", "proxy", "resolve")):
        return "ดึงราคาไม่ได้ — ฟีดราคาไม่ตอบรอบนี้"
    if "โครงสร้าง" in text or "atr" in low:
        return "ข้อมูลไม่พอจะอ่านโครงสร้างของคู่นี้"
    return "ดึงข้อมูลคู่นี้ไม่สำเร็จรอบนี้"


def _num(value: float, digits: int) -> str:
    """Prices with thousands separators - 3,395.00 reads faster than 3395.0."""
    return f"{value:,.{digits}f}"


def format_outlook(o, now=None, closed: bool = False) -> str:
    """One instrument, one message: trend, levels, what-ifs, and news.

    Deliberately built from flowing lines rather than monospace tables.
    A table has to be measured against the narrowest phone that will ever
    read it, and when it loses that bet the columns land under the wrong
    headings. Text simply wraps, and a wrapped sentence is still a
    sentence.
    """
    from .daily import BANGKOK
    stamp = (now or datetime.now(timezone.utc)).astimezone(BANGKOK)
    icon = _SYMBOL_ICON.get(o.symbol, "💱")
    lines = [f"{icon} <b>{_esc(o.symbol)} · บทวิเคราะห์แนวโน้ม</b>",
             f"🕒 {stamp.strftime('%d/%m %H:%M')} น."]

    if o.error:
        lines.append(f"⚠️ <b>{_esc(_feed_error_th(o.error))}</b>")
        lines.append(f"<i>{_esc(o.error[:120])}</i>")
        lines.append("<i>ไม่มีข้อมูลพอจะวิเคราะห์รอบนี้ — "
                     "ระบบไม่เดาแทน จะวิเคราะห์ใหม่รอบหน้า</i>")
        return "\n".join(lines)

    # Over a closed market the quote is Friday's close, not a stale feed.
    # Reporting it as "2,900 minutes late" would read as a broken bot.
    if closed:
        lines.append(f"💰 ราคาปิดวันศุกร์ <b>{_num(o.price, o.digits)}</b>")
        lines.append("🔒 <i>ตลาดปิดสุดสัปดาห์ — วิเคราะห์จากราคาปิดวันศุกร์ "
                     "ใช้วางแผนสัปดาห์หน้า ราคาจะยังไม่ขยับจนตลาดเปิด "
                     "เช้าวันจันทร์</i>")
    else:
        age = (f" · ช้า {o.price_age_min:.0f} นาที" if o.price_age_min >= 2 else "")
        lines.append(f"💰 ราคา <b>{_num(o.price, o.digits)}</b> "
                     f"<i>({_esc(o.quote_tf)}{age})</i>")

    # --- the big picture ------------------------------------------------
    lines += ["", "<b>━━ ภาพรวม ━━</b>", _esc(o.long_term), _esc(o.alignment)]
    if o.regime:
        vol = _VOL_TH.get(o.volatility, o.volatility)
        lines.append(f"สภาพตลาด: <b>{_esc(o.regime)}</b> "
                     f"(มั่นใจ {o.regime_confidence:.0f}%) · ความผันผวน{vol}")

    # The plain answer first: everything below is the reasoning behind it.
    v_icon = {"BUY": "🟢", "SELL": "🔴"}.get(o.verdict, "⚪")
    v_word = {"BUY": "มีความได้เปรียบฝั่ง BUY",
              "SELL": "มีความได้เปรียบฝั่ง SELL"}.get(
                  o.verdict, "ยังไม่ควรเข้า รอให้ชัดก่อน")
    why = f" — {_esc(o.verdict_why)}" if o.verdict_why else ""
    lines.append(f"{v_icon} <b>ตอนนี้: {v_word}</b>{why}")

    # --- every timeframe ------------------------------------------------
    if o.reads:
        lines += ["", "<b>━━ แนวโน้มรายทามเฟรม ━━</b>"]
        for r in o.reads:
            note = f" · {_esc(r.note)}" if r.note else ""
            lines.append(f"{r.arrow} <b>{r.tf}</b> {r.word}{note}")

    # --- levels, drawn the way a chart is: top down --------------------
    lv = o.level_map
    if lv is not None and (lv.above or lv.below):
        lines += ["", "<b>━━ แนวรับแนวต้านสำคัญ ━━</b>"]
        for n, level in reversed(list(enumerate(lv.above, start=1))):
            lines.append(f"🔺 <b>ต้าน {n}</b> {_num(level.price, o.digits)} "
                         f"{level.stars} · {_esc(level.why)}")
        lines.append(f"▶️ <b>ราคาตอนนี้ {_num(o.price, o.digits)}</b>")
        for n, level in enumerate(lv.below, start=1):
            lines.append(f"🔻 <b>รับ {n}</b> {_num(level.price, o.digits)} "
                         f"{level.stars} · {_esc(level.why)}")
        lines.append("<i>★ ยิ่งมาก = ยิ่งมีเหตุผลหลายอย่างมาชนกันที่ราคานั้น</i>")

    # --- point by point --------------------------------------------------
    if o.scenarios:
        lines += ["", "<b>━━ ถ้ากราฟไปถึงจุดนี้ ━━</b>"]
        for sc in o.scenarios:
            mark = " <i>(ทางที่เทรนด์หนุนอยู่)</i>" if sc.likely else ""
            lines.append(f"{sc.icon} <b>{_esc(sc.trigger)}</b>{mark}")
            lines.append(f"    ↳ {_esc(sc.outcome)}")
    if o.range_note:
        lines.append(f"📏 กรอบที่น่าจะแกว่ง {_esc(o.range_note)}")
    if o.invalidation:
        lines.append(f"🚫 {_esc(o.invalidation)}")

    # --- which techniques actually agree ---------------------------------
    lines += ["", "<b>━━ เทคนิคที่อ่านตรงกัน ━━</b>"]
    if o.techniques_for:
        lines.append("✅ หนุน: " + _esc(" · ".join(o.techniques_for)))
    if o.techniques_against:
        lines.append("❌ ค้าน: " + _esc(" · ".join(o.techniques_against)))
    if not (o.techniques_for or o.techniques_against):
        lines.append("➖ รอบนี้ยังไม่มีเทคนิคไหนชี้ชัดไปทางใดทางหนึ่ง")
    lines.append(f"น้ำหนัก: ฝั่งซื้อ <b>{o.buy_score:.0f}</b> · "
                 f"ฝั่งขาย <b>{o.sell_score:.0f}</b> (เต็ม 100)")

    # --- news, never invented --------------------------------------------
    lines += ["", "<b>━━ ข่าวที่มีผลกับคู่นี้ ━━</b>"]
    if o.news_note:
        lines.append(f"<i>{_esc(o.news_note)}</i>")
    for e in o.events:
        icon2, _ = _IMPACT.get(e.impact.lower(), ("⚪", ""))
        local = e.when.astimezone(BANGKOK).strftime("%H:%M")
        detail = []
        if e.forecast:
            detail.append(f"คาด {_esc(e.forecast)}")
        if e.previous:
            detail.append(f"ก่อนหน้า {_esc(e.previous)}")
        tail = f" <i>({' · '.join(detail)})</i>" if detail else ""
        lines.append(f"{icon2} <b>{local}</b> {e.currency} · "
                     f"{_esc(e.title)}{tail}")

    lines += ["", "⚠️ <i>บทวิเคราะห์เพื่อวางแผน ไม่ใช่คำสั่งเข้า · "
              "จุดเข้าจริงระบบส่งแยกเมื่อเงื่อนไขครบทุกข้อ</i>"]
    return "\n".join(lines)


def format_outlook_header(session_name: str, slot: int, symbols,
                          macro_view=None, now=None,
                          closed: bool = False) -> str:
    """The banner that opens a session's run of per-pair analyses.

    One line saying what is about to arrive and how the world looks, so a
    reader scrolling into fourteen messages knows what they are and can
    stop reading after the pairs they care about.
    """
    from .daily import BANGKOK, SESSIONS
    stamp = (now or datetime.now(timezone.utc)).astimezone(BANGKOK)
    why = SESSIONS.get(slot, ("", ""))[1]
    if closed:
        lines = [f"📊 <b>บทวิเคราะห์สุดสัปดาห์</b> · {stamp.strftime('%d/%m/%Y')}",
                 "<i>ตลาดปิด — อ่านจากราคาปิดวันศุกร์ เพื่อวางแผนสัปดาห์หน้า "
                 "ส่งรอบเดียวตลอดเสาร์อาทิตย์ เพราะกราฟจะไม่ขยับอีกจนวันจันทร์</i>"]
    else:
        lines = [f"📊 <b>บทวิเคราะห์รอบ{session_name} {slot:02d}:00 น.</b> · "
                 f"{stamp.strftime('%d/%m/%Y')}"]
        if why:
            lines.append(f"<i>{_esc(why)}</i>")

    if macro_view is not None and getattr(macro_view, "available", False):
        icon = {"Risk On": "🟢", "Risk Off": "🔴"}.get(macro_view.risk, "⚪")
        row = " · ".join(
            f"{n} {'▲' if macro_view.changes[n] > 0 else '▼'}{macro_view.changes[n]:+.1f}%"
            for n in ("DXY", "US10Y", "VIX") if n in macro_view.changes)
        lines.append(f"{icon} ภาพรวมตลาด: <b>{_esc(macro_view.risk)}</b>"
                     + (f" · {row}" if row else ""))
    else:
        lines.append("⚪ <i>ภาพมหภาครอบนี้ดึงไม่ได้ — ไม่นำมาใช้เป็นเหตุผล</i>")

    lines.append(f"📨 กำลังส่งบทวิเคราะห์ <b>{len(list(symbols))}</b> คู่ "
                 "<i>(คู่ละ 1 ข้อความ)</i>")
    return "\n".join(lines)
