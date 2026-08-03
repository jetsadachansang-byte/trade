# CapitalGuard Signal — ระบบ AI วิเคราะห์ตลาดและแจ้งเตือนสัญญาณเข้า Telegram

ระบบวิเคราะห์ตลาดด้วย **Smart Money Concepts (SMC) + ICT** ทำงานบน MetaTrader 5 ตลอดเวลาที่ตลาดเปิด
เมื่อพบ setup คุณภาพสูง (คะแนน ≥ 90) จะ **ส่งสัญญาณแจ้งเตือนเข้า Telegram** ให้ผู้ใช้ตัดสินใจเปิดออเดอร์เอง

> ⚠️ **ระบบนี้ไม่เปิดออเดอร์ใด ๆ ทั้งสิ้น** — ไม่มีโค้ดส่งคำสั่งเทรด (`CTrade`, `OrderSend`) อยู่ในระบบเลย
> หน้าที่เดียวคือวิเคราะห์และแจ้งเตือน การเปิดออเดอร์และบริหารความเสี่ยงเป็นของผู้ใช้ทั้งหมด

📖 **คู่มือติดตั้งฉบับละเอียด: [docs/MANUAL_TH.md](docs/MANUAL_TH.md)**

---

## ระบบทำอะไร

| ด้าน | รายละเอียด |
|---|---|
| **แกนวิเคราะห์** | SMC เป็นหลัก — อินดิเคเตอร์ยืนยันได้เท่านั้น (น้ำหนัก 5%) |
| **สินทรัพย์** | 14 ตัว จัดลำดับ 3 Tier — XAUUSD เป็นหลัก |
| **Timeframe** | วิเคราะห์ W1, D1, H4, H1, M30, M15 พร้อมกัน — หา entry ที่ M5 (หรือ M1) |
| **เกณฑ์ส่งสัญญาณ** | คะแนน ≥ 90/100 **และ** ผ่าน SMC pipeline ครบทุกขั้น |
| **ช่องทางแจ้งเตือน** | Telegram (แชทส่วนตัว / กลุ่ม / channel) — ฟรีไม่จำกัดข้อความ |
| **ติดตามผล** | แจ้ง TP1 / TP2 / TP3 / SL / ยกเลิกสัญญาณ อัตโนมัติ |
| **Dashboard** | บนกราฟ + หน้าเว็บเปิดจากมือถือได้ |

---

## ขอบเขตสินทรัพย์ (จัดลำดับความสำคัญ)

| Tier | สินทรัพย์ | ความถี่วิเคราะห์ |
|---|---|---|
| **1** ⭐⭐⭐⭐⭐ | **XAUUSD** | ทุกรอบสแกน (15 วิ) — ได้ทรัพยากรมากที่สุด |
| **2** | EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, NZDUSD, USDCAD | ทุกแท่ง M5 ที่ปิด |
| **3** | EURJPY, GBPJPY, EURGBP, AUDJPY, CADJPY, CHFJPY | ทุกแท่งปิด + เกณฑ์คะแนนสูงกว่า (+2) |

**ลำดับการส่ง** เมื่อมีหลาย setup พร้อมกัน: XAUUSD → EURUSD → GBPUSD → USDJPY → major อื่น → cross

---

## ลำดับการวิเคราะห์ SMC (ขั้นใดไม่ผ่าน = ไม่ส่งสัญญาณ)

1. **Market Structure** — HH/HL/LH/LL บน W1/D1/H4/H1/entry → ทิศทางมาจากโครงสร้างเท่านั้น
2. **Liquidity** — Equal Highs/Lows, BSL/SSL pools
3. **BOS** — Break of Structure ในทิศทางเทรด
4. **CHoCH / MSS** — Change of Character
5. **Order Block** — ผ่านการให้คะแนนคุณภาพ ≥ 60 (ความสด / จำนวนครั้งที่ถูกแตะ / volume / ตำแหน่ง / เกิดหลัง sweep)
6. **Fair Value Gap** — imbalance ที่ยังไม่ถูก invalidate
7. **Liquidity Sweep** — ต้องเกิดการกวาดสภาพคล่องก่อนเสมอ
8. **Premium / Discount** — Buy เฉพาะ Discount, Sell เฉพาะ Premium (+ OTE ถ้าเปิดใช้)
9. **Mitigation** — ราคาต้องกลับมา mitigate OB หรือ FVG จริง ๆ
10. **Entry Confirmation** — M30/M15 ห้ามสวนทิศ + คะแนน ≥ 90
11. **News & Macro** — ไม่มีข่าวแรง, ปัจจัยมหภาคไม่ขัดแย้ง

## Confidence Score (0–100)

| หมวด | น้ำหนัก |
|---|---|
| Market Structure | 25% |
| Liquidity | 20% |
| BOS / CHoCH | 20% |
| Order Block | 15% |
| Fair Value Gap | 10% |
| Volume | 5% |
| Indicator Confirmation | 5% |

## ICT Layer

**Weekly/Daily Bias** (W1 ห้ามสวน) · **Kill Zones** (London 09–12, NY 15–18 server) · **OTE** (pullback 62–79%) · **Power of Three** (Accumulation → Manipulation/sweep → Distribution/BOS) · **SMT Divergence** (ใช้ DXY เป็น proxy)

## ปัจจัยมหภาคสำหรับทองคำ

ติดตาม **DXY / US Treasury Yields / VIX** (ถ้าโบรกเกอร์มี symbol):
- ขัดแย้ง **1 ปัจจัย** → หักคะแนน + เขียนเตือนในหมายเหตุ
- ขัดแย้ง **≥ 2 ปัจจัย** → **ระงับสัญญาณทันที**

ส่วน FOMC / CPI / PPI / NFP / Core PCE / ดอกเบี้ย / แถลง Fed จัดการโดย News Filter (เว้น ±45 นาที) และข่าวสงคราม/ภูมิรัฐศาสตร์ใส่เวลาเองได้

---

## ตัวอย่างข้อความที่ส่งเข้า Telegram

```
📊 สินทรัพย์: XAUUSD
📈 ประเภท: BUY
🎯 ราคาเข้า (Entry Zone): 3245.10 – 3247.80
🛑 Stop Loss: 3238.40
🎯 Take Profit 1: 3254.20
🎯 Take Profit 2: 3261.00
🎯 Take Profit 3: 3267.80
📉 Risk : Reward = 1 : 2.0
⭐ Confidence Score: 92%
🧠 เหตุผลในการวิเคราะห์:
• Market Structure: W:UP D:UP H4:UP H1:UP
• BOS ✔
• Liquidity Sweep ✔
• Order Block ✔ (คุณภาพ 84/100)
• Fair Value Gap ✔ (mitigated)
• Discount Zone ✔ (rangePos 0.32) | OTE ✔
• Regime: TREND UP / NORMAL VOL
⏰ เวลาที่วิเคราะห์: 2026.08.03 16:20 (server)
📌 หมายเหตุ:
• รอแท่งเทียน M5 ปิดยืนยันก่อนเข้า
• ยกเลิกสัญญาณหากราคาปิดเลย SL ก่อนเข้าไม้
• หลีกเลี่ยงการเข้าใกล้ช่วงประกาศข่าวสำคัญ
━━━━━━━━━━━━━━
🆔 Signal ID: 1785312000
⚠️ บริหารความเสี่ยงเอง ไม่เกิน 1% ต่อไม้ | ไม่ใช่คำแนะนำการลงทุน
```

จากนั้นระบบติดตามให้อัตโนมัติ: **✅ TP1 Hit** (แนะนำเลื่อน SL เป็น BE) → **✅ TP2** → **✅ TP3** → **🛑 Stop Loss Hit** → **❌ Signal Cancelled** (โครงสร้างเปลี่ยนทิศ หรือเกิน 12 ชม.)

---

## การคุมคุณภาพสัญญาณ

- คะแนนขั้นต่ำ **90** (cross ต้อง 92)
- สูงสุด **3 สัญญาณ/วัน** รวมทุกสินทรัพย์
- เว้น **60 นาที** ระหว่างสัญญาณ
- ไม่ส่งซ้อน symbol เดิมขณะยังมีสัญญาณ active
- เฉพาะช่วง Kill Zone และไม่มีข่าวแรง

> **ไม่มีสัญญาณทั้งวันถือเป็นเรื่องปกติ** — ระบบออกแบบให้เน้นคุณภาพมากกว่าปริมาณ ห้ามส่งสัญญาณเพียงเพื่อให้มีสัญญาณ

---

## โครงสร้างโปรเจกต์

```
MQL5/
├── Experts/
│   └── CapitalGuardSignalEA.mq5   ← EA เดียวที่ต้องรัน (วิเคราะห์+แจ้งเตือน)
└── Include/CapitalGuard/
    ├── SymbolAnalyst.mqh          ← นักวิเคราะห์ 1 ตัว/1 สินทรัพย์
    ├── MarketStructure.mqh        ← HH/HL/LH/LL, BOS, CHoCH
    ├── SmartMoney.mqh             ← Liquidity pools, Sweep, OB, FVG, Premium/Discount
    ├── ScoringEngine.mqh          ← คะแนน SMC ถ่วงน้ำหนัก
    ├── IndicatorSet.mqh           ← อินดิเคเตอร์ (ยืนยันเท่านั้น)
    ├── Regime.mqh                 ← Trend/Range × High/Low Volatility
    ├── NewsFilter.mqh             ← ปฏิทินข่าวเศรษฐกิจ
    ├── TelegramNotify.mqh         ← ส่งข้อความเข้า Telegram (Bot API)
    └── SignalManager.mqh          ← วงจรชีวิตสัญญาณ TP1/2/3, SL, ยกเลิก
python/
├── signal_stats.py                ← สถิติ รายวัน/สัปดาห์/เดือน
└── requirements.txt
docs/
└── MANUAL_TH.md                   ← คู่มือฉบับละเอียด
archive/                           ← เวอร์ชันบอทเทรดอัตโนมัติเดิม (ไม่ใช้แล้ว เก็บไว้อ้างอิง)
```

---

## เริ่มใช้งาน (สรุป 6 ขั้น)

1. สร้างบอทกับ **@BotFather** ใน Telegram → พิมพ์ `/newbot` → ได้ **bot token**
2. ทักบอทที่สร้าง (กด Start) → หา **chat id** (ใช้ `@userinfobot` หรือเปิด `InpTgShowChatId`)
3. MT5 → Tools → Options → Expert Advisors → **Allow WebRequest** → เพิ่ม `https://api.telegram.org`
4. คัดลอกโฟลเดอร์ `MQL5/` ลง MT5 Data Folder → Compile (F7)
5. ตรวจชื่อ symbol จริงของโบรกเกอร์ใน Market Watch แล้วแก้ใน input
6. ลาก EA ลง **กราฟเดียว** → ใส่ token + chat id → ต้องได้ข้อความทดสอบเข้า Telegram

รายละเอียดทุกขั้นตอนพร้อมวิธีแก้ปัญหา: **[docs/MANUAL_TH.md](docs/MANUAL_TH.md)**

---

## ใช้งานบนมือถือ

⚠️ **แอป MT5 บนมือถือรัน EA ไม่ได้** (ข้อจำกัดของ MetaTrader เอง) สถาปัตยกรรมจริงคือ:

```
[ คอม/VPS เปิด 24 ชม. ]              [ มือถือ ]
  MT5 + EA วิเคราะห์      ──────►   Telegram รับสัญญาณ
                                     MT5 มือถือ เปิดออเดอร์
```

| ทำบนมือถือได้ | ต้องใช้คอม/VPS |
|---|---|
| สร้าง Telegram bot, หา chat id | ติดตั้ง + compile EA |
| รับสัญญาณ, เปิด/ปิดออเดอร์ | รัน EA วิเคราะห์ตลาด (เปิดค้างตลอด) |
| ดู dashboard, ควบคุม VPS ผ่าน Remote Desktop | |

รายละเอียดขั้นตอนบนมือถือ (คำนวณ lot, เปิดออเดอร์ใน MT5 มือถือ, จัดการเมื่อ TP1 Hit): [คู่มือข้อ 13](docs/MANUAL_TH.md#13-ใช้งานผ่านมือถือ)

---

## ข้อจำกัดที่ควรทราบ

- **ไม่รับประกันผลกำไร** — สัญญาณคือความน่าจะเป็น ไม่ใช่ความแน่นอน
- **EA รันบนมือถือไม่ได้** ต้องใช้คอมหรือ VPS เปิดค้างไว้
- ผู้ใช้ต้องบริหารความเสี่ยงเอง (แนะนำไม่เกิน 1% ต่อไม้)
- Strategy Tester ใช้ WebRequest ไม่ได้ — ตอนทดสอบระบบจะพิมพ์ข้อความลง Journal แทน
- ต้องเปิด MT5 ค้างไว้ตลอด (แนะนำ VPS) ระบบจึงจะเฝ้าตลาดต่อเนื่อง
- ปฏิทินข่าวไม่ทำงานใน Strategy Tester (ใช้ manual list แทน)
- ไม่ใช่คำแนะนำการลงทุน
