# Archive — เวอร์ชันบอทเทรดอัตโนมัติ (เลิกใช้แล้ว)

โฟลเดอร์นี้เก็บโค้ดเวอร์ชันก่อนหน้าที่ **เปิดออเดอร์อัตโนมัติ** ไว้เพื่ออ้างอิงเท่านั้น

**ระบบปัจจุบันคือระบบสัญญาณ** (`MQL5/Experts/CapitalGuardSignalEA.mq5`) ซึ่งวิเคราะห์ตลาด
และส่งการแจ้งเตือนเข้า **Telegram** โดยไม่เปิดออเดอร์ใด ๆ

## ไฟล์ในนี้

| ไฟล์ | หน้าที่เดิม |
|---|---|
| `Experts/CapitalGuardEA.mq5` | EA เทรดอัตโนมัติ SMC-first |
| `Experts/TradeTemplate_TP_SL.mq5` | template ตั้ง TP/SL อย่างง่าย (เวอร์ชันแรกสุด) |
| `Include/RiskManager.mqh` | position sizing, daily/weekly loss limits, drawdown breaker |
| `Include/TradeManager.mqh` | breakeven, partial close, ATR trailing, emergency exit |
| `Include/Logger.mqh` | บันทึกออเดอร์ CSV/JSONL |
| `Include/Dashboard.mqh` | แดชบอร์ดบัญชีเทรด |
| `Include/LineNotify.mqh` | ส่งข้อความเข้า LINE OA (ระบบเปลี่ยนไปใช้ Telegram แล้ว) |
| `python/` | ML pipeline + Monte Carlo สำหรับ log ของบอทเทรด |

## ถ้าจะกลับมาใช้

ต้องคัดลอกไฟล์ `Include/*.mqh` กลับไปที่ `MQL5/Include/CapitalGuard/` และ
`Experts/*.mq5` กลับไปที่ `MQL5/Experts/` ก่อน compile เพราะโมดูลเหล่านี้
ไม่ได้อยู่ในเส้นทาง include ของระบบสัญญาณแล้ว

⚠️ โค้ดชุดนี้ยังไม่ผ่านการ backtest จนครบตามที่เอกสารเดิมกำหนด — อย่านำไปใช้เงินจริง
