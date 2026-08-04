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
    """Comma-separated symbols, normalised to upper case."""
    raw = os.getenv(name, "").strip() or default
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


def _env_names(name: str, default: str) -> list[str]:
    """Comma-separated identifiers, normalised to lower case.

    Profile keys are lower case, so upper-casing them the way symbols are
    normalised made every configured style look unknown and failed
    validation before a scan could start.
    """
    raw = os.getenv(name, "").strip() or default
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


@dataclass
class Settings:
    """All tunables in one place."""

    # --- Telegram ---------------------------------------------------
    telegram_token: str = field(default_factory=lambda: _env_str("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _env_str("TELEGRAM_CHAT_ID"))

    # --- market data -------------------------------------------------
    twelvedata_key: str = field(default_factory=lambda: _env_str("TWELVEDATA_API_KEY"))
    request_pause: float = field(default_factory=lambda: _env_float("REQUEST_PAUSE", 0.0))
    # Gold is spot only by default. Turning this on lets the loader fall
    # back to COMEX futures when no spot ticker responds - more coverage,
    # but the prices are a different market and will not match the board.
    allow_gold_futures: bool = field(
        default_factory=lambda: _env_bool("ALLOW_GOLD_FUTURES", False))

    # --- trading styles ----------------------------------------------
    profiles: list[str] = field(default_factory=lambda: _env_names(
        "PROFILES", "turbo,scalp,day,trend"))

    # --- symbol universe (priority order matters) --------------------
    tier1: list[str] = field(default_factory=lambda: _env_list("TIER1_SYMBOLS", "XAUUSD"))
    tier2: list[str] = field(default_factory=lambda: _env_list(
        "TIER2_SYMBOLS", "EURUSD,GBPUSD,USDJPY,USDCHF,AUDUSD,NZDUSD,USDCAD"))
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
    # --- adaptive threshold ------------------------------------------
    # Target signals per day (the 7-10 band, paced at its midpoint). The
    # market does not produce setups on schedule, so this cannot be a
    # guarantee - it lowers the score bar when the day is running behind,
    # never below the floor, and every signal is graded so a relaxed one
    # is visibly a relaxed one.
    daily_signal_target: int = field(
        default_factory=lambda: _env_int("DAILY_SIGNAL_TARGET", 9))
    # Gold is the primary instrument and is paced against its own target,
    # so a quiet FX session cannot eat into the gold count and vice versa.
    # Nothing caps it: if gold offers more than this, the extras are sent.
    gold_symbols: list[str] = field(
        default_factory=lambda: _env_list("GOLD_SYMBOLS", "XAUUSD"))
    gold_daily_target: int = field(
        default_factory=lambda: _env_int("GOLD_DAILY_TARGET", 4))
    # Gold spot comes from Twelve Data, whose free plan allows 800 requests
    # a day. Gold needs 8 series per scan, so scanning it every 5 minutes
    # like the pairs would need ~2,300 - well over the limit. Every 15
    # minutes costs 8 x 96 = 768/day, which fits. Raise this if the log
    # ever shows quota errors.
    gold_scan_minutes: int = field(
        default_factory=lambda: _env_int("GOLD_SCAN_MINUTES", 15))
    adaptive_threshold: bool = field(
        default_factory=lambda: _env_bool("ADAPTIVE_THRESHOLD", True))
    min_score_floor: float = field(
        default_factory=lambda: _env_float("MIN_SCORE_FLOOR", 66.0))
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

    # --- news --------------------------------------------------------------
    use_news: bool = field(default_factory=lambda: _env_bool("USE_NEWS", True))
    news_pre_min: int = field(default_factory=lambda: _env_int("NEWS_PRE_MIN", 45))
    news_post_min: int = field(default_factory=lambda: _env_int("NEWS_POST_MIN", 45))
    news_soft_min: int = field(default_factory=lambda: _env_int("NEWS_SOFT_MIN", 120))

    # --- institutional brain (LEVEL 1 / 7) ---------------------------------
    use_macro: bool = field(default_factory=lambda: _env_bool("USE_MACRO", True))
    # the global tape moves slowly and costs 9 Yahoo requests, so it is
    # refreshed on its own clock and cached in the state file between runs
    macro_refresh_minutes: int = field(
        default_factory=lambda: _env_int("MACRO_REFRESH_MINUTES", 60))
    self_learning: bool = field(default_factory=lambda: _env_bool("SELF_LEARNING", True))

    # --- reporting ---------------------------------------------------------
    send_status_report: bool = field(default_factory=lambda: _env_bool("SEND_STATUS_REPORT", False))
    # รายงานวิเคราะห์กราฟทุกกี่นาที (0 = ปิด). ส่งแยกข้อความละ 1 คู่เทรด
    # จึงได้ ~8 ข้อความต่อรอบ — ทุกชั่วโมงคือ ~192 ข้อความ/วัน
    briefing_minutes: int = field(default_factory=lambda: _env_int("BRIEFING_MINUTES", 60))
    # แจ้ง "ยังไม่มีจุดเข้า" ทุกกี่นาที (0 = ปิด) - ต่อท้ายรายงานภาพรวม
    no_setup_notice: bool = field(default_factory=lambda: _env_bool("NO_SETUP_NOTICE", True))

    # --- scoring weights ---------------------------------------------------
    weights: dict[str, float] = field(default_factory=lambda: {
        "smc": _env_float("W_SMC", 30.0),           # BOS/CHoCH + OB + FVG
        "liquidity": _env_float("W_LIQUIDITY", 20.0),
        "trend": _env_float("W_TREND", 15.0),       # multi-timeframe alignment
        "ict": _env_float("W_ICT", 10.0),           # OTE, PO3, kill zone
        "volume": _env_float("W_VOLUME", 5.0),
        "indicator": _env_float("W_INDICATOR", 5.0),
        "rr": _env_float("W_RR", 5.0),
        "spread": _env_float("W_SPREAD", 5.0),
        "news": _env_float("W_NEWS", 5.0),
    })

    def gate(self, name: str, profile_default: bool) -> bool:
        """A pipeline gate: the profile decides unless an env var overrides.

        Repository variables are global, so setting one applies to every
        profile; leaving it unset lets each style keep its own strictness.
        """
        return _env_bool(name, profile_default)

    def number(self, name: str, profile_default: float) -> float:
        """Same idea for numeric thresholds."""
        return _env_float(name, profile_default)

    def is_gold(self, symbol: str) -> bool:
        return symbol in self.gold_symbols

    @property
    def pair_daily_target(self) -> int:
        """What the currency pairs are expected to contribute.

        Gold carries its own quota, so the pairs only have to make up the
        remainder of the daily target.
        """
        return max(0, self.daily_signal_target - self.gold_daily_target)

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
        from .profiles import ALL as PROFILE_ALL
        unknown = [p for p in self.profiles if p not in PROFILE_ALL]
        if unknown:
            problems.append(
                f"PROFILES มีชื่อที่ไม่รู้จัก: {', '.join(unknown)} "
                f"(ใช้ได้: {', '.join(PROFILE_ALL)})")
        if not any(p in PROFILE_ALL for p in self.profiles):
            problems.append("PROFILES ว่าง - ต้องเลือกอย่างน้อย 1 สไตล์")
        return problems
