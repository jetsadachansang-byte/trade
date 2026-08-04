"""LEVEL 4 and 5 - strategy selection and dynamic weighting.

The rule the brief sets is that the market picks the strategy, not the
other way round, and that not every strategy runs at once. So each regime
maps to the handful of approaches that actually suit it, and the scoring
weights are rebuilt from that selection on every pass.

Only approaches this system can genuinely evaluate are listed. Elliott
Wave, Harmonic patterns and true Order Flow need either wave labelling or
real depth-of-market data, neither of which exists here, so they are named
in UNIMPLEMENTED and never claimed as a reason for a trade.
"""
from __future__ import annotations

from . import regime as R

# Scoring categories the analyser knows how to compute. A "strategy" here
# is a named way of reading the market that maps onto those categories.
#   smc       - structure, order blocks, imbalance
#   liquidity - sweeps, pools, premium/discount
#   trend     - multi-timeframe alignment
#   ict       - OTE, power of three, kill zones
#   volume    - participation behind the move
#   indicator - confirmation only, never a reason
#   rr        - what the setup pays for the risk
#   spread    - execution conditions
#   news      - calendar state
#   macro     - the global tape (LEVEL 1)

UNIMPLEMENTED = (
    "Elliott Wave — ต้องนับคลื่นซึ่งตีความได้หลายแบบ ระบบไม่เดา",
    "Harmonic Pattern — ยังไม่ได้ทำ ไม่อ้างเป็นเหตุผล",
    "Order Flow / Market Profile จริง — ต้องมี order book ฟีดฟรีไม่มี",
    "VSA เต็มรูปแบบ — FX ไม่มี volume จริง มีแต่ tick volume",
)

# regime -> (strategies named in the message, weight overlay)
PLAYBOOK = {
    R.STRONG_BULL: (("SMC", "Trend Following", "ICT", "Momentum"),
                    {"smc": 32, "trend": 22, "liquidity": 16, "ict": 10,
                     "macro": 8, "rr": 5, "volume": 3, "spread": 2, "news": 2}),
    R.STRONG_BEAR: (("SMC", "Trend Following", "ICT", "Momentum"),
                    {"smc": 32, "trend": 22, "liquidity": 16, "ict": 10,
                     "macro": 8, "rr": 5, "volume": 3, "spread": 2, "news": 2}),
    R.WEAK_BULL: (("SMC", "Dow Theory", "Trend Following"),
                  {"smc": 30, "trend": 18, "liquidity": 18, "ict": 8,
                   "macro": 10, "rr": 6, "volume": 4, "spread": 3, "news": 3}),
    R.WEAK_BEAR: (("SMC", "Dow Theory", "Trend Following"),
                  {"smc": 30, "trend": 18, "liquidity": 18, "ict": 8,
                   "macro": 10, "rr": 6, "volume": 4, "spread": 3, "news": 3}),
    R.RANGE: (("Wyckoff Range", "Mean Reversion", "Support/Resistance"),
              {"liquidity": 30, "smc": 24, "rr": 12, "macro": 10,
               "trend": 8, "volume": 6, "ict": 4, "spread": 3, "news": 3}),
    R.MEAN_REVERSION: (("Mean Reversion", "Support/Resistance", "Wyckoff Range"),
                       {"liquidity": 32, "smc": 22, "rr": 12, "macro": 10,
                        "trend": 6, "volume": 6, "ict": 5, "spread": 4, "news": 3}),
    R.COMPRESSION: (("Volatility Compression", "Breakout Preparation"),
                    {"smc": 26, "liquidity": 24, "rr": 14, "macro": 10,
                     "trend": 10, "volume": 6, "ict": 5, "spread": 3, "news": 2}),
    R.EXPANSION: (("Breakout", "Momentum", "Trend Following"),
                  {"smc": 28, "trend": 20, "liquidity": 16, "ict": 10,
                   "macro": 8, "volume": 8, "rr": 5, "spread": 3, "news": 2}),
    R.BREAKOUT_TRUE: (("Breakout", "SMC", "Momentum"),
                      {"smc": 30, "trend": 20, "liquidity": 16, "ict": 10,
                       "macro": 8, "volume": 7, "rr": 5, "spread": 2, "news": 2}),
    R.BREAKOUT_FALSE: (("Liquidity Sweep Fade", "Mean Reversion"),
                       {"liquidity": 34, "smc": 24, "rr": 12, "macro": 8,
                        "trend": 6, "ict": 6, "volume": 5, "spread": 3, "news": 2}),
    R.LIQUIDITY_HUNT: (("ICT Liquidity Sweep", "SMC", "Wyckoff Spring"),
                       {"liquidity": 34, "smc": 26, "ict": 14, "rr": 8,
                        "macro": 6, "trend": 5, "volume": 4, "spread": 2, "news": 1}),
    R.TREND_EXHAUSTION: (("Mean Reversion", "Trend Exhaustion", "SMC"),
                         {"liquidity": 28, "smc": 24, "rr": 14, "macro": 10,
                          "trend": 8, "ict": 6, "volume": 5, "spread": 3, "news": 2}),
    R.NEWS_DRIVEN: (("News", "Volatility", "Liquidity Sweep Fade"),
                    {"news": 24, "liquidity": 24, "smc": 20, "macro": 14,
                     "rr": 8, "spread": 5, "trend": 3, "volume": 1, "ict": 1}),
}

_DEFAULT = (("SMC", "Liquidity"),
            {"smc": 30, "liquidity": 20, "trend": 15, "ict": 10, "macro": 8,
             "volume": 5, "indicator": 5, "rr": 5, "spread": 2, "news": 5})


def select(reg: R.Regime, learned: dict | None = None) -> tuple:
    """(strategies, weights, why) for the regime the market is actually in.

    `learned` is the LEVEL 7 feedback: a per-category multiplier built from
    closed trades. It nudges the regime's weights, it does not replace
    them, so a run of bad luck cannot rewrite the playbook.
    """
    strategies, weights = PLAYBOOK.get(reg.name, _DEFAULT)
    weights = dict(weights)
    weights.setdefault("indicator", 3)      # confirmation only, always small

    if learned:
        for key, mult in learned.items():
            if key in weights:
                weights[key] = max(1.0, weights[key] * max(0.5, min(1.5, mult)))

    # A low-confidence regime should not swing the weights hard, so blend
    # back toward the neutral profile in proportion to how unsure it is.
    blend = max(0.0, min(1.0, reg.confidence / 100.0))
    base = _DEFAULT[1]
    weights = {k: round(weights.get(k, 0) * blend + base.get(k, 0) * (1 - blend), 1)
               for k in set(weights) | set(base)}

    why = (f"ตลาดอยู่ในสภาพ <b>{reg.name}</b> (มั่นใจ {reg.confidence:.0f}%) "
           f"จึงเลือกใช้ {', '.join(strategies)}")
    if reg.confidence < 40:
        why += " — ความมั่นใจต่ำ ระบบจึงถ่วงกลับไปหาน้ำหนักกลาง"
    return strategies, weights, why
