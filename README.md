# CapitalGuard v3 — ระบบเทรด SMC-First แบบ Capital Preservation

ระบบเทรด MT5 สำหรับโบรกเกอร์ XM (XAUUSD เป็นหลัก) ที่ใช้ **Smart Money Concepts (SMC) เป็นระบบตัดสินใจหลัก**
อินดิเคเตอร์**ห้าม**เป็นเหตุผลในการเปิดออเดอร์ — มีหน้าที่ยืนยันความน่าจะเป็นเท่านั้น (weight 5%)
"การไม่เทรด" คือการตัดสินใจที่ถูกต้อง — วันที่ไม่มีออเดอร์เลยคือวันปกติ

**กฎเหล็ก:** ไม่มี Martingale / ไม่มี Grid / ไม่เฉลี่ยขาดทุน / ไม่ย้าย SL ออกห่าง / ทุกออเดอร์ต้องมี SL และ TP / ถึง Daily Loss หยุดเทรดทันที

📖 **คู่มือติดตั้ง-ใช้งานฉบับละเอียด (XM + Backtest + Optimization): [docs/MANUAL_TH.md](docs/MANUAL_TH.md)**

## โครงสร้างโปรเจกต์ (Modular OOP)

```
MQL5/
├── Experts/
│   ├── CapitalGuardEA.mq5        ← EA หลัก v2 (ระบบเต็ม)
│   └── TradeTemplate_TP_SL.mq5   ← template อย่างง่าย (เวอร์ชันแรก)
└── Include/CapitalGuard/
    ├── RiskManager.mqh           ← บริหารความเสี่ยง + position sizing
    ├── IndicatorSet.mqh          ← EMA/VWAP/RSI/MACD/ATR/ADX/BB/OBV/CMF/Ichimoku/SuperTrend/Pivot
    ├── MarketStructure.mqh       ← Swing / BOS / CHoCH
    ├── SmartMoney.mqh            ← Liquidity Sweep / Order Block / FVG
    ├── Regime.mqh                ← แยกสภาพตลาด Trend/Range/Volatility
    ├── NewsFilter.mqh            ← กรองข่าวแรง (Economic Calendar)
    ├── ScoringEngine.mqh         ← คะแนน 10 หมวดถ่วงน้ำหนัก 0-100
    ├── TradeManager.mqh          ← BE / Partial / ATR Trailing / Time Exit / Emergency Exit
    ├── Logger.mqh                ← บันทึกทุกออเดอร์ (CSV + JSONL)
    └── Dashboard.mqh             ← แดชบอร์ดบนกราฟ
python/
├── feature_engineering.py        ← แปลง log → feature matrix
├── train_model.py                ← เทรนโมเดล ML (veto filter)
├── walk_forward.py               ← Walk-Forward Analysis
├── monte_carlo.py                ← Monte Carlo simulation (risk of ruin)
└── requirements.txt
docs/
└── MANUAL_TH.md                  ← คู่มือฉบับละเอียด
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

## ลำดับการวิเคราะห์ SMC (Sequential Pipeline — ข้อใดไม่ผ่าน ห้ามเข้า)

1. **Market Structure** — HH/HL/LH/LL บน D1, H4, H1, entry TF → ทิศทางมาจากโครงสร้างเท่านั้น (H4+H1 ต้องตรงกัน, D1 ห้ามสวน) ห้ามสวนเทรนด์ TF หลัก เว้นแต่เปิด `InpAllowCounterTrend` และมี CHoCH ชัดเจนบน H1
2. **Liquidity** — แผนที่สภาพคล่อง: Equal Highs/Lows, BSL/SSL pools
3. **BOS** — Break of Structure ในทิศทางเทรด
4. **CHoCH / MSS** — Change of Character (นับรวมกับข้อ 3: ต้องมีอย่างใดอย่างหนึ่ง)
5. **Order Block** — โซนที่ผ่านการให้คะแนนคุณภาพ ≥ 60 (ความสด, จำนวนครั้งที่ถูกแตะ, volume, ตำแหน่งในโครงสร้าง, ความสัมพันธ์กับ sweep)
6. **Fair Value Gap** — มี FVG ในทิศทางเทรดที่ยังไม่ถูก invalidate
7. **Liquidity Sweep** — ต้องเกิดการกวาดสภาพคล่องก่อนเสมอ จึงพิจารณาเข้า
8. **Premium/Discount** — Buy เฉพาะ Discount (≤0.5 ของ dealing range), Sell เฉพาะ Premium
9. **Mitigation** — ราคาต้องกลับมา mitigate OB หรือ FVG จริง ๆ (ไม่ไล่ราคา)
10. **Entry Confirmation** — TF ล่าง (M30/M15) ห้ามสวนทิศ + คะแนนรวม ≥ 90
11. **Risk Management** — RR ≥ 1:2, position sizing, margin

## Confidence Score (SMC เป็นหลัก, เกณฑ์ ≥ 90)

| หมวด | น้ำหนัก | สิ่งที่วัด |
|---|---|---|
| Market Structure | 25% | ทุก TF (D1/H4/H1/entry) สนับสนุนทิศทาง |
| Liquidity | 20% | Sweep เกิดแล้ว, pool เป้าหมายในทิศกำไร, ฝั่งถูกของ range |
| BOS / CHoCH | 20% | Structure break ในทิศทางเทรด (สัญญาณหลัก) |
| Order Block | 15% | คะแนนคุณภาพของโซน × สถานะ mitigation |
| FVG | 10% | มี imbalance + ราคากลับมา mitigate |
| Volume | 5% | Volume ratio, OBV, CMF |
| Indicator Confirmation | 5% | EMA/VWAP/RSI/MACD — ยืนยันได้เท่านั้น ตัดสินใจไม่ได้ |

ทุกคะแนนย่อย, step ที่ fail และเหตุผล ถูกบันทึกลง log — audit ย้อนหลังได้ทุกการตัดสินใจ

## Multi-Timeframe Analysis

วิเคราะห์ **Daily, H4, H1, M30, M15** พร้อมกัน เข้าออเดอร์ที่ **M5** (หรือ M1 ผ่าน `InpEntryTF`)
โครงสร้าง D1/H4/H1 กำหนดทิศทาง — **H4 กับ H1 ต้องตรงกัน และ D1 ห้ามสวน** ส่วน M30/M15 ห้ามขัดทิศทางตอนยืนยันเข้า

## Smart Money Concepts (ระบบหลัก)

- **Liquidity Map** — Equal Highs/Lows = pool ของ stop (BSL/SSL), ตรวจว่ามี pool เป้าหมายในทิศกำไร
- **Liquidity Sweep** — ไส้เทียนกวาด stop ใต้/เหนือ swing แล้วปิดกลับ — ต้องเกิดก่อนเข้าเสมอ
- **Order Block คุณภาพสูง** — ให้คะแนน 5 ด้าน (ด้านละ 20): ความสดของโซน / จำนวนครั้งที่ถูกแตะ / volume ของแท่ง OB / ตำแหน่ง premium-discount / เกิดหลัง sweep
- **Fair Value Gap + Mitigation** — imbalance 3 แท่งที่ยังไม่ถูกปิด และรอราคากลับเข้าโซนก่อนเข้า
- **Premium/Discount** — dealing range จาก swing ล่าสุด, จุดสมดุล 0.5 — buy ต่ำกว่า, sell สูงกว่า
- **Emergency Exit** — เกิด CHoCH สวนทางไม้ที่ถือ → ปิดทันทีไม่รอ SL

## SL / TP

- **SL** = ใต้/เหนือ Swing Low/High ล่าสุด + buffer 0.3×ATR (กัน liquidity sweep) — ถ้า swing ใช้ไม่ได้ fallback เป็น 1.5×ATR โดย clamp ระหว่าง 0.8–2.5×ATR และเช็ค stops level ของโบรกเกอร์เสมอ
- **TP** = Dynamic RR: ขั้นต่ำ **1:2** | Trend แข็งแรง (ADX≥30) → 1:2.5 ปล่อยกำไรวิ่ง
- **ห้ามส่งออเดอร์โดยไม่มี SL/TP โดยเด็ดขาด** — โค้ดไม่มีเส้นทางที่เปิดออเดอร์เปล่าได้ และ SL ขยับได้ทางเดียวคือเข้าหากำไร

## Daily Target

เป้าหมายกำไรต่อวัน 1–3% (ค่าเริ่มต้น 2% ≈ $0.60 ที่ทุน $30) เมื่อถึงเป้าเลือกได้:
- `AFTER_TARGET_STOP` (ค่าเริ่มต้น) — ปิดการเทรดทั้งวัน
- `AFTER_TARGET_QUALITY` — เทรดต่อเฉพาะ setup คะแนน ≥ 95

## Trade Management

- กำไรถึง **1R** → เลื่อน SL เป็น Breakeven (+offset) และ Partial Close 50%
- หลัง BE → **ATR Trailing Stop** (1.2×ATR)
- **Time Exit** — ถือเกิน 48 ชม. และกำไรต่ำกว่า 0.3R → ปิดทิ้ง
- **Emergency Exit** — เกิด CHoCH สวนทางไม้ที่ถือ → ปิดทันทีไม่รอ SL


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
4. เปิดกราฟ **XAUUSD (หรือ GOLD ของ XM) M5** → ลาก EA ลงกราฟ → เปิด Algo Trading
5. เปิดสิทธิ์ปฏิทินข่าว: Tools → Options → ติ๊ก Allow News

## Backtest (ต้องผ่านก่อนใช้เงินจริง)

ใน Strategy Tester:
1. เลือก `CapitalGuardEA` / XAUUSD / M5 / **Every tick based on real ticks**
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
# 4) Monte Carlo: โอกาสชน drawdown breaker 15% กี่เปอร์เซ็นต์
python monte_carlo.py --features features.csv --balance 30 --sims 5000
```

โมเดลทำหน้าที่เป็น **veto filter** (ตัดไม้ที่โอกาสชนะต่ำ) เท่านั้น — ไม่มีวันแทนที่ risk management

## ลำดับการนำไปใช้จริง (อย่าข้ามขั้น)

1. **Backtest 5 ปี** ผ่านเกณฑ์ metrics ด้านบน
2. **Walk-Forward** ผลสม่ำเสมอทุก window
3. **Forward Test บนเดโม่** อย่างน้อย 1–3 เดือน เทียบสถิติกับ backtest
4. เงินจริงเริ่มเล็กที่สุด และหยุดทันทีถ้าสถิติจริงเบี่ยงจาก backtest อย่างมีนัย

> ระบบนี้ออกแบบเพื่อ "อยู่รอดระยะยาว" — กำไรน้อยแต่สม่ำเสมอ ดีกว่ากำไรมากแล้วล้างพอร์ต
