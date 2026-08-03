//+------------------------------------------------------------------+
//|                                              SignalManager.mqh   |
//|  CapitalGuard - Multi-symbol signal lifecycle manager            |
//|                                                                  |
//|  The system does NOT trade. It issues signals (any symbol) to    |
//|  Telegram for the user to execute manually, then tracks each     |
//|  one against that symbol's live prices:                          |
//|   - TP1 / TP2 / TP3 hit  -> Telegram notification                |
//|   - Stop Loss hit        -> Telegram notification                |
//|   - Setup invalidated    -> "Signal Cancelled" + reason          |
//|                                                                  |
//|  Every event is appended to CSV + JSONL (UTF-8) for the Python   |
//|  stats pipeline (daily / weekly / monthly summaries).            |
//+------------------------------------------------------------------+
#ifndef CG_SIGNAL_MANAGER_MQH
#define CG_SIGNAL_MANAGER_MQH

#include "TelegramNotify.mqh"
#include "MarketStructure.mqh"
#include "SymbolAnalyst.mqh"

//--- lifecycle states of one signal
enum ENUM_SIGNAL_STATUS
  {
   SIG_ACTIVE,       // issued, nothing hit yet
   SIG_TP1,          // TP1 reached (runner still live)
   SIG_TP2,          // TP2 reached (runner still live)
   SIG_TP3,          // TP3 reached - completed
   SIG_SL,           // stop loss hit - closed
   SIG_CANCELLED     // setup invalidated before playing out
  };

//--- one issued signal
struct SSignalRecord
  {
   long              id;            // unique id (epoch seconds)
   datetime          time;          // time of analysis
   string            symbol;
   int               tier;
   int               dir;           // +1 buy, -1 sell
   double            entry;
   double            sl;
   double            tp1, tp2, tp3;
   double            rr;
   double            score;
   string            reasons;
   string            tf;
   ENUM_SIGNAL_STATUS status;
   bool              tp1Hit, tp2Hit, tp3Hit;
  };

//+------------------------------------------------------------------+
//| Multi-symbol signal lifecycle manager                            |
//+------------------------------------------------------------------+
class CSignalManager
  {
private:
   SSignalRecord     m_signals[];      // session history (newest last)
   CTelegramNotify  *m_tg;
   string            m_csvFile;
   string            m_jsonFile;
   int               m_expiryHours;

   //--- append one line to a file (UTF-8, header on creation)
   void              AppendLine(const string filename, const string line, const string header)
     {
      int fh = FileOpen(filename, FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_SHARE_READ, ';', CP_UTF8);
      if(fh == INVALID_HANDLE)
        {
         PrintFormat("SignalManager: cannot open %s (err %d)", filename, GetLastError());
         return;
        }
      if(FileSize(fh) == 0 && header != "")
         FileWriteString(fh, header + "\n");
      FileSeek(fh, 0, SEEK_END);
      FileWriteString(fh, line + "\n");
      FileClose(fh);
     }

   //--- escape for CSV / JSON embedding
   string            CsvEscape(const string s) const
     {
      string out = s;
      StringReplace(out, "\"", "'");  StringReplace(out, ",", ";");
      StringReplace(out, "\n", " | ");
      return(out);
     }
   string            JsonEscape(const string s) const
     {
      string out = s;
      StringReplace(out, "\\", "\\\\"); StringReplace(out, "\"", "\\\"");
      StringReplace(out, "\n", " | ");
      return(out);
     }

   //--- write one lifecycle event of a signal to both log files
   void              LogEvent(const SSignalRecord &s, const string event, const string note)
     {
      string ts = TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS);
      string csvHeader = "event,time,signal_id,symbol,tier,dir,entry,sl,tp1,tp2,tp3,rr,score,tf,status,note,reasons";
      string csv = StringFormat("%s,%s,%I64d,%s,%d,%s,%.5f,%.5f,%.5f,%.5f,%.5f,%.2f,%.1f,%s,%s,%s,%s",
                                event, ts, s.id, s.symbol, s.tier, s.dir > 0 ? "BUY" : "SELL",
                                s.entry, s.sl, s.tp1, s.tp2, s.tp3, s.rr, s.score,
                                s.tf, StatusName(s.status), CsvEscape(note), CsvEscape(s.reasons));
      AppendLine(m_csvFile, csv, csvHeader);

      string json = StringFormat(
         "{\"event\":\"%s\",\"time\":\"%s\",\"signal_id\":%I64d,\"symbol\":\"%s\",\"tier\":%d,"
         "\"dir\":\"%s\",\"entry\":%.5f,\"sl\":%.5f,\"tp1\":%.5f,\"tp2\":%.5f,\"tp3\":%.5f,"
         "\"rr\":%.2f,\"score\":%.1f,\"tf\":\"%s\",\"status\":\"%s\","
         "\"note\":\"%s\",\"reasons\":\"%s\"}",
         event, ts, s.id, s.symbol, s.tier, s.dir > 0 ? "BUY" : "SELL",
         s.entry, s.sl, s.tp1, s.tp2, s.tp3, s.rr, s.score,
         s.tf, StatusName(s.status), JsonEscape(note), JsonEscape(s.reasons));
      AppendLine(m_jsonFile, json, "");
     }

public:
   //--- configure; `tg` is the shared Telegram client
   void              Init(CTelegramNotify *tg, const long magic, const int expiryHours)
     {
      m_tg          = tg;
      m_expiryHours = expiryHours;
      m_csvFile     = StringFormat("CapitalGuard\\signals_%I64d.csv", magic);
      m_jsonFile    = StringFormat("CapitalGuard\\signals_%I64d.jsonl", magic);
      ArrayResize(m_signals, 0);
     }

   //--- readable status label
   static string     StatusName(const ENUM_SIGNAL_STATUS st)
     {
      switch(st)
        {
         case SIG_ACTIVE:    return("ACTIVE");
         case SIG_TP1:       return("TP1");
         case SIG_TP2:       return("TP2");
         case SIG_TP3:       return("TP3_DONE");
         case SIG_SL:        return("SL_HIT");
         case SIG_CANCELLED: return("CANCELLED");
        }
      return("UNKNOWN");
     }

   //--- true while the record is still being tracked
   bool              IsLive(const SSignalRecord &s) const
     {
      return(s.status == SIG_ACTIVE || s.status == SIG_TP1 || s.status == SIG_TP2);
     }

   //--- any live signal for this symbol? (avoid stacking per symbol)
   bool              HasActiveSignal(const string symbol)
     {
      for(int i = ArraySize(m_signals) - 1; i >= 0; i--)
         if(m_signals[i].symbol == symbol && IsLive(m_signals[i]))
            return(true);
      return(false);
     }

   //--- number of live signals across all symbols
   int               ActiveCount()
     {
      int n = 0;
      for(int i = 0; i < ArraySize(m_signals); i++)
         if(IsLive(m_signals[i])) n++;
      return(n);
     }

   //--- signals issued since the given time
   int               SignalsSince(const datetime from) const
     {
      int count = 0;
      for(int i = 0; i < ArraySize(m_signals); i++)
         if(m_signals[i].time >= from) count++;
      return(count);
     }

   //--- session win rate: win = reached TP1+; loss = SL without TP1
   void              Stats(int &wins, int &losses, int &cancelled)
     {
      wins = 0; losses = 0; cancelled = 0;
      for(int i = 0; i < ArraySize(m_signals); i++)
        {
         if(m_signals[i].status == SIG_CANCELLED) { cancelled++; continue; }
         if(m_signals[i].tp1Hit)                    wins++;
         else if(m_signals[i].status == SIG_SL)     losses++;
        }
     }

   //--- last issued signal (dashboard); false when none yet
   bool              LastSignal(SSignalRecord &out) const
     {
      int n = ArraySize(m_signals);
      if(n == 0) return(false);
      out = m_signals[n - 1];
      return(true);
     }

   //--- register + broadcast a candidate produced by an analyst
   long              NewSignal(const SSignalCandidate &c)
     {
      SSignalRecord s;
      s.id     = (long)TimeCurrent();
      s.time   = TimeCurrent();
      s.symbol = c.symbol;
      s.tier   = c.tier;
      s.dir    = c.dir;
      s.entry  = c.entry;  s.sl = c.sl;
      s.tp1    = c.tp1;    s.tp2 = c.tp2;  s.tp3 = c.tp3;
      s.rr     = c.rr;     s.score = c.score;
      s.reasons = c.reasons;
      s.tf     = c.tf;
      s.status = SIG_ACTIVE;
      s.tp1Hit = false;  s.tp2Hit = false;  s.tp3Hit = false;

      int n = ArraySize(m_signals);
      ArrayResize(m_signals, n + 1);
      m_signals[n] = s;

      //--- Telegram message in the required notification format.
      //--- HTML tags are safe here: dynamic parts are symbol names and
      //--- numbers only (parse_mode=HTML is set by the client).
      int digits = (int)SymbolInfoInteger(c.symbol, SYMBOL_DIGITS);
      string msg;
      msg  = "📊 <b>สินทรัพย์: " + c.symbol + "</b>\n";
      msg += "📈 <b>ประเภท: " + (c.dir > 0 ? "BUY" : "SELL") + "</b>\n";
      msg += "━━━━━━━━━━━━━━\n";
      msg += "🎯 ราคาเข้า (Entry Zone): <b>" + DoubleToString(c.entryLow, digits)
           + " – " + DoubleToString(c.entryHigh, digits) + "</b>\n";
      msg += "🛑 Stop Loss: <b>" + DoubleToString(c.sl, digits) + "</b>\n";
      msg += "🎯 Take Profit 1: " + DoubleToString(c.tp1, digits) + "\n";
      msg += "🎯 Take Profit 2: " + DoubleToString(c.tp2, digits) + "\n";
      msg += "🎯 Take Profit 3: " + DoubleToString(c.tp3, digits) + "\n";
      msg += StringFormat("📉 Risk : Reward = 1 : %.1f\n", c.rr);
      msg += StringFormat("⭐ Confidence Score: <b>%.0f%%</b>\n", c.score);
      msg += "━━━━━━━━━━━━━━\n";
      msg += "🧠 <b>เหตุผลในการวิเคราะห์:</b>\n" + c.reasons + "\n";
      msg += "⏰ เวลาที่วิเคราะห์: " + TimeToString(s.time, TIME_DATE|TIME_MINUTES) + " (server)\n";
      msg += "📌 <b>หมายเหตุ:</b>\n" + c.notes + "\n";
      msg += "━━━━━━━━━━━━━━\n";
      msg += StringFormat("🆔 Signal ID: %I64d\n", s.id);
      msg += "⚠️ <i>บริหารความเสี่ยงเอง ไม่เกิน 1% ต่อไม้ | ไม่ใช่คำแนะนำการลงทุน</i>";
      m_tg.Push(msg);

      LogEvent(s, "SIGNAL", "issued");
      return(s.id);
     }

   //--- tick/cycle monitoring of every live signal (all symbols).
   //--- Uses each record's own symbol prices.
   void              Monitor()
     {
      for(int i = 0; i < ArraySize(m_signals); i++)
        {
         if(!IsLive(m_signals[i]))
            continue;

         string sym  = m_signals[i].symbol;
         double bid  = SymbolInfoDouble(sym, SYMBOL_BID);
         double ask  = SymbolInfoDouble(sym, SYMBOL_ASK);
         if(bid <= 0.0 || ask <= 0.0) continue;    // no quotes right now
         int    digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
         bool   isBuy = (m_signals[i].dir > 0);
         double px    = isBuy ? bid : ask;         // long exits on bid

         //--- 1) stop loss
         bool slHit = isBuy ? (px <= m_signals[i].sl) : (px >= m_signals[i].sl);
         if(slHit)
           {
            m_signals[i].status = SIG_SL;
            m_tg.Push(StringFormat("🛑 <b>Stop Loss Hit</b>\n%s %s @ <b>%s</b>\n🆔 Signal ID: %I64d",
                                   sym, isBuy ? "BUY" : "SELL",
                                   DoubleToString(m_signals[i].sl, digits), m_signals[i].id));
            LogEvent(m_signals[i], "SL_HIT", "");
            continue;
           }

         //--- 2) take profits (each announced once, in order)
         if(!m_signals[i].tp1Hit)
           {
            bool hit = isBuy ? (px >= m_signals[i].tp1) : (px <= m_signals[i].tp1);
            if(hit)
              {
               m_signals[i].tp1Hit = true;
               m_signals[i].status = SIG_TP1;
               m_tg.Push(StringFormat("✅ <b>TP1 Hit</b> (1R)\n%s %s @ <b>%s</b>\n💡 แนะนำ: ปิดบางส่วน + เลื่อน SL มาจุดเข้า (Break Even)\n🆔 Signal ID: %I64d",
                                      sym, isBuy ? "BUY" : "SELL",
                                      DoubleToString(m_signals[i].tp1, digits), m_signals[i].id));
               LogEvent(m_signals[i], "TP1_HIT", "");
              }
           }
         if(m_signals[i].tp1Hit && !m_signals[i].tp2Hit)
           {
            bool hit = isBuy ? (px >= m_signals[i].tp2) : (px <= m_signals[i].tp2);
            if(hit)
              {
               m_signals[i].tp2Hit = true;
               m_signals[i].status = SIG_TP2;
               m_tg.Push(StringFormat("✅ <b>TP2 Hit</b> (2R)\n%s %s @ <b>%s</b>\n💡 แนะนำ: ปิดเพิ่ม หรือเลื่อน SL ตามกำไร\n🆔 Signal ID: %I64d",
                                      sym, isBuy ? "BUY" : "SELL",
                                      DoubleToString(m_signals[i].tp2, digits), m_signals[i].id));
               LogEvent(m_signals[i], "TP2_HIT", "");
              }
           }
         if(m_signals[i].tp2Hit && !m_signals[i].tp3Hit)
           {
            bool hit = isBuy ? (px >= m_signals[i].tp3) : (px <= m_signals[i].tp3);
            if(hit)
              {
               m_signals[i].tp3Hit = true;
               m_signals[i].status = SIG_TP3;
               m_tg.Push(StringFormat("🎯 <b>TP3 Hit</b> (3R) — สัญญาณจบสมบูรณ์\n%s %s @ <b>%s</b>\n🆔 Signal ID: %I64d",
                                      sym, isBuy ? "BUY" : "SELL",
                                      DoubleToString(m_signals[i].tp3, digits), m_signals[i].id));
               LogEvent(m_signals[i], "TP3_HIT", "completed");
               continue;
              }
           }

         //--- 3) expiry: untouched for too long
         if(m_signals[i].status == SIG_ACTIVE && m_expiryHours > 0 &&
            TimeCurrent() - m_signals[i].time >= (long)m_expiryHours * 3600)
           {
            m_signals[i].status = SIG_CANCELLED;
            m_tg.Push(StringFormat("❌ <b>Signal Cancelled</b>\n%s %s\nเหตุผล: เกินเวลา %d ชม. โดยไม่ถึง TP1\n🆔 Signal ID: %I64d",
                                   sym, isBuy ? "BUY" : "SELL", m_expiryHours, m_signals[i].id));
            LogEvent(m_signals[i], "CANCELLED", "expired");
           }
        }
     }

   //--- structure-based invalidation for one symbol: an opposing
   //--- CHoCH before TP1 kills the idea. Call after each rescan.
   void              CheckInvalidation(const string symbol, const SStructureInfo &st)
     {
      if(!st.recentCHoCH || st.bias == 0)
         return;
      for(int i = 0; i < ArraySize(m_signals); i++)
        {
         if(m_signals[i].symbol != symbol) continue;
         if(m_signals[i].status != SIG_ACTIVE) continue;
         if(st.bias == -m_signals[i].dir)
           {
            m_signals[i].status = SIG_CANCELLED;
            m_tg.Push(StringFormat("❌ <b>Signal Cancelled</b>\n%s %s\nเหตุผล: โครงสร้างตลาดเปลี่ยนทิศ (CHoCH สวนทาง)\n💡 ถ้ายังไม่เข้า = ไม่ต้องเข้าแล้ว / ถ้าเข้าแล้ว = พิจารณาปิดก่อนถึง SL\n🆔 Signal ID: %I64d",
                                   symbol, m_signals[i].dir > 0 ? "BUY" : "SELL", m_signals[i].id));
            LogEvent(m_signals[i], "CANCELLED", "structure flipped (CHoCH)");
           }
        }
     }
  };

#endif // CG_SIGNAL_MANAGER_MQH
