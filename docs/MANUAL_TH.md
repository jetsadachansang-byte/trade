# คู่มือติดตั้งและใช้งาน CapitalGuard Signal (ฉบับละเอียด)

ระบบวิเคราะห์ตลาดด้วย SMC/ICT บน MetaTrader 5 → ส่งสัญญาณเข้า LINE Official Account

> **ระบบนี้ไม่เปิดออเดอร์ให้** วิเคราะห์และแจ้งเตือนเท่านั้น การเปิดออเดอร์และบริหารความเสี่ยงเป็นของคุณทั้งหมด

---

## สารบัญ

1. [เตรียม LINE Official Account](#1-เตรียม-line-official-account)
2. [ตั้งค่า MetaTrader 5](#2-ตั้งค่า-metatrader-5)
3. [ติดตั้งไฟล์และ Compile](#3-ติดตั้งไฟล์และ-compile)
4. [ตรวจชื่อ Symbol ของโบรกเกอร์](#4-ตรวจชื่อ-symbol-ของโบรกเกอร์)
5. [ลาก EA ลงกราฟและตั้งค่า](#5-ลาก-ea-ลงกราฟและตั้งค่า)
6. [ทดสอบว่าเชื่อมต่อสำเร็จ](#6-ทดสอบว่าเชื่อมต่อสำเร็จ)
7. [ข้อความที่ระบบส่ง](#7-ข้อความที่ระบบส่ง)
8. [พารามิเตอร์ทั้งหมด](#8-พารามิเตอร์ทั้งหมด)
9. [Dashboard บนมือถือ](#9-dashboard-บนมือถือ)
10. [สถิติสัญญาณ](#10-สถิติสัญญาณ)
11. [รัน 24 ชม. ด้วย VPS](#11-รัน-24-ชม-ด้วย-vps)
12. [แก้ปัญหา](#12-แก้ปัญหา)

---

## 1. เตรียม LINE Official Account

### 1.1 เปิดใช้ Messaging API
1. เข้า [manager.line.biz](https://manager.line.biz) → เลือก OA ของคุณ
2. **Settings (ตั้งค่า) → Messaging API** → กด **Enable Messaging API**
3. เลือกหรือสร้าง **Provider** (ตั้งชื่ออะไรก็ได้ เช่น "CapitalGuard") → ยืนยัน

### 1.2 เอา Channel Access Token
1. เข้า [developers.line.biz/console](https://developers.line.biz/console) → login ด้วยบัญชี LINE เดียวกัน
2. เลือก Provider → เลือก channel ของ OA
3. แท็บ **Messaging API** → เลื่อนล่างสุด **Channel access token (long-lived)** → กด **Issue**
4. คัดลอก token เก็บไว้ (ยาว ~170 ตัวอักษร)

> 🔒 **ห้ามแชร์ token ให้ใคร** ใครมี token นี้สามารถส่งข้อความในนาม OA ของคุณได้ และห้าม commit ขึ้น GitHub

### 1.3 เพิ่ม OA เป็นเพื่อน
สแกน **QR code** ในหน้า Messaging API ด้วยมือถือ — ถ้าไม่ทำข้อนี้จะไม่ได้รับข้อความ แม้ระบบส่งสำเร็จ

### 1.4 ปิดข้อความอัตโนมัติ (แนะนำ)
LINE OA Manager → **Settings → Response settings** → ปิด **Auto-reply messages** และ **Greeting message**
เพื่อไม่ให้ข้อความอัตโนมัติมาปนกับสัญญาณ

> ℹ️ LINE Notify เดิมปิดบริการไปแล้ว (มี.ค. 2025) ระบบนี้ใช้ **Messaging API** ซึ่งเป็นช่องทางการปัจจุบัน

---

## 2. ตั้งค่า MetaTrader 5

**Tools → Options → Expert Advisors** → ติ๊ก ✅ **Allow WebRequest for listed URL** → กดเพิ่ม:

```
https://api.line.me
```

→ กด OK

> ⚠️ **ข้อนี้ลืมบ่อยที่สุด** ถ้าไม่ทำ EA จะทำงานปกติแต่ข้อความไม่ออก และ Journal จะขึ้น `WebRequest blocked`

เปิดสิทธิ์ปฏิทินข่าวด้วย: **Tools → Options → Server** → ติ๊ก **Enable news**

---

## 3. ติดตั้งไฟล์และ Compile

1. MT5 → **File → Open Data Folder**
2. คัดลอกไฟล์จาก repo:
   - `MQL5/Experts/CapitalGuardSignalEA.mq5` → ไปที่ `MQL5/Experts/`
   - โฟลเดอร์ `MQL5/Include/CapitalGuard/` **ทั้งโฟลเดอร์** → ไปที่ `MQL5/Include/`
3. เปิด MetaEditor (กด **F4** ใน MT5) → เปิด `CapitalGuardSignalEA.mq5`
4. กด **Compile (F7)** → ต้องขึ้น `0 errors`

> โฟลเดอร์ `archive/` ในโปรเจกต์คือเวอร์ชันบอทเทรดอัตโนมัติเดิม **ไม่ต้องคัดลอก** เก็บไว้อ้างอิงเท่านั้น

---

## 4. ตรวจชื่อ Symbol ของโบรกเกอร์

ชื่อ symbol ต่างกันในแต่ละโบรกเกอร์ ต้องตรวจก่อนตั้งค่า:

1. เปิด **Market Watch** (Ctrl+M) → คลิกขวา → **Show All**
2. ดูชื่อจริงของแต่ละคู่

| ชื่อมาตรฐาน | อาจเป็น |
|---|---|
| XAUUSD | `GOLD`, `XAUUSD.raw`, `XAUUSDm`, `GOLD#` |
| EURUSD | `EURUSDm`, `EURUSD.a`, `EURUSD_i` |

จดชื่อจริงไว้ใช้ในขั้นถัดไป — **symbol ที่พิมพ์ผิดระบบจะข้ามและเขียนเตือนใน Journal ไม่ทำให้ EA พัง**

---

## 5. ลาก EA ลงกราฟและตั้งค่า

ลาก `CapitalGuardSignalEA` ลง **กราฟเดียวเท่านั้น** (แนะนำกราฟทอง M5)

> EA จัดการทุก symbol เองผ่าน timer — **ไม่ต้องลากหลายกราฟ** ลากซ้ำจะทำให้ได้ข้อความซ้ำ

### ค่าที่ต้องแก้

**กลุ่ม `=== LINE OA ===`**

| Input | ใส่อะไร |
|---|---|
| `InpLineEnabled` | `true` |
| `InpLineToken` | วาง token จากข้อ 1.2 |
| `InpLineUserId` | **เว้นว่าง** = broadcast ถึงผู้ติดตามทุกคน (ง่ายสุด) |

**กลุ่ม `=== Symbols (priority order) ===`**

| Input | ค่าเริ่มต้น |
|---|---|
| `InpTier1Symbols` | `XAUUSD` (เปลี่ยนเป็น `GOLD` ถ้าโบรกเกอร์ใช้ชื่อนี้) |
| `InpTier2Symbols` | `EURUSD,GBPUSD,USDJPY,USDCHF,AUDUSD,NZDUSD,USDCAD` |
| `InpTier3Symbols` | `EURJPY,GBPJPY,EURGBP,AUDJPY,CADJPY,CHFJPY` |

ลำดับชื่อในรายการ = ลำดับความสำคัญในการส่งสัญญาณ

**กลุ่ม `=== Sessions ===`** — เวลาเป็น **เวลา server ของโบรกเกอร์** (มักเป็น GMT+2/+3)
ตรวจโดยดูเวลาที่มุมบน Market Watch เทียบกับเวลาไทย (ไทย GMT+7)

---

## 6. ทดสอบว่าเชื่อมต่อสำเร็จ

กด **OK** → ภายในไม่กี่วินาทีต้องได้ข้อความเข้า LINE:

```
🤖 CapitalGuard Signal เริ่มทำงาน
วิเคราะห์ 14 สินทรัพย์ (Tier1: XAUUSD)
ส่งเฉพาะสัญญาณคะแนน >= 90
```

ได้ข้อความนี้ = เชื่อมต่อครบวงจรเรียบร้อย ระบบจะเริ่มเฝ้าตลาดทันที

ถ้าไม่ได้ → ดูแท็บ **Experts** และ **Journal** ใน MT5 แล้วเทียบกับ[ตารางแก้ปัญหา](#12-แก้ปัญหา)

---

## 7. ข้อความที่ระบบส่ง

### 7.1 สัญญาณใหม่
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
⚠️ บริหารความเสี่ยงเอง ไม่เกิน 1% ต่อไม้ | ไม่ใช่คำแนะนำการลงทุน
```

**Entry Zone คืออะไร** — ช่วงราคาของ Order Block ที่ราคากำลัง mitigate อยู่ ไม่ใช่ราคาเดียว
เข้าได้ทั้งช่วง แต่ยิ่งใกล้ขอบล่าง (สำหรับ BUY) ยิ่งได้ RR ดีกว่า

### 7.2 ติดตามผลอัตโนมัติ

| ข้อความ | เมื่อไหร่ | ควรทำอะไร |
|---|---|---|
| ✅ **TP1 Hit** | ราคาถึง TP1 (1R) | ปิดบางส่วน + เลื่อน SL มาจุดเข้า (BE) |
| ✅ **TP2 Hit** | ราคาถึง TP2 (2R) | ปิดเพิ่ม หรือเลื่อน SL ตาม |
| ✅ **TP3 Hit** | ราคาถึง TP3 (3R) | ปิดทั้งหมด สัญญาณจบสมบูรณ์ |
| 🛑 **Stop Loss Hit** | ราคาถึง SL | ปิดตาม SL (ควรตั้ง SL ไว้ในออเดอร์ตั้งแต่แรก) |
| ❌ **Signal Cancelled** | โครงสร้างเปลี่ยนทิศ (CHoCH สวน) ก่อนถึง TP1 หรือเกิน 12 ชม. | ถ้ายังไม่เข้า = ไม่ต้องเข้าแล้ว / ถ้าเข้าแล้ว = พิจารณาปิดก่อนถึง SL |

> ⚠️ **ระบบไม่ปิดออเดอร์ให้** ข้อความ TP/SL Hit เป็นการแจ้งว่าราคาแตะระดับนั้นแล้ว คุณต้องจัดการออเดอร์เอง
> **แนะนำให้ตั้ง SL และ TP ในออเดอร์ทันทีตอนเปิด** เพื่อไม่ต้องเฝ้าจอ

---

## 8. พารามิเตอร์ทั้งหมด

### คุณภาพและปริมาณสัญญาณ
| Input | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| `InpScoreThreshold` | 90 | คะแนนขั้นต่ำ ต่ำกว่านี้ไม่ส่ง |
| `InpTier3Extra` | 2.0 | cross ต้องได้ 92+ |
| `InpMaxSignalsPerDay` | 3 | จำกัดต่อวัน รวมทุกสินทรัพย์ |
| `InpCooldownMinutes` | 60 | เว้นระยะระหว่างสัญญาณ |
| `InpSignalExpiryHrs` | 12 | ยกเลิกถ้าไม่ถึง TP1 ภายในกี่ชม. |
| `InpScanSeconds` | 15 | รอบสแกน Tier 1 |
| `InpTP1R` / `InpTP2R` / `InpTP3R` | 1 / 2 / 3 | ระยะ TP เป็น R |

### เงื่อนไข SMC (ทุกข้อต้องผ่าน)
| Input | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| `InpReqBosChoch` | true | ต้องมี BOS/CHoCH |
| `InpReqOrderBlock` / `InpMinOBQuality` | true / 60 | ต้องมี OB คุณภาพ ≥ 60 |
| `InpReqFVG` | true | ต้องมี Fair Value Gap |
| `InpReqSweep` | true | ต้องเกิด Liquidity Sweep ก่อน |
| `InpReqPremiumDiscount` / `InpDiscountMax` | true / 0.5 | Buy เฉพาะ discount |
| `InpReqMitigation` | true | ราคาต้องกลับมา mitigate |
| `InpReqTrendConfirm` | true | M30/M15 ห้ามสวน |
| `InpAllowCounterTrend` | false | สวนเทรนด์ได้เมื่อมี CHoCH ชัด |
| `InpReqLiquidityTarget` | false | บังคับมี pool ในทิศกำไร (เข้มพิเศษ) |

### ICT
| Input | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| `InpReqWeeklyBias` | true | W1 ห้ามสวนทิศ |
| `InpUseKillZones` | true | ส่งเฉพาะช่วง Kill Zone |
| `InpLondonKZStart/End` | 9 / 12 | London KZ (server time) |
| `InpNYKZStart/End` | 15 / 18 | New York KZ (server time) |
| `InpReqOTE` | false | บังคับอยู่ในโซน OTE 62–79% |

### ปัจจัยมหภาคของทองคำ
| Input | ใส่อะไร |
|---|---|
| `InpDxySymbol` | ชื่อ symbol ดัชนีดอลลาร์ (เช่น `USDX`) — เว้นว่างถ้าไม่มี |
| `InpYieldSymbol` | symbol พันธบัตร/yield — เว้นว่างถ้าไม่มี |
| `InpVixSymbol` | symbol VIX — เว้นว่างถ้าไม่มี |
| `InpMacroPenalty` | 5.0 = หักคะแนนเมื่อขัดแย้ง 1 ปัจจัย |

**กติกา:** DXY หรือ Yield วิ่งทิศเดียวกับทอง = ขัดแย้ง (ปกติทองสวนทางทั้งคู่)
ขัดแย้ง 1 ปัจจัย → หักคะแนน + เตือนในหมายเหตุ · ขัดแย้ง ≥2 ปัจจัย → **ระงับสัญญาณ**

### ข่าว
| Input | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| `InpNewsPreMin` / `InpNewsPostMin` | 45 / 45 | เว้นก่อน/หลังข่าวแรง (นาที) |
| `InpNewsCurrencies` | USD,EUR,GBP,JPY,... | สกุลที่เฝ้าข่าว |
| `InpNewsManualTimes` | ว่าง | ข่าวที่ปฏิทินไม่มี เช่น `2026.08.05 21:00;2026.08.07 19:30` |

> ข่าวสงคราม / เหตุการณ์ภูมิรัฐศาสตร์ / แถลงกะทันหัน ใส่เวลาเองที่ `InpNewsManualTimes` (เวลา server)

### อยากได้สัญญาณถี่ขึ้น?
ผ่อนเงื่อนไขทีละข้อ **อย่าลดหลายข้อพร้อมกัน** ลำดับที่แนะนำ:
1. `InpUseKillZones` = false (วิเคราะห์ทั้ง session)
2. `InpReqMitigation` = false
3. `InpReqFVG` = false
4. `InpScoreThreshold` = 85

สังเกตผลอย่างน้อย 1–2 สัปดาห์ก่อนผ่อนข้อถัดไป และดูสถิติจาก `signal_stats.py` ประกอบเสมอ

---

## 9. Dashboard บนมือถือ

EA เขียนไฟล์ `MQL5/Files/CapitalGuard/dashboard.html` (รีเฟรชตัวเองทุก 60 วินาที) แสดง:
สถานะตลาด · Kill Zone · ข่าว · สัญญาณล่าสุด · จำนวนสัญญาณวันนี้ · Win rate · **ตารางทุก symbol** พร้อม bias ทุก TF และสถานะว่ากำลังรออะไร

**วิธีเปิดจากมือถือ**

*ทางเลือกที่ 1 — VPS + web server (แนะนำ)*
```bash
cd "<MT5 Data Folder>/MQL5/Files"
python -m http.server 8080
```
เปิดจากมือถือ: `http://<ip-vps>:8080/CapitalGuard/dashboard.html`

*ทางเลือกที่ 2 — Cloud drive*
sync โฟลเดอร์ `MQL5/Files/CapitalGuard/` ขึ้น Dropbox / Google Drive แล้วเปิดไฟล์จากแอปมือถือ

---

## 10. สถิติสัญญาณ

ระบบบันทึกทุกเหตุการณ์ลง `MQL5/Files/CapitalGuard/signals_<magic>.csv` และ `.jsonl`

```bash
cd python
pip install -r requirements.txt
python signal_stats.py --log /path/to/signals_20260804.jsonl
```

ได้สรุป **รายวัน / รายสัปดาห์ / รายเดือน**: จำนวนสัญญาณ, wins, losses, ยกเลิก, win rate %, net R, คะแนนเฉลี่ย

ส่งออก CSV: เพิ่ม `--export summary.csv`

---

## 11. รัน 24 ชม. ด้วย VPS

ระบบต้องเปิด MT5 ค้างไว้ตลอดจึงจะวิเคราะห์ต่อเนื่องได้ ถ้าปิดคอมระบบจะหยุด

ทางเลือก:
- **VPS ของโบรกเกอร์** — หลายเจ้าให้ฟรีเมื่อมียอดเงินฝากขั้นต่ำ
- **VPS ทั่วไป** (Contabo, Vultr, AWS Lightsail) เริ่มต้น ~$5–10/เดือน สเปกขั้นต่ำ 2 vCPU / 4GB RAM
- **คอมที่บ้าน** เปิดค้าง + ตั้งไม่ให้ sleep (ประหยัดสุดแต่ต้องมีไฟ+เน็ตเสถียร)

ตั้งค่าเพิ่มบน VPS: เปิด MT5 auto-start, ปิด Windows Update auto-restart, เปิด **Allow WebRequest** ให้เรียบร้อยก่อนออกจากเครื่อง

---

## 12. แก้ปัญหา

| อาการ | สาเหตุ | วิธีแก้ |
|---|---|---|
| Journal ขึ้น `WebRequest blocked` | ไม่ได้ whitelist URL | เพิ่ม `https://api.line.me` ในข้อ 2 แล้ว**ลาก EA ลงกราฟใหม่** |
| `HTTP 401` | token ผิดหรือหมดอายุ | Issue token ใหม่ในข้อ 1.2 แล้ววางใหม่ |
| `HTTP 429` | โควตาข้อความหมด | ดูหัวข้อโควตาด้านล่าง |
| ไม่มี error แต่ไม่ได้ข้อความ | ยังไม่ได้เพิ่ม OA เป็นเพื่อน | สแกน QR ในข้อ 1.3 |
| `symbol XXX not found` | ชื่อไม่ตรงโบรกเกอร์ | แก้ตามชื่อจริงใน Market Watch (ข้อ 4) |
| ได้ข้อความซ้ำ 2 ชุด | ลาก EA หลายกราฟ | ลบออกให้เหลือกราฟเดียว |
| ไม่มีสัญญาณเลยหลายวัน | **ปกติตามดีไซน์** | ดู Status บน dashboard ว่ารออะไร ถ้าอยากถี่ขึ้นดูข้อ 8 |
| Compile error | Include ไม่ครบ | ตรวจว่าคัดลอกโฟลเดอร์ `CapitalGuard` ครบทุกไฟล์ |

### โควตาข้อความ LINE
แผนฟรี (Communication) = **500 ข้อความ/เดือน** และ **broadcast นับตามจำนวนผู้ติดตาม**
(ผู้ติดตาม 10 คน ส่ง 1 ครั้ง = ใช้ 10 ข้อความ)

- **ใช้คนเดียว** → ไม่มีปัญหา (3 สัญญาณ/วัน × ~4 ข้อความ ≈ 360/เดือน)
- **มีผู้ติดตามหลายคน** → ใส่ userId ตัวเองใน `InpLineUserId` เพื่อส่งเฉพาะตัวเอง (นับ 1 ข้อความ/ครั้งเสมอ)
  หา userId ได้ที่ LINE Developers → แท็บ **Basic settings** → **Your user ID** (ขึ้นต้นด้วย `U`)

---

## ข้อควรระวังก่อนใช้จริง

1. **เปิดสังเกตเฉย ๆ ก่อน 1–2 สัปดาห์** ดูว่าสัญญาณสมเหตุสมผลไหม ก่อนเอาไปเทรดเงินจริง
2. **บริหารความเสี่ยงเอง** — แนะนำไม่เกิน 1% ของพอร์ตต่อไม้ คำนวณ lot จากระยะ SL เสมอ
3. **ตั้ง SL/TP ในออเดอร์ทันทีตอนเปิด** ระบบไม่ปิดออเดอร์ให้
4. **สัญญาณคือความน่าจะเป็น ไม่ใช่ความแน่นอน** ไม่มีระบบไหนชนะ 100%
5. ระบบนี้เป็นเครื่องมือช่วยวิเคราะห์ **ไม่ใช่คำแนะนำการลงทุน**
