"""Trading style profiles.

The same SMC engine can be run at three different speeds. Each profile
picks its own timeframe ladder, stop distance, target spacing and gate
strictness, because what counts as a good setup is not the same for a
five-minute scalp and a multi-day trend position.

Profiles are analysed independently, so one symbol can carry a scalp
signal and a swing signal at the same time without them interfering.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Profile:
    """One trading style."""
    name: str                      # key used in config and state
    label: str                     # Thai display name
    emoji: str

    # --- timeframes --------------------------------------------------
    entry_tf: str                  # where entries are read
    htf_major: str                 # sets the overall bias
    htf_mid: str                   # must agree with htf_minor
    htf_minor: str
    confirm_tfs: tuple             # lower TFs that must not oppose

    # --- stop and targets (multiples of ATR / R) ----------------------
    atr_mult_sl: float             # fallback stop when no swing is usable
    min_sl_atr: float
    max_sl_atr: float
    tp_r: tuple                    # (TP1, TP2, TP3) in R

    # --- how strict the pipeline is ------------------------------------
    score_threshold: float
    min_ob_quality: float
    require_bos_choch: bool = True
    require_order_block: bool = True
    require_fvg: bool = True
    require_sweep: bool = True
    require_premium_discount: bool = True
    require_mitigation: bool = True

    # --- lifecycle ------------------------------------------------------
    expiry_hours: int = 12
    hold_time: str = ""            # expected holding time, shown in signals
    note: str = ""                 # style-specific advice in the message

    # --- how this style is announced and paced ---------------------------
    # "short" styles are closed the same day and are announced together;
    # "long" styles are held across days and get their own section, because
    # mixing a five-minute scalp into a multi-day position list is how a
    # swing trade ends up being managed like a scalp.
    horizon: str = "short"
    # How much of the daily-pacing relaxation this style is allowed to take.
    # The bias is deliberate: short-term styles carry the daily count, while
    # a multi-day position is only ever taken at close to full quality.
    pace_weight: float = 1.0

    @property
    def is_long_hold(self) -> bool:
        return self.horizon == "long"

    def timeframes(self) -> list:
        """Every timeframe this profile needs loaded, de-duplicated."""
        wanted = [self.htf_major, self.htf_mid, self.htf_minor,
                  *self.confirm_tfs, self.entry_tf]
        seen, out = set(), []
        for tf in wanted:
            if tf not in seen:
                seen.add(tf)
                out.append(tf)
        return out


# ----------------------------------------------------------------------
# SCALP - fast, small targets, looser gates so setups actually appear
# ----------------------------------------------------------------------
# The higher-timeframe ladder stops at H1 on purpose. Asking H4 and above
# to agree makes a five-minute entry almost impossible, which is why the
# first version produced no scalp signals at all.
SCALP = Profile(
    name="scalp",
    label="Scalping",
    emoji="⚡",
    entry_tf="M5",
    htf_major="H1", htf_mid="M30", htf_minor="M15",
    confirm_tfs=("M15",),
    atr_mult_sl=1.0, min_sl_atr=0.6, max_sl_atr=1.8,
    tp_r=(0.5, 1.0, 1.5),
    score_threshold=75.0,
    min_ob_quality=45.0,
    # a five-minute chart rarely shows a clean FVG *and* a mitigated
    # retest at the same moment; the sweep plus the structure break carry
    # the setup here instead
    require_fvg=False,
    require_mitigation=False,
    expiry_hours=4,
    hold_time="15 นาที – 2 ชั่วโมง",
    note="ไม้สั้น ถือไม่กี่นาทีถึงชั่วโมง — ตั้ง SL/TP ทันทีและอย่าถือข้ามข่าว",
    horizon="short",
    pace_weight=1.0,
)

# Fastest profile: one-minute entries off a M15/M5 structure read. Only
# worth running when the scan cadence is low; at five-minute scans a
# signal can already be a few bars old by the time it arrives.
TURBO = Profile(
    name="turbo",
    label="Scalp M1",
    emoji="🔥",
    entry_tf="M1",
    htf_major="M30", htf_mid="M15", htf_minor="M5",
    confirm_tfs=("M5",),
    atr_mult_sl=1.0, min_sl_atr=0.5, max_sl_atr=1.5,
    tp_r=(0.5, 1.0, 1.5),
    score_threshold=72.0,
    min_ob_quality=40.0,
    require_fvg=False,
    require_mitigation=False,
    require_premium_discount=False,
    expiry_hours=2,
    hold_time="5 – 30 นาที",
    note="ไม้เร็วมาก ถือไม่กี่นาที — ราคาอาจวิ่งไปแล้วตอนได้รับ ให้ดู Entry Zone เป็นหลัก",
    horizon="short",
    # already the loosest bar in the system; let it relax a little less than
    # the M5/M15 styles so the daily count is not carried by M1 noise
    pace_weight=0.8,
)

# ----------------------------------------------------------------------
# DAY - the original balanced profile
# ----------------------------------------------------------------------
DAY = Profile(
    name="day",
    label="Day Trade",
    emoji="📊",
    entry_tf="M15",
    htf_major="D1", htf_mid="H4", htf_minor="H1",
    confirm_tfs=("M30",),
    atr_mult_sl=1.5, min_sl_atr=0.8, max_sl_atr=2.5,
    tp_r=(1.0, 2.0, 3.0),
    score_threshold=90.0,
    min_ob_quality=60.0,
    expiry_hours=12,
    hold_time="2 – 12 ชั่วโมง (ภายในวัน)",
    note="ไม้รายวัน ปิดก่อนจบวันได้ — เลื่อน SL เป็น BE เมื่อถึง TP1",
    horizon="short",
    # the style the daily target is built around
    pace_weight=1.0,
)

# ----------------------------------------------------------------------
# RUN TREND - swing positions, wide targets, let winners run
# ----------------------------------------------------------------------
RUN_TREND = Profile(
    name="trend",
    label="Run Trend",
    emoji="🚀",
    entry_tf="H1",
    htf_major="W1", htf_mid="D1", htf_minor="H4",
    confirm_tfs=("H1",),
    atr_mult_sl=2.0, min_sl_atr=1.0, max_sl_atr=3.5,
    tp_r=(2.0, 4.0, 6.0),
    score_threshold=88.0,
    min_ob_quality=65.0,
    expiry_hours=72,
    hold_time="1 – 5 วัน",
    note="ไม้ยาว ถือข้ามวันถึงสัปดาห์ — ปิดบางส่วนที่ TP1 แล้วปล่อยที่เหลือวิ่ง",
    horizon="long",
    # a position held for days is never worth taking just to fill a daily
    # quota, so this style barely moves off its bar
    pace_weight=0.3,
)

ALL: dict = {p.name: p for p in (TURBO, SCALP, DAY, RUN_TREND)}
DEFAULT_ORDER = ("turbo", "scalp", "day", "trend")


def resolve(names) -> list:
    """Turn configured profile names into Profile objects, in a stable order."""
    wanted = {n.strip().lower() for n in names if n.strip()}
    return [ALL[n] for n in DEFAULT_ORDER if n in wanted and n in ALL]
