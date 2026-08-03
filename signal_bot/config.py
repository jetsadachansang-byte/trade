"""Configuration for the signal bot.

Everything is read from environment variables so the whole bot can be
configured from GitHub's web UI (Settings -> Secrets and variables)
without touching code - which means it can be set up entirely from a
phone.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_str(name: str, default: str = "") -> str:
    """Read a string setting, treating an empty value as "not set".

    GitHub Actions injects an empty string for any `vars.X` that has no
    repository variable defined, so an empty value must fall back to the
    default rather than being taken literally.
    """
    return os.getenv(name, "").strip() or default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _env_list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, "").strip() or default
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


@dataclass
class Settings:
    """All tunables in one place."""

    # --- Telegram ---------------------------------------------------
    telegram_token: str = field(default_factory=lambda: _env_str("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _env_str("TELEGRAM_CHAT_ID"))

    # --- market data -------------------------------------------------
    twelvedata_key: str = field(default_factory=lambda: _env_str("TWELVEDATA_API_KEY"))
    request_pause: float = field(default_factory=lambda: _env_float("REQUEST_PAUSE", 0.0))

    # --- symbol universe (priority order matters) --------------------
    tier1: list[str] = field(default_factory=lambda: _env_list("TIER1_SYMBOLS", "XAUUSD"))
    tier2: list[str] = field(default_factory=lambda: _env_list(
        "TIER2_SYMBOLS", "EURUSD,GBPUSD,USDJPY"))
    tier3: list[str] = field(default_factory=lambda: _env_list("TIER3_SYMBOLS", ""))
    tier3_extra: float = field(default_factory=lambda: _env_float("TIER3_EXTRA", 2.0))

    # --- timeframes ---------------------------------------------------
    entry_timeframe: str = field(default_factory=lambda: _env_str("ENTRY_TIMEFRAME", "M15"))

    # --- signal quality ------------------------------------------------
    score_threshold: float = field(default_factory=lambda: _env_float("SCORE_THRESHOLD", 90.0))
    # 0 = ไม่จำกัด (ทั้งต่อวันและต่อรอบสแกน)
    max_signals_per_day: int = field(default_factory=lambda: _env_int("MAX_SIGNALS_PER_DAY", 0))
    max_signals_per_run: int = field(default_factory=lambda: _env_int("MAX_SIGNALS_PER_RUN", 0))
    cooldown_minutes: int = field(default_factory=lambda: _env_int("COOLDOWN_MINUTES", 0))
    signal_expiry_hours: int = field(default_factory=lambda: _env_int("SIGNAL_EXPIRY_HOURS", 12))

    # --- SMC pipeline gates --------------------------------------------
    require_bos_choch: bool = field(default_factory=lambda: _env_bool("REQUIRE_BOS_CHOCH", True))
    require_order_block: bool = field(default_factory=lambda: _env_bool("REQUIRE_ORDER_BLOCK", True))
    min_ob_quality: float = field(default_factory=lambda: _env_float("MIN_OB_QUALITY", 60.0))
    require_fvg: bool = field(default_factory=lambda: _env_bool("REQUIRE_FVG", True))
    require_sweep: bool = field(default_factory=lambda: _env_bool("REQUIRE_SWEEP", True))
    require_premium_discount: bool = field(
        default_factory=lambda: _env_bool("REQUIRE_PREMIUM_DISCOUNT", True))
    discount_max: float = field(default_factory=lambda: _env_float("DISCOUNT_MAX", 0.5))
    require_mitigation: bool = field(default_factory=lambda: _env_bool("REQUIRE_MITIGATION", True))
    require_liquidity_target: bool = field(
        default_factory=lambda: _env_bool("REQUIRE_LIQUIDITY_TARGET", False))
    require_ote: bool = field(default_factory=lambda: _env_bool("REQUIRE_OTE", False))

    # --- structure detection --------------------------------------------
    swing_bars: int = field(default_factory=lambda: _env_int("SWING_BARS", 3))
    struct_lookback: int = field(default_factory=lambda: _env_int("STRUCT_LOOKBACK", 80))
    smc_window: int = field(default_factory=lambda: _env_int("SMC_WINDOW", 30))

    # --- stop loss / targets ---------------------------------------------
    atr_period: int = field(default_factory=lambda: _env_int("ATR_PERIOD", 14))
    atr_mult_sl: float = field(default_factory=lambda: _env_float("ATR_MULT_SL", 1.5))
    min_sl_atr: float = field(default_factory=lambda: _env_float("MIN_SL_ATR", 0.8))
    max_sl_atr: float = field(default_factory=lambda: _env_float("MAX_SL_ATR", 2.5))
    tp1_r: float = field(default_factory=lambda: _env_float("TP1_R", 1.0))
    tp2_r: float = field(default_factory=lambda: _env_float("TP2_R", 2.0))
    tp3_r: float = field(default_factory=lambda: _env_float("TP3_R", 3.0))

    # --- sessions / kill zones (UTC hours) --------------------------------
    use_kill_zones: bool = field(default_factory=lambda: _env_bool("USE_KILL_ZONES", False))
    london_kz: tuple[int, int] = field(
        default_factory=lambda: (_env_int("LONDON_KZ_START", 7), _env_int("LONDON_KZ_END", 10)))
    ny_kz: tuple[int, int] = field(
        default_factory=lambda: (_env_int("NY_KZ_START", 13), _env_int("NY_KZ_END", 16)))

    # --- reporting ---------------------------------------------------------
    send_status_report: bool = field(default_factory=lambda: _env_bool("SEND_STATUS_REPORT", False))
    # รายงานวิเคราะห์กราฟทุกกี่นาที (0 = ปิด). สแกนทุก 5 นาทีแต่รายงาน
    # ทุก 5 นาทีจะได้ ~288 ข้อความ/วัน จึงตั้งค่าเริ่มต้นไว้ที่ 60 นาที
    briefing_minutes: int = field(default_factory=lambda: _env_int("BRIEFING_MINUTES", 60))

    # --- scoring weights ---------------------------------------------------
    weights: dict[str, float] = field(default_factory=lambda: {
        "structure": _env_float("W_STRUCTURE", 25.0),
        "liquidity": _env_float("W_LIQUIDITY", 20.0),
        "bos_choch": _env_float("W_BOS_CHOCH", 20.0),
        "orderblock": _env_float("W_ORDERBLOCK", 15.0),
        "fvg": _env_float("W_FVG", 10.0),
        "volume": _env_float("W_VOLUME", 5.0),
        "indicator": _env_float("W_INDICATOR", 5.0),
    })

    def universe(self) -> list[tuple[str, int]]:
        """(symbol, tier) pairs in send-priority order."""
        pairs: list[tuple[str, int]] = []
        for tier, symbols in ((1, self.tier1), (2, self.tier2), (3, self.tier3)):
            pairs.extend((sym, tier) for sym in symbols)
        return pairs

    def validate(self) -> list[str]:
        """Human-readable configuration problems, empty when all good."""
        problems = []
        if not self.telegram_token:
            problems.append("TELEGRAM_BOT_TOKEN ยังไม่ได้ตั้ง")
        if not self.telegram_chat_id:
            problems.append("TELEGRAM_CHAT_ID ยังไม่ได้ตั้ง")
        if not self.universe():
            problems.append("ไม่มี symbol ให้วิเคราะห์ (TIER1_SYMBOLS ว่าง)")
        if self.entry_timeframe not in ("M15", "H1"):
            problems.append("ENTRY_TIMEFRAME ต้องเป็น M15 หรือ H1")
        return problems
