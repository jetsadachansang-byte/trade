"""LEVEL 1 - global market analysis.

Reads the instruments that set the tone for everything else - the dollar,
yields, volatility, equities, commodities, crypto - and turns them into a
single market narrative that the rest of the system reasons against.

What this module will not do is guess. Several inputs the brief asks for
(geopolitical risk, central-bank stance, true market liquidity) have no
free machine-readable feed, so they are reported as unavailable rather
than invented. `MacroView.gaps` carries that list into every message.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd
import requests

_YF_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"

# The global tape, in the order a desk would read it.
MACRO_TICKERS = {
    "DXY": "DX-Y.NYB",       # dollar index
    "US10Y": "^TNX",         # 10-year yield
    "VIX": "^VIX",           # equity volatility
    "SP500": "^GSPC",
    "NASDAQ": "^IXIC",
    "DOW": "^DJI",
    "OIL": "CL=F",
    "SILVER": "SI=F",
    "BTC": "BTC-USD",
}

# Inputs the spec asks for that no free feed provides. Stated, never faked.
UNAVAILABLE = (
    "Geopolitical Risk — ไม่มีฟีดที่เครื่องอ่านได้ ต้องติดตามข่าวเอง",
    "Central Bank stance — ต้องอ่านถ้อยแถลง ระบบประเมินแทนไม่ได้",
    "Liquidity จริง (order book / volume จริง) — ฟีดฟรีไม่มีให้",
)

RISK_ON, RISK_OFF, RISK_MIXED = "Risk On", "Risk Off", "Mixed"


@dataclass
class MacroView:
    """The global picture, as far as it can actually be measured."""
    changes: dict = field(default_factory=dict)     # name -> % change on the day
    levels: dict = field(default_factory=dict)      # name -> last close
    risk: str = RISK_MIXED
    risk_score: float = 0.0        # -100 (risk off) .. +100 (risk on)
    usd_bias: int = 0              # +1 dollar strong, -1 weak, 0 unclear
    gold_bias: int = 0             # what the macro picture implies for gold
    narrative: list = field(default_factory=list)
    gaps: list = field(default_factory=lambda: list(UNAVAILABLE))
    errors: list = field(default_factory=list)
    fetched_at: str = ""

    @property
    def available(self) -> bool:
        return bool(self.changes)

    def to_dict(self) -> dict:
        return {"changes": self.changes, "levels": self.levels, "risk": self.risk,
                "risk_score": self.risk_score, "usd_bias": self.usd_bias,
                "gold_bias": self.gold_bias, "narrative": self.narrative,
                "gaps": self.gaps, "errors": self.errors,
                "fetched_at": self.fetched_at}

    @classmethod
    def from_dict(cls, raw: dict) -> "MacroView":
        if not raw:
            return cls(changes={}, errors=["ยังไม่เคยดึงข้อมูลภาพรวมตลาด"])
        return cls(**{k: raw.get(k, v) for k, v in {
            "changes": {}, "levels": {}, "risk": RISK_MIXED, "risk_score": 0.0,
            "usd_bias": 0, "gold_bias": 0, "narrative": [],
            "gaps": list(UNAVAILABLE), "errors": [], "fetched_at": ""}.items()})

    def age_minutes(self, now: datetime) -> float:
        if not self.fetched_at:
            return float("inf")
        return (now - datetime.fromisoformat(self.fetched_at)).total_seconds() / 60.0


def _daily(ticker: str) -> pd.DataFrame:
    """One month of daily candles for a macro ticker."""
    resp = requests.get(
        _YF_URL.format(sym=ticker), timeout=20,
        headers={"User-Agent": "Mozilla/5.0 (compatible; CapitalGuard/1.0)"},
        params={"interval": "1d", "range": "1mo"})
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code}")
    result = (resp.json().get("chart", {}).get("result") or [None])[0]
    if not result:
        raise RuntimeError("empty response")
    quote = result["indicators"]["quote"][0]
    df = pd.DataFrame({"close": quote["close"]},
                      index=pd.to_datetime(result["timestamp"], unit="s", utc=True))
    return df.dropna()


def _pct_change(df: pd.DataFrame) -> float:
    """Latest close against the previous one, in percent."""
    if len(df) < 2:
        return 0.0
    prev, last = float(df["close"].iloc[-2]), float(df["close"].iloc[-1])
    return 0.0 if prev == 0 else (last - prev) / prev * 100.0


def _risk_appetite(ch: dict) -> tuple:
    """Score risk appetite from the tape: equities up and VIX down = risk on.

    Returns (label, score) where score runs -100 (defensive) to +100.
    """
    score = 0.0
    weight_used = 0.0
    for name, weight in (("SP500", 30.0), ("NASDAQ", 25.0), ("DOW", 15.0)):
        if name in ch:
            score += max(-1.0, min(1.0, ch[name] / 1.0)) * weight
            weight_used += weight
    if "VIX" in ch:                      # VIX falls when risk appetite rises
        score += max(-1.0, min(1.0, -ch["VIX"] / 5.0)) * 20.0
        weight_used += 20.0
    if "BTC" in ch:
        score += max(-1.0, min(1.0, ch["BTC"] / 3.0)) * 10.0
        weight_used += 10.0
    if not weight_used:
        return RISK_MIXED, 0.0
    score = score / weight_used * 100.0
    label = RISK_ON if score >= 20 else RISK_OFF if score <= -20 else RISK_MIXED
    return label, round(score, 1)


def _narrate(ch: dict, risk: str, usd: int, gold: int) -> list:
    """The day's story in a few plain sentences, each tied to a number."""
    out = []
    if "DXY" in ch:
        word = "แข็งค่า" if ch["DXY"] > 0.1 else "อ่อนค่า" if ch["DXY"] < -0.1 else "ทรงตัว"
        out.append(f"ดอลลาร์ {word} ({ch['DXY']:+.2f}%) — "
                   f"{'กดดัน' if ch['DXY'] > 0.1 else 'หนุน' if ch['DXY'] < -0.1 else 'เป็นกลางต่อ'}ทองและสินค้าโภคภัณฑ์")
    if "US10Y" in ch:
        word = "ขึ้น" if ch["US10Y"] > 0.5 else "ลง" if ch["US10Y"] < -0.5 else "ทรงตัว"
        out.append(f"บอนด์ยีลด์ 10 ปี {word} ({ch['US10Y']:+.2f}%) — "
                   f"ยีลด์ขึ้นมักกดทอง ยีลด์ลงมักหนุนทอง")
    if "VIX" in ch:
        state = "พุ่ง" if ch["VIX"] > 5 else "ลด" if ch["VIX"] < -5 else "นิ่ง"
        out.append(f"VIX {state} ({ch['VIX']:+.2f}%) — "
                   f"{'ตลาดกลัว เงินหนีเข้าสินทรัพย์ปลอดภัย' if ch['VIX'] > 5 else 'ความกลัวคลี่คลาย'}")
    if risk == RISK_ON:
        out.append("โหมด <b>Risk On</b> — เงินไหลเข้าสินทรัพย์เสี่ยง ทองมักถูกลดน้ำหนัก")
    elif risk == RISK_OFF:
        out.append("โหมด <b>Risk Off</b> — เงินหลบเข้าสินทรัพย์ปลอดภัย หนุนทอง")
    else:
        out.append("โหมด <b>Mixed</b> — ตลาดยังไม่เลือกข้าง")
    if gold:
        out.append(f"ภาพมหภาคเอียงไปทาง <b>{'หนุนทอง' if gold > 0 else 'กดทอง'}</b>")
    else:
        out.append("ภาพมหภาคต่อทอง <b>ยังไม่ชัด</b> — ปัจจัยหักล้างกันเอง")
    return out


def build(now: datetime, fetch=None) -> MacroView:
    """Fetch the global tape and reduce it to a narrative.

    `fetch` is injectable so the pipeline can be tested without network.
    """
    fetch = fetch or _daily
    view = MacroView(fetched_at=now.isoformat(timespec="seconds"))

    for name, ticker in MACRO_TICKERS.items():
        try:
            df = fetch(ticker)
            view.changes[name] = round(_pct_change(df), 2)
            view.levels[name] = round(float(df["close"].iloc[-1]), 4)
        except Exception as exc:            # noqa: BLE001 - one dead ticker is survivable
            view.errors.append(f"{name}: {exc}")

    if not view.changes:
        view.narrative = ["ไม่สามารถดึงข้อมูลภาพรวมตลาดได้ — "
                          "จะไม่ใช้ปัจจัยมหภาคประกอบการตัดสินใจรอบนี้"]
        return view

    view.risk, view.risk_score = _risk_appetite(view.changes)

    dxy = view.changes.get("DXY", 0.0)
    view.usd_bias = 1 if dxy > 0.15 else -1 if dxy < -0.15 else 0

    # Gold: hurt by a strong dollar and rising yields, helped by fear.
    gold_score = 0.0
    gold_score -= max(-1.0, min(1.0, dxy / 0.5)) * 40.0
    gold_score -= max(-1.0, min(1.0, view.changes.get("US10Y", 0.0) / 2.0)) * 30.0
    gold_score -= max(-1.0, min(1.0, view.risk_score / 100.0)) * 30.0
    view.gold_bias = 1 if gold_score > 20 else -1 if gold_score < -20 else 0

    view.narrative = _narrate(view.changes, view.risk, view.usd_bias, view.gold_bias)
    return view


def bias_for(view: MacroView, symbol: str) -> tuple:
    """(direction, why) that the macro picture implies for one instrument.

    Only the dollar leg is modelled for FX, because that is the part the
    tape actually measures. Anything less certain returns 0.
    """
    if not view.available:
        return 0, "ไม่มีข้อมูลภาพรวมตลาด"
    if symbol in ("XAUUSD", "XAGUSD"):
        if view.gold_bias:
            return view.gold_bias, ("มหภาคหนุนทอง" if view.gold_bias > 0
                                    else "มหภาคกดทอง")
        return 0, "มหภาคต่อทองยังไม่ชัด"
    if not view.usd_bias:
        return 0, "ดอลลาร์ยังไม่เลือกทาง"
    if symbol.startswith("USD"):
        return view.usd_bias, ("ดอลลาร์แข็ง หนุนคู่ที่ USD อยู่หน้า"
                               if view.usd_bias > 0 else "ดอลลาร์อ่อน กดคู่ที่ USD อยู่หน้า")
    if symbol.endswith("USD"):
        return -view.usd_bias, ("ดอลลาร์แข็ง กดคู่ที่ USD อยู่หลัง"
                                if view.usd_bias > 0 else "ดอลลาร์อ่อน หนุนคู่ที่ USD อยู่หลัง")
    return 0, "คู่ไขว้ ไม่ขึ้นกับดอลลาร์โดยตรง"
