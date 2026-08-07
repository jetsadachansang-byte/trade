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
        "PROFILES", "turbo,scalp,day,intraday,trend"))

    # --- ไทม์เฟรมต่ำสุดที่ "เข้าออเดอร์" ได้ ------------------------------
    # สเปรดของคู่เงินบางคู่กินไม้สั้นหมดตั้งแต่ยังไม่ทันวิ่ง จึงห้ามเข้าต่ำ
    # กว่า H1 ทองสเปรดแคบกว่ามากเมื่อเทียบกับระยะที่มันวิ่ง จึงเข้า M1 ได้
    # ข้อจำกัดนี้คุมเฉพาะ "จุดเข้า" — ไทม์เฟรมเล็กยังถูกอ่านเพื่อวิเคราะห์อยู่
    min_entry_tf: str = field(
        default_factory=lambda: _env_str("MIN_ENTRY_TF", "H1").upper())
    gold_min_entry_tf: str = field(
        default_factory=lambda: _env_str("GOLD_MIN_ENTRY_TF", "M1").upper())

    # --- SL ต้องกว้างพอที่จะไม่ถูกสเปรดและการแกว่งปกติกินทิ้ง -------------
    # ATR อย่างเดียวเคยให้ SL แคบกว่าสเปรด (CADJPY เคยได้ SL 0.1 pip ขณะที่
    # สเปรดราว 2.5 pip) SL ที่แคบกว่าสเปรดไม่ใช่ SL แคบ แต่คือไม้ที่แพ้ไปแล้ว
    min_sl_spreads: float = field(
        default_factory=lambda: _env_float("MIN_SL_SPREADS", 6.0))
    min_sl_candles: float = field(
        default_factory=lambda: _env_float("MIN_SL_CANDLES", 1.2))
    # ถ้า SL ขั้นต่ำกว้างกว่าเพดานของสไตล์เกินเท่านี้ แปลว่าไทม์เฟรมนี้เทรด
    # คู่นี้ไม่ได้ในสภาพตลาดตอนนี้ — ไม่ส่งดีกว่าส่งไม้ที่เรขาคณิตพัง
    sl_floor_stretch: float = field(
        default_factory=lambda: _env_float("SL_FLOOR_STRETCH", 2.5))

    def min_entry_minutes(self, symbol: str) -> int:
        """Smallest entry timeframe this symbol may trade, in minutes."""
        from .profiles import TF_MINUTES
        name = self.gold_min_entry_tf if self.is_gold(symbol) else self.min_entry_tf
        return TF_MINUTES.get(name, 0)

    def entry_allowed(self, symbol: str, prof) -> bool:
        return prof.entry_minutes >= self.min_entry_minutes(symbol)

    # --- symbol universe (priority order matters) --------------------
    tier1: list[str] = field(default_factory=lambda: _env_list("TIER1_SYMBOLS", "XAUUSD"))
    tier2: list[str] = field(default_factory=lambda: _env_list(
        "TIER2_SYMBOLS", "EURUSD,GBPUSD,USDJPY,USDCHF,AUDUSD,NZDUSD,USDCAD"))
    # The crosses were mapped for both providers but never switched on,
    # so the scan was covering eight instruments instead of fourteen.
    tier3: list[str] = field(default_factory=lambda: _env_list(
        "TIER3_SYMBOLS", "EURJPY,GBPJPY,EURGBP,AUDJPY,CADJPY,CHFJPY"))
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
    # 0 = ไม่จำกัดจำนวนต่อวัน ระบบจะส่งทุกจุดเข้าที่ผ่านเกณฑ์ทั้งหมด
    # เมื่อไม่มีเป้า การผ่อนเกณฑ์ตามจังหวะก็ไม่มีความหมาย เกณฑ์คะแนนจึงยืน
    # ที่พื้น (MIN_SCORE_FLOOR) ตลอด — ได้ไม้มากที่สุดเท่าที่ระบบยอมรับได้
    daily_signal_target: int = field(
        default_factory=lambda: _env_int("DAILY_SIGNAL_TARGET", 0))
    # Gold is the primary instrument and is paced against its own target,
    # so a quiet FX session cannot eat into the gold count and vice versa.
    # Nothing caps it: if gold offers more than this, the extras are sent.
    gold_symbols: list[str] = field(
        default_factory=lambda: _env_list("GOLD_SYMBOLS", "XAUUSD"))
    gold_daily_target: int = field(
        default_factory=lambda: _env_int("GOLD_DAILY_TARGET", 0))
    # Gold spot comes from Twelve Data, whose free plan allows 800 requests
    # a day. Gold needs 8 series per scan, so scanning it every 5 minutes
    # like the pairs would need ~2,300 - well over the limit. Every 15
    # minutes costs 8 x 96 = 768/day, which fits. Raise this if the log
    # ever shows quota errors.
    gold_scan_minutes: int = field(
        default_factory=lambda: _env_int("GOLD_SCAN_MINUTES", 15))
    adaptive_threshold: bool = field(
        default_factory=lambda: _env_bool("ADAPTIVE_THRESHOLD", True))
    # Fourteen instruments looking for seven signals means the bar hardly
    # has to move, so the floor can afford to sit higher than it did when
    # eight instruments were chasing fourteen.
    min_score_floor: float = field(
        default_factory=lambda: _env_float("MIN_SCORE_FLOOR", 70.0))
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
    # Require every higher timeframe to be actively trending, not merely
    # non-opposing. Far fewer setups; set true to restore it.
    strict_structure: bool = field(
        default_factory=lambda: _env_bool("STRICT_STRUCTURE", False))
    # A regime this unclear is not worth trading. Kept low because an
    # unconfident regime already blends its weights back toward neutral,
    # so gating hard here would penalise it twice.
    min_regime_confidence: float = field(
        default_factory=lambda: _env_float("MIN_REGIME_CONFIDENCE", 20.0))

    # --- Adaptive Multi-Strategy: the Strategy Voting System -------------
    # Each technique the regime selected scores the trade on its own, and
    # the spread of those scores adjusts the confidence. Set false to fall
    # back to the blended category score alone.
    strategy_voting: bool = field(
        default_factory=lambda: _env_bool("STRATEGY_VOTING", True))
    # How much the consensus is allowed to move the final score, in points
    # per 50 points of consensus confidence away from neutral.
    vote_influence: float = field(
        default_factory=lambda: _env_float("VOTE_INFLUENCE", 0.30))
    # Refuse the trade when the called techniques openly contradict each
    # other. This is the brief's "conflict -> WAIT" rule.
    vote_conflict_blocks: bool = field(
        default_factory=lambda: _env_bool("VOTE_CONFLICT_BLOCKS", True))

    # --- reporting ---------------------------------------------------------
    # Status is sent only on request now (--status). It was a repository
    # variable, which meant one setting turned every scan into a message.

    # --- สถานะแผนรายชั่วโมง + สรุปรายสัปดาห์ -----------------------------
    # กระดานสถานะ: แผนไหนยังวิ่ง แผนไหนครบ TP3 แผนไหนโดน SL (0 = ปิด)
    plan_status_hours: float = field(
        default_factory=lambda: _env_float("PLAN_STATUS_HOURS", 1.0))
    # แสดงแผนที่ปิดไปแล้วย้อนหลังกี่ชั่วโมง (ที่ยังเปิดอยู่แสดงเสมอ)
    plan_status_window: int = field(
        default_factory=lambda: _env_int("PLAN_STATUS_WINDOW", 24))
    # สรุปทั้งสัปดาห์ วันอาทิตย์เช้า
    weekly_summary: bool = field(
        default_factory=lambda: _env_bool("WEEKLY_SUMMARY", True))
    weekly_summary_hour: int = field(
        default_factory=lambda: _env_int("WEEKLY_SUMMARY_HOUR", 6))

    # --- ค้นข้อความเก่าด้วยคีย์เวิร์ด ------------------------------------
    # บอทเก็บข้อความที่ส่งไปแล้วไว้ พิมพ์คำค้นเข้ามาใน Telegram แล้วมันจะ
    # ตอบกลับพร้อมข้อความที่ตรงคำนั้น (ตอบในรอบสแกนถัดไป)
    message_archive: bool = field(
        default_factory=lambda: _env_bool("MESSAGE_ARCHIVE", True))
    archive_days: int = field(
        default_factory=lambda: _env_int("ARCHIVE_DAYS", 30))
    search_results: int = field(
        default_factory=lambda: _env_int("SEARCH_RESULTS", 5))

    # --- ปฏิทินข่าววันนี้ (News Agenda) ----------------------------------
    # ส่งตอน 06:00 น. ทุกวัน ดึงจาก Forex Factory ตัวเดียวกับที่ระบบใช้อยู่
    news_agenda: bool = field(
        default_factory=lambda: _env_bool("NEWS_AGENDA", True))
    news_agenda_hour: int = field(
        default_factory=lambda: _env_int("NEWS_AGENDA_HOUR", 6))

    # --- เช็คชีพจรตลาด (Market Pulse) ------------------------------------
    # ตารางสรุปว่าแต่ละคู่เดินไปถึงขั้นไหนของไพป์ไลน์เข้าเทรด
    # ปิดไว้ (0) เพราะบทวิเคราะห์แนวโน้มรายคู่ตอบคำถามเดียวกันแบบละเอียด
    # กว่า — ส่งทั้งสองอย่างคือส่งเรื่องเดิมซ้ำสองรอบ เปิดกลับได้ด้วย
    # PULSE_HOURS=3 ถ้าอยากได้ตารางสั้น ๆ ระหว่างรอบวิเคราะห์
    pulse_hours: float = field(
        default_factory=lambda: _env_float("PULSE_HOURS", 0.0))

    # --- บทวิเคราะห์ตลาดรายวัน (Daily Market Analysis) ------------------
    daily_report: bool = field(default_factory=lambda: _env_bool("DAILY_REPORT", True))
    # ชั่วโมงตามเวลาไทย (Asia/Bangkok) ที่จะส่งบทวิเคราะห์
    # ส่ง 3 รอบต่อวันตามเซสชั่นตลาด (เวลาไทย): เช้าก่อนลอนดอน · บ่ายลอนดอน
    # เข้า · ค่ำนิวยอร์กเข้า แผนที่วางไว้ตอนเช้าใช้อธิบายเบรกตอนลอนดอนไม่ได้
    daily_report_hours: list = field(default_factory=lambda: sorted(
        {int(h) for h in _env_list("DAILY_REPORT_HOURS", "6,14,20")
         if h.strip().isdigit() and 0 <= int(h) <= 23}))
    # --- สรุปผลรายวัน (Daily Result Review) ----------------------------
    # ส่งตอนตี 5 ก่อนบทวิเคราะห์ 1 ชม. เพราะรอบตลาดนิวยอร์กเพิ่งปิด
    daily_summary: bool = field(
        default_factory=lambda: _env_bool("DAILY_SUMMARY", True))
    daily_summary_hour: int = field(
        default_factory=lambda: _env_int("DAILY_SUMMARY_HOUR", 5))

    daily_symbols: list[str] = field(default_factory=lambda: _env_list(
        "DAILY_SYMBOLS",
        "XAUUSD,EURUSD,GBPUSD,USDJPY,USDCHF,AUDUSD,NZDUSD,USDCAD"))

    # --- บทวิเคราะห์แนวโน้มรายคู่ (Trend Outlook) -----------------------
    # หนึ่งคู่ = หนึ่งข้อความ ครบทั้งเทรนด์ทุกทามเฟรม แนวรับแนวต้าน
    # สถานการณ์จุดต่อจุด และข่าวที่กระทบคู่นั้น
    outlook: bool = field(default_factory=lambda: _env_bool("OUTLOOK", True))
    # ทองส่งทุกกี่ชั่วโมง (คู่อื่นส่งตามเซสชั่นตลาดใน DAILY_REPORT_HOURS)
    outlook_gold_hours: float = field(
        default_factory=lambda: _env_float("OUTLOOK_GOLD_HOURS", 1.0))
    # ว่างไว้ = ใช้ทุกคู่ใน universe (tier1+tier2+tier3)
    outlook_symbols: list[str] = field(
        default_factory=lambda: _env_list("OUTLOOK_SYMBOLS", ""))


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
    def unlimited(self) -> bool:
        """No daily quota: send every entry that clears the gates."""
        return self.daily_signal_target <= 0

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

    def outlook_universe(self) -> list[str]:
        """Instruments the trend outlook covers, gold first.

        Defaults to everything the scanner watches: "every pair" means
        every pair, and a shorter hard-coded list is how the crosses ended
        up analysed for entries but never reported on.
        """
        if self.outlook_symbols:
            return list(self.outlook_symbols)
        return [sym for sym, _ in self.universe()]

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
