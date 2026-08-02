# CapitalGuard — ระบบเทรดอัตโนมัติแบบ Capital Preservation First

ระบบเทรด MT5 (XAUUSD เป็นหลัก) ที่ให้ความสำคัญกับ **การรักษาทุนเป็นอันดับหนึ่ง**
ขาดทุนให้น้อยที่สุด → กำไรสม่ำเสมอ → ไม่ Overtrade → ทุกออเดอร์มีเหตุผลบันทึกไว้เสมอ

**กฎเหล็ก:** ไม่มี Martingale / ไม่มี Grid / ไม่เฉลี่ยขาดทุน / ทุกออเดอร์ต้องมี SL และ TP / ถึง Daily Loss หยุดเทรดทันที

## โครงสร้างโปรเจกต์ (Modular)

```
MQL5/
├── Experts/
│   ├── CapitalGuardEA.mq5        ← EA หลัก (ระบบเต็ม)
│   └── TradeTemplate_TP_SL.mq5   ← template อย่างง่าย (เวอร์ชันแรก)
└── Include/CapitalGuard/
    ├── RiskManager.mqh           ← บริหารความเสี่ยง + position sizing
    ├── IndicatorSet.mqh          ← ชุดอินดิเคเตอร์ต่อ timeframe
    ├── MarketStructure.mqh       ← Swing / BOS / CHoCH
    ├── Regime.mqh                ← แยกสภาพตลาด Trend/Range/Volatility
    ├── NewsFilter.mqh            ← กรองข่าวแรง (Economic Calendar)
    ├── ScoringEngine.mqh         ← คะแนนความมั่นใจแบบถ่วงน้ำหนัก 0-100
    ├── TradeManager.mqh          ← Breakeven / Partial / ATR Trailing / Time Exit
    ├── Logger.mqh                ← บันทึกทุกออเดอร์ (CSV + JSONL)
    └── Dashboard.mqh             ← แดชบอร์ดบนกราฟ
python/
├── feature_engineering.py        ← แปลง log → feature matrix
├── train_model.py                ← เทรนโมเดล ML (veto filter)
├── walk_forward.py               ← Walk-Forward Analysis
└── requirements.txt
```

## Risk Management (หัวใจของระบบ)

| กลไก | ค่าเริ่มต้น | พฤติกรรม |
|---|---|---|
| Risk per Trade | 0.75% (ตั้งได้ 0.5–1%) | คำนวณ lot จากระยะ SL แบบ dynamic |
| Max Daily Loss | 3% | ถึงลิมิต → **หยุดเทรดทั้งวันทันที** |
| Max Weekly Loss | 8% | ถึงลิมิต → หยุดทั้งสัปดาห์ |
| Max Drawdown | 15% | Circuit breaker หยุดทุกอย่าง |
| Drawdown ต่อเนื่อง | อัตโนมัติ | DD ถึง 1/3 ของลิมิต → ลด risk เหลือ 75%, ถึง 2/3 → เหลือ 50% |
| ออเดอร์ต่อวัน | 3 ไม้ | กัน overtrade |
| Position ซ้อน | 1 ตำแหน่ง | ไม่เปิดไม้ซ้อนโดยไม่มีเหตุผล |

### ⚠️ หมายเหตุสำคัญสำหรับทุน $30

XAUUSD ที่ lot ขั้นต่ำ 0.01 การขาดทุน 1 ครั้ง (SL ระยะ ~$3 ของราคาทอง) ≈ $3 = **10% ของทุน** ซึ่งเกิน risk 1% มาก
ระบบจึงมี `InpMinLotPolicy` ให้เลือก:
- `MINLOT_SKIP` — เข้มงวดสุด: ถ้า lot ขั้นต่ำเสี่ยงเกินเป้า ไม่เทรดเลย (แนะนำถ้ารับความเสี่ยงไม่ได้)
- `MINLOT_USE_IF_CAPPED` (ค่าเริ่มต้น) — ใช้ lot ขั้นต่ำได้ก็ต่อเมื่อความเสี่ยงจริงไม่เกิน Hard Cap 3%

คำแนะนำจริงใจ: ทุน $30 เหมาะกับการ**พิสูจน์ระบบ** มากกว่าสร้างรายได้ — ใช้บัญชี Cent หรือเดโม่จนกว่าสถิติจะพิสูจน์ตัวเองผ่าน Backtest + Forward Test แล้วค่อยเพิ่มทุน

## AI Decision Engine (คะแนน 0–100)

เปิดออเดอร์เฉพาะเมื่อคะแนนรวม **> 80** โดยแต่ละหมวดมีน้ำหนัก:

| หมวด | น้ำหนัก | สิ่งที่วัด |
|---|---|---|
| Trend | 25% | EMA 20/50/200 stack บน H4/H1/M15 + ฝั่งของ VWAP |
| Momentum | 20% | RSI zone + ทิศทาง, MACD (entry TF + H1), +DI/−DI |
| Volume | 20% | ปริมาณเทียบค่าเฉลี่ย 20 แท่ง + ความชัน OBV |
| Market Structure | 20% | Bias จาก Swing, BOS, CHoCH ล่าสุด |
| Volatility | 15% | ATR regime + ตำแหน่งใน Bollinger Bands |

ทุกคะแนนย่อยถูกบันทึกลง log พร้อมเหตุผล — ตรวจสอบย้อนหลังได้ทุกไม้

## Multi-Timeframe Analysis

วิเคราะห์ H4, H1, M30, M15, M5 พร้อมกัน — **กฎเด็ดขาด: ถ้า H4 กับ H1 สวนทางกัน ไม่เข้าเทรด** (direction = 0)

## SL / TP

- **SL** = ใต้/เหนือ Swing Low/High ล่าสุด + buffer 0.3×ATR (กัน liquidity sweep) — ถ้า swing ใช้ไม่ได้ fallback เป็น 1.5×ATR โดย clamp ระหว่าง 0.8–2.5×ATR และเช็ค stops level ของโบรกเกอร์เสมอ
- **TP** = Dynamic RR ตามสภาพตลาด: Range → 1:1.5 (ขั้นต่ำ) | Trend → 1:2 (เป้าหมาย) | Trend แข็งแรง (ADX≥30) → 1:2.5
- **ห้ามส่งออเดอร์โดยไม่มี SL/TP โดยเด็ดขาด** — โค้ดไม่มีเส้นทางที่เปิดออเดอร์เปล่าได้

## Daily Target

เป้าหมายกำไรต่อวัน 1–3% (ค่าเริ่มต้น 2% ≈ $0.60 ที่ทุน $30) เมื่อถึงเป้าเลือกได้:
- `AFTER_TARGET_STOP` — ปิดการเทรดทั้งวัน
- `AFTER_TARGET_QUALITY` (ค่าเริ่มต้น) — เทรดต่อเฉพาะ setup คะแนน ≥ 90

## Trade Management

- กำไรถึง **1R** → เลื่อน SL เป็น Breakeven (+offset) และ Partial Close 50%
- หลัง BE → **ATR Trailing Stop** (1.2×ATR)
- **Time Exit** — ถือเกิน 48 ชม. และกำไรต่ำกว่า 0.3R → ปิดทิ้ง

## News Filter

ใช้ Economic Calendar ในตัว MT5 กรองข่าว impact สูงของสกุลที่กำหนด (USD สำหรับทองคำ): FOMC, CPI, PPI, NFP, Interest Rate ฯลฯ
- งดเทรดก่อนข่าว 45 นาที / หลังข่าว 45 นาที (ปรับได้ 30–60)
- ข่าวที่ปฏิทินไม่มี (สงคราม, การเมือง, สุนทรพจน์กะทันหัน) ใส่เวลาเองได้ที่ `InpNewsManualTimes` เช่น `2026.08.05 21:00;2026.08.07 19:30`
- ⚠️ Strategy Tester ไม่มีปฏิทินข่าว — ตอน backtest ระบบจะใช้เฉพาะ manual list

## Market Regime Detection

แยกตลาดด้วย ADX + ATR ratio: **Trend Up / Trend Down / Range** × **High / Normal / Low Volatility**
ผลลัพธ์ปรับพฤติกรรม: RR target, คะแนน volatility, และแสดงบน dashboard

## Sessions

ค่าเริ่มต้นเทรดเฉพาะ **London + New York** (ช่วงที่ XAUUSD มีสภาพคล่องดีที่สุด) — Asian ปิดไว้ เปิดได้ผ่าน input
ชั่วโมงเป็นเวลา server ของโบรกเกอร์ ปรับให้ตรงกับ GMT offset ของโบรกเกอร์คุณ

## Logging

ทุกออเดอร์บันทึกลง `MQL5/Files/CapitalGuard/`:
- `trades_<magic>.csv` — เปิดใน Excel ได้ทันที
- `trades_<magic>.jsonl` — ป้อนเข้า Python pipeline

ข้อมูลที่เก็บ: เวลา, ราคาเข้า, SL, TP, lot, เหตุผลการเข้า, คะแนนย่อยทุกหมวด, regime, session, การคำนวณ lot, ผลลัพธ์, กำไร, realized RR — รวมถึง setup ที่**ถูกปฏิเสธ**และเหตุผล (audit ได้ว่า filter ทำงานถูก)

## Dashboard บนกราฟ

Balance / Equity / Daily–Weekly–Monthly P/L / Drawdown / Risk scale ปัจจุบัน / Win rate / จำนวนไม้วันนี้ / Regime / Session / สถานะข่าว / คะแนนสัญญาณล่าสุด / สถานะระบบ

## วิธีติดตั้ง

1. MT5 → `File → Open Data Folder`
2. คัดลอกทั้งโฟลเดอร์ `MQL5/` ของ repo นี้ทับลงไป (Experts + Include)
3. MetaEditor (F4) → เปิด `CapitalGuardEA.mq5` → Compile (F7)
4. เปิดกราฟ **XAUUSD M15** → ลาก EA ลงกราฟ → เปิด Algo Trading
5. เปิดสิทธิ์ปฏิทินข่าว: Tools → Options → ติ๊ก Allow News

## Backtest (ต้องผ่านก่อนใช้เงินจริง)

ใน Strategy Tester:
1. เลือก `CapitalGuardEA` / XAUUSD / M15 / **Every tick based on real ticks**
2. ช่วงข้อมูล **อย่างน้อย 5 ปี** (ครอบคลุม Bull 2020, Bear/Sideway 2021-22, High Vol 2024-25)
3. ตรวจ metrics ขั้นต่ำที่ควรผ่าน:

| Metric | เกณฑ์ที่ควรได้ |
|---|---|
| Profit Factor | > 1.3 |
| Max Drawdown | < 15% |
| Expectancy | > 0 ต่อไม้ (หลังหักสเปรด/ค่าคอม) |
| Sharpe Ratio | > 0.8 |
| Recovery Factor | > 2 |
| Win Rate × Avg RR | สอดคล้องกัน (RR 1:2 → winrate 40%+ ก็กำไร) |

## Optimization + Walk-Forward (ป้องกัน Overfitting)

พารามิเตอร์ที่ควร optimize: `InpEmaFast/Mid`, `InpAtrMultSL`, `InpRsiPeriod`, `InpAdxTrendMin`, `InpRiskPerTrade`, `InpScoreThreshold`, `InpBaseRR`, `InpAtrTrailMult`, `InpBETriggerR`, sessions

ขั้นตอนที่ถูกต้อง:
1. Strategy Tester → Settings → **Forward: 1/3** (ระบบจะแบ่ง in-sample / out-of-sample ให้)
2. Optimize ด้วย Genetic Algorithm บน criterion **Complex Criterion max**
3. **เลือกชุดพารามิเตอร์ที่ผลใน Forward period ใกล้เคียง Back period** — ไม่ใช่ชุดที่กำไร back สูงสุด
4. ชุดที่ดีเฉพาะ in-sample = overfit → ทิ้ง

## Machine Learning Pipeline (พร้อมต่อยอด)

```bash
cd python && pip install -r requirements.txt
# 1) แปลง log จริง/backtest เป็น features
python feature_engineering.py --log trades_20260803.jsonl --out features.csv
# 2) เทรนโมเดลทำนายโอกาสชนะ (chronological split — ไม่ leak อนาคต)
python train_model.py --features features.csv --model model.pkl
# 3) Walk-forward: โมเดลเสถียรข้ามช่วงเวลาหรือไม่
python walk_forward.py --features features.csv --train-window 200 --test-window 50
```

โมเดลทำหน้าที่เป็น **veto filter** (ตัดไม้ที่โอกาสชนะต่ำ) เท่านั้น — ไม่มีวันแทนที่ risk management

## ลำดับการนำไปใช้จริง (อย่าข้ามขั้น)

1. **Backtest 5 ปี** ผ่านเกณฑ์ metrics ด้านบน
2. **Walk-Forward** ผลสม่ำเสมอทุก window
3. **Forward Test บนเดโม่** อย่างน้อย 1–3 เดือน เทียบสถิติกับ backtest
4. เงินจริงเริ่มเล็กที่สุด และหยุดทันทีถ้าสถิติจริงเบี่ยงจาก backtest อย่างมีนัย

> ระบบนี้ออกแบบเพื่อ "อยู่รอดระยะยาว" — กำไรน้อยแต่สม่ำเสมอ ดีกว่ากำไรมากแล้วล้างพอร์ต
