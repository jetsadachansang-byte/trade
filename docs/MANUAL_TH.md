# คู่มือการติดตั้งและใช้งาน CapitalGuard EA v3 — SMC-First (ฉบับละเอียด)

สำหรับโบรกเกอร์ XM / MetaTrader 5 / XAUUSD — ทุนเริ่มต้น $30

---

## 1. เตรียมบัญชี XM

1. เปิดบัญชี XM แนะนำประเภท **Micro Account** สำหรับทุน $30
   - บัญชี Micro: 1 lot = 1 oz ของทอง → ความเสี่ยงต่อจุดต่ำกว่าบัญชี Standard 100 เท่า ทำให้ risk 0.5–1% ต่อไม้ **ทำได้จริง**
   - บัญชี Standard: 0.01 lot ขั้นต่ำก็ยังเสี่ยง ~10% ต่อไม้ที่ทุน $30 — ไม่แนะนำ
2. Leverage: ตั้งได้ตามต้องการ (เช่น 1:500) — **leverage สูงไม่ได้เพิ่มความเสี่ยงของ EA นี้** เพราะขนาด lot ถูกคุมด้วย risk % เสมอ leverage มีผลแค่ margin ที่ถูกใช้
3. ดาวน์โหลด MT5 จาก XM แล้วล็อกอินบัญชี
4. ตรวจสอบชื่อ symbol ทองของ XM: โดยทั่วไปคือ `GOLD` หรือ `GOLD#` หรือ `XAUUSD` (แล้วแต่ประเภทบัญชี) — เปิดกราฟ symbol นั้น

## 2. ติดตั้ง EA

1. MT5 → เมนู **File → Open Data Folder**
2. คัดลอกไฟล์จาก repo นี้:
   - `MQL5/Experts/CapitalGuardEA.mq5` → ไปที่ `MQL5/Experts/`
   - โฟลเดอร์ `MQL5/Include/CapitalGuard/` ทั้งโฟลเดอร์ → ไปที่ `MQL5/Include/CapitalGuard/`
3. เปิด MetaEditor (กด F4 ใน MT5) → เปิด `CapitalGuardEA.mq5` → กด **Compile (F7)** — ต้องขึ้น `0 errors, 0 warnings`
4. กลับมา MT5 → เปิดกราฟทอง **M5** → ลาก `CapitalGuardEA` จาก Navigator ลงกราฟ
5. แท็บ Common: ติ๊ก **Allow Algo Trading** → OK
6. เปิดปุ่ม **Algo Trading** บนแถบเครื่องมือ (ต้องเป็นสีเขียว)
7. เปิดสิทธิ์ปฏิทินข่าว: **Tools → Options → Server → ติ๊ก Enable news**

Dashboard จะปรากฏมุมซ้ายบนของกราฟทันทีที่ EA ทำงาน

## 3. การตั้งค่าที่สำคัญ (Input Parameters)

### ความเสี่ยง (อย่าแก้ถ้าไม่จำเป็น)
| Input | ค่าเริ่มต้น | คำอธิบาย |
|---|---|---|
| `InpRiskPerTrade` | 0.75 | % ความเสี่ยงต่อไม้ (0.5–1 ตามสเปค) |
| `InpMaxDailyLoss` | 3.0 | ขาดทุนถึง 3% ของวัน → หยุดทั้งวัน |
| `InpMaxWeeklyLoss` | 8.0 | ขาดทุนถึง 8% ของสัปดาห์ → หยุดทั้งสัปดาห์ |
| `InpMaxDrawdown` | 15.0 | DD จากจุดสูงสุด → หยุดทุกอย่าง (circuit breaker) |
| `InpMaxTradesPerDay` | 2 | จำกัดจำนวนไม้ต่อวัน |
| `InpHardRiskCap` | 3.0 | เพดานความเสี่ยงจริงเมื่อจำเป็นต้องใช้ lot ขั้นต่ำ |

### เกณฑ์การเข้า (SMC Pipeline)
| Input | ค่าเริ่มต้น | คำอธิบาย |
|---|---|---|
| `InpScoreThreshold` | 90 | คะแนนขั้นต่ำ — ต่ำกว่านี้ไม่เข้าเด็ดขาด |
| `InpReqBosChoch` | true | ต้องมี BOS/CHoCH ในทิศทางเทรด |
| `InpReqOrderBlock` / `InpMinOBQuality` | true / 60 | ต้องมี OB คุณภาพ ≥ 60 |
| `InpReqFVG` | true | ต้องมี Fair Value Gap |
| `InpReqSweep` | true | ต้องเกิด Liquidity Sweep ก่อน |
| `InpReqPremiumDiscount` / `InpDiscountMax` | true / 0.5 | Buy เฉพาะ discount, Sell เฉพาะ premium |
| `InpReqMitigation` | true | ราคาต้องกลับมา mitigate OB/FVG ก่อนเข้า |
| `InpReqTrendConfirm` | true | M30/M15 ห้ามสวนทิศทาง |
| `InpAllowCounterTrend` | false | อนุญาตสวนเทรนด์เมื่อมี CHoCH ชัดเจนบน H1 |
| `InpReqLiquidityTarget` | false | บังคับให้มี BSL/SSL pool ในทิศกำไร (เข้มพิเศษ) |
| `InpMinRR` | 2.0 | Risk:Reward ขั้นต่ำ 1:2 |

> ⚠️ **ค่าเริ่มต้นเข้มงวดมากตามสเปค** — pipeline 11 ขั้นต้องผ่านครบ (Sweep+BOS/CHoCH+OB+FVG+Mitigation พร้อมกัน) บางสัปดาห์อาจไม่มีออเดอร์เลย ซึ่ง**ถือว่าปกติและถูกต้อง** — dashboard บรรทัด `Status:` จะบอกว่ารออยู่ที่ step ไหน หาก backtest แล้วจำนวนเทรดน้อยจนวัดสถิติไม่ได้ ให้ผ่อนทีละข้อ (แนะนำเริ่มจาก `InpReqMitigation=false` → `InpReqFVG=false`) แล้ว backtest เทียบกันทุกครั้ง **อย่าลดหลายข้อพร้อมกัน**

### เวลาเทรด (สำคัญ — ต้องปรับตามโบรกเกอร์)
เวลาใน EA เป็น**เวลา server ของ XM** (GMT+2 หรือ GMT+3 ช่วง DST):
- London session ≈ 10:00–18:00 server time (ค่าเริ่มต้นตั้งไว้แล้ว)
- New York ≈ 15:00–23:00 server time
- ช่วง Overlap (15:00–18:00) ได้คะแนน session สูงสุด

ตรวจสอบ: ดูเวลาที่มุม Market Watch เทียบกับเวลาไทย (ไทย = GMT+7; ถ้า server GMT+3 → เวลาไทยเร็วกว่า server 4 ชม.)

### News Filter
- `InpNewsPreMin/PostMin` = 45 นาที ก่อน/หลังข่าว impact สูง (ปรับได้ 30–60)
- ข่าวที่ไม่อยู่ในปฏิทิน (สงคราม, เหตุการณ์ภูมิรัฐศาสตร์, สุนทรพจน์กะทันหัน) → ใส่เวลาที่ `InpNewsManualTimes` รูปแบบ `2026.08.05 21:00;2026.08.07 19:30` (เวลา server)
- DXY filter (`InpUseDxyFilter`): XM ไม่มี DXY โดยตรงในบางประเภทบัญชี — ถ้ามี symbol ดัชนีดอลลาร์ (เช่น `USDX`) ใส่ชื่อที่ `InpDxySymbol` แล้วเปิดใช้ ระบบจะไม่เข้าไม้ทองที่วิ่งทิศเดียวกับแนวโน้ม DXY

## 4. Backtest (บังคับก่อนใช้เงินจริง)

1. MT5 → View → **Strategy Tester** (Ctrl+R)
2. ตั้งค่า:
   - Expert: `CapitalGuardEA`
   - Symbol: ทองของ XM / Timeframe: **M5**
   - Modelling: **Every tick based on real ticks** (แม่นสุด) หรือ 1 minute OHLC (เร็วกว่า ใช้คัดกรองรอบแรก)
   - ช่วงเวลา: **อย่างน้อย 5 ปี** (2021–2026 ครอบคลุม bull/bear/sideway/สงคราม/เงินเฟ้อ)
   - Deposit: 30 USD, Leverage ตามบัญชีจริง
3. ⚠️ Strategy Tester **ไม่มีปฏิทินข่าว** — ผล backtest จะไม่รวม news filter (ยกเว้น manual list) ผลจริงควรดีกว่า backtest เล็กน้อยเพราะ live มีตัวกรองข่าวเพิ่ม
4. เกณฑ์ผ่านขั้นต่ำ:

| Metric | เกณฑ์ |
|---|---|
| Profit Factor | > 1.3 |
| Max Drawdown | < 15% |
| Sharpe Ratio | > 0.8 |
| Recovery Factor | > 2 |
| Expectancy | > 0 หลังหัก spread/commission |
| จำนวนเทรด | > 100 ไม้ (น้อยกว่านี้สถิติไม่มีนัย) |

## 5. Optimization อย่างปลอดภัย (กัน Overfitting)

1. Strategy Tester → Settings → **Optimization: Genetic** → **Forward: 1/3**
2. พารามิเตอร์ที่ควร optimize (ทีละกลุ่ม อย่าทำพร้อมกันทั้งหมด):
   - กลุ่ม SMC: `InpSwingBars` (2–5), `InpSmcWindow` (20–50), `InpMinOBQuality` (50–80), `InpDiscountMax` (0.4–0.6)
   - กลุ่ม SL/TP: `InpAtrMultSL` (1.0–2.5), `InpBaseRR` (1.5–3.0), `InpAtrTrailMult` (0.8–2.0)
   - กลุ่มเกณฑ์เข้า: `InpScoreThreshold` (80–95)
   - กลุ่มเวลา: ช่วง session
3. **กติกาเลือกผล:** เลือกชุดที่กำไร/DD ในช่วง **Forward ใกล้เคียงช่วง Back** — ไม่ใช่ชุดที่ back สูงสุด ชุดที่ดีเฉพาะ back คือ overfit ให้ทิ้ง
4. รัน Monte Carlo ซ้ำด้วย Python (ข้อ 7) เพื่อดูความเสี่ยงลำดับเทรดสลับกัน

## 6. Forward Test

1. รัน EA บน **บัญชีเดโม่ XM** (สเปรดใกล้จริง) อย่างน้อย **1–3 เดือน**
2. ทุกสัปดาห์เทียบสถิติจริงกับ backtest: win rate, avg RR, DD — ถ้าเบี่ยงมาก (เช่น winrate ต่ำกว่า backtest เกิน 15 จุด) ให้หยุดและหาสาเหตุ
3. ตรวจไฟล์ log `MQL5/Files/CapitalGuard/trades_<magic>.csv` — ทุกไม้ต้องมีเหตุผล และไม้ที่ถูก skip ต้องมีเหตุผลถูกต้อง
4. ผ่านแล้วจึงลงเงินจริง โดย**เริ่มที่ risk ต่ำสุด 0.5%** ก่อนเสมอ

## 7. Python Pipeline (ML / Monte Carlo)

```bash
cd python && pip install -r requirements.txt

# แปลง log เป็น feature matrix
python feature_engineering.py --log trades_20260803.jsonl --out features.csv

# เทรนโมเดล veto (ต้องมี 100+ เทรดขึ้นไป)
python train_model.py --features features.csv --model model.pkl

# Walk-forward ของโมเดล
python walk_forward.py --features features.csv --train-window 200 --test-window 50

# Monte Carlo: ความเสี่ยงพัง 15% DD มีโอกาสกี่ %
python monte_carlo.py --features features.csv --balance 30 --sims 5000
```

ไฟล์ log อยู่ที่ `MT5 Data Folder/MQL5/Files/CapitalGuard/` (backtest จะอยู่ใต้ `Tester/<agent>/MQL5/Files/`)

## 8. FAQ / ปัญหาที่พบบ่อย

**EA ไม่เปิดออเดอร์เลยหลายวัน** — ปกติ ตามดีไซน์ ดูบรรทัด `Status:` บน dashboard จะบอกเหตุผลปัจจุบัน (คะแนนไม่ถึง / checklist ข้อไหนไม่ผ่าน / นอก session / ติดข่าว)

**Order failed: Invalid stops** — โบรกเกอร์มี stops level กว้างกว่าปกติ ระบบกันไว้แล้วแต่ถ้าเจอ ให้เพิ่ม `InpMinSLAtrMult`

**Order failed: No money** — margin ไม่พอ ตรวจ leverage และประเภทบัญชี (ควรเป็น Micro)

**อยากให้เทรดถี่ขึ้น** — ลด `InpScoreThreshold` เป็น 85 หรือปิด checklist บางข้อ **แต่ต้อง backtest ใหม่ทุกครั้งก่อนใช้จริง** อย่าแก้ค่าบนบัญชีจริงโดยตรง

**เปลี่ยนไป symbol อื่น** — ได้ (EA อ่าน symbol จากกราฟ) แต่ต้อง: เปลี่ยน `InpNewsCurrencies` ให้ตรงคู่เงิน, ปรับ `InpMaxSpreadPoints`, และ backtest ใหม่ทั้งหมด

**Global Variables ค้าง** — ระบบเก็บ peak equity / R ของ position ใน terminal global variables (F3 ใน MT5 ดูได้ ชื่อขึ้นต้น `CG_`) ลบได้ถ้าต้องการ reset drawdown tracking (ระวัง: จะ reset circuit breaker ด้วย)

## 9. สิ่งที่ระบบนี้ไม่ทำ (โดยตั้งใจ)

- ไม่ Martingale, ไม่ Grid, ไม่เฉลี่ยขาดทุน, ไม่ย้าย SL ออกห่าง (โค้ดอนุญาตให้ SL ขยับเข้าหากำไรเท่านั้น)
- ไม่เปิดออเดอร์ไม่มี SL/TP — ไม่มีเส้นทางในโค้ดที่ทำได้
- ไม่เทรดตอนข่าวแรง ไม่เทรดนอก session ไม่เทรดสเปรดกว้าง
- ไม่รับประกันกำไร — ระบบที่ดีที่สุดคือระบบที่คุณ backtest เองจนเชื่อสถิติของมัน
