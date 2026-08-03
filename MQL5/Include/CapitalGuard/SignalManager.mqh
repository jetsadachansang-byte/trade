//+------------------------------------------------------------------+
//|                                              SignalManager.mqh   |
//|  CapitalGuard - Signal lifecycle manager                         |
//|                                                                  |
//|  The system does NOT trade. It issues signals for the user to    |
//|  execute manually, then tracks each signal against live prices:  |
//|   - TP1 / TP2 / TP3 hit  -> LINE notification                    |
//|   - Stop Loss hit        -> LINE notification                    |
//|   - Setup invalidated    -> "Signal Cancelled" + reason          |
//|     (structure flips against the idea, or the signal expires)    |
//|                                                                  |
//|  Every event is appended to CSV + JSONL for the Python stats     |
//|  pipeline (daily / weekly / monthly summaries).                  |
//+------------------------------------------------------------------+
#ifndef CG_SIGNAL_MANAGER_MQH
#define CG_SIGNAL_MANAGER_MQH

#include "LineNotify.mqh"
#include "MarketStructure.mqh"

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
   int               dir;           // +1 buy, -1 sell
   double            entry;
   double            sl;
   double            tp1, tp2, tp3;
   double            rr;            // headline RR (entry->TP2 vs entry->SL)
   double            score;         // confidence 0-100
   string            reasons;       // entry reasons (for the log)
   string            tf;            // entry timeframe label
   ENUM_SIGNAL_STATUS status;
   bool              tp1Hit, tp2Hit, tp3Hit;
  };

//+------------------------------------------------------------------+
//| Signal lifecycle manager                                         |
//+------------------------------------------------------------------+
class CSignalManager
  {
private:
   SSignalRecord     m_signals[];      // session history (newest last)
   CLineNotify      *m_line;
   string            m_symbol;
   string            m_csvFile;
   string            m_jsonFile;
   int               m_expiryHours;    // cancel untouched signals after this

   //--- append one line to a file, creating it with a header if new
   //--- (UTF-8 so Thai text in reasons survives)
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
      string csvHeader = "event,time,signal_id,symbol,dir,entry,sl,tp1,tp2,tp3,rr,score,tf,status,note,reasons";
      string csv = StringFormat("%s,%s,%I64d,%s,%s,%.5f,%.5f,%.5f,%.5f,%.5f,%.2f,%.1f,%s,%s,%s,%s",
                                event, ts, s.id, m_symbol, s.dir > 0 ? "BUY" : "SELL",
                                s.entry, s.sl, s.tp1, s.tp2, s.tp3, s.rr, s.score,
                                s.tf, StatusName(s.status), CsvEscape(note), CsvEscape(s.reasons));
      AppendLine(m_csvFile, csv, csvHeader);

      string json = StringFormat(
         "{\"event\":\"%s\",\"time\":\"%s\",\"signal_id\":%I64d,\"symbol\":\"%s\",\"dir\":\"%s\","
         "\"entry\":%.5f,\"sl\":%.5f,\"tp1\":%.5f,\"tp2\":%.5f,\"tp3\":%.5f,"
         "\"rr\":%.2f,\"score\":%.1f,\"tf\":\"%s\",\"status\":\"%s\","
         "\"note\":\"%s\",\"reasons\":\"%s\"}",
         event, ts, s.id, m_symbol, s.dir > 0 ? "BUY" : "SELL",
         s.entry, s.sl, s.tp1, s.tp2, s.tp3, s.rr, s.score,
         s.tf, StatusName(s.status), JsonEscape(note), JsonEscape(s.reasons));
      AppendLine(m_jsonFile, json, "");
     }

public:
   //--- configure; `line` is the shared LINE client
   void              Init(CLineNotify *line, const string symbol, const long magic,
                          const int expiryHours)
     {
      m_line        = line;
      m_symbol      = symbol;
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

   //--- is any signal still being tracked? (used to avoid stacking)
   bool              HasActiveSignal() const
     {
      for(int i = ArraySize(m_signals) - 1; i >= 0; i--)
         if(m_signals[i].status == SIG_ACTIVE || m_signals[i].status == SIG_TP1 ||
            m_signals[i].status == SIG_TP2)
            return(true);
      return(false);
     }

   //--- signals issued since the given day start
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

   //--- last issued signal (for the dashboard); false when none yet
   bool              LastSignal(SSignalRecord &out) const
     {
      int n = ArraySize(m_signals);
      if(n == 0) return(false);
      out = m_signals[n - 1];
      return(true);
     }

   //--- register + broadcast a fresh signal; returns its id
   long              NewSignal(const int dir, const double entry, const double sl,
                               const double tp1, const double tp2, const double tp3,
                               const double rr, const double score, const string reasons,
                               const string tf)
     {
      SSignalRecord s;
      s.id     = (long)TimeCurrent();
      s.time   = TimeCurrent();
      s.dir    = dir;
      s.entry  = entry;  s.sl = sl;
      s.tp1    = tp1;    s.tp2 = tp2;  s.tp3 = tp3;
      s.rr     = rr;     s.score = score;
      s.reasons = reasons;
      s.tf     = tf;
      s.status = SIG_ACTIVE;
      s.tp1Hit = false;  s.tp2Hit = false;  s.tp3Hit = false;

      int n = ArraySize(m_signals);
      ArrayResize(m_signals, n + 1);
      m_signals[n] = s;

      //--- LINE message exactly per the required format
      int digits = (int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS);
      string msg;
      msg  = (dir > 0 ? "📈 BUY SIGNAL" : "📉 SELL SIGNAL") + "\n";
      msg += "━━━━━━━━━━━━━━\n";
      msg += "คู่เงิน: " + m_symbol + "\n";
      msg += "ราคาเข้า: " + DoubleToString(entry, digits) + "\n";
      msg += "Stop Loss: " + DoubleToString(sl, digits) + "\n";
      msg += "Take Profit 1: " + DoubleToString(tp1, digits) + "\n";
      msg += "Take Profit 2: " + DoubleToString(tp2, digits) + "\n";
      msg += "Take Profit 3: " + DoubleToString(tp3, digits) + "\n";
      msg += StringFormat("Risk : Reward = 1 : %.1f\n", rr);
      msg += "Timeframe: " + tf + "\n";
      msg += StringFormat("Confidence Score: %.0f/100\n", score);
      msg += "━━━━━━━━━━━━━━\n";
      msg += "เหตุผลในการเข้า:\n" + reasons + "\n";
      msg += "━━━━━━━━━━━━━━\n";
      msg += "เวลาวิเคราะห์: " + TimeToString(s.time, TIME_DATE|TIME_MINUTES) + " (server)\n";
      msg += "⚠️ บริหารความเสี่ยงเอง ไม่เกิน 1% ต่อไม้ | ไม่ใช่คำแนะนำการลงทุน";
      m_line.Push(msg);

      LogEvent(s, "SIGNAL", "issued");
      return(s.id);
     }

   //--- tick-level monitoring of every live signal.
   //--- `st` = latest entry-TF structure (for invalidation checks)
   void              Monitor(const double bid, const double ask, const SStructureInfo &st)
     {
      int digits = (int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS);
      for(int i = 0; i < ArraySize(m_signals); i++)
        {
         if(m_signals[i].status == SIG_TP3 || m_signals[i].status == SIG_SL ||
            m_signals[i].status == SIG_CANCELLED)
            continue;

         bool   isBuy = (m_signals[i].dir > 0);
         //--- a long is exited on bid, a short on ask
         double px    = isBuy ? bid : ask;

         //--- 1) stop loss
         bool slHit = isBuy ? (px <= m_signals[i].sl) : (px >= m_signals[i].sl);
         if(slHit)
           {
            m_signals[i].status = SIG_SL;
            m_line.Push(StringFormat("🛑 Stop Loss Hit\n%s %s @ %s\nSignal ID: %I64d",
                                     m_symbol, isBuy ? "BUY" : "SELL",
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
               m_line.Push(StringFormat("✅ TP1 Hit\n%s %s @ %s\nแนะนำ: เลื่อน SL มาที่จุดเข้า (Break Even)\nSignal ID: %I64d",
                                        m_symbol, isBuy ? "BUY" : "SELL",
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
               m_line.Push(StringFormat("✅ TP2 Hit\n%s %s @ %s\nSignal ID: %I64d",
                                        m_symbol, isBuy ? "BUY" : "SELL",
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
               m_line.Push(StringFormat("✅ TP3 Hit 🎯 สัญญาณจบสมบูรณ์\n%s %s @ %s\nSignal ID: %I64d",
                                        m_symbol, isBuy ? "BUY" : "SELL",
                                        DoubleToString(m_signals[i].tp3, digits), m_signals[i].id));
               LogEvent(m_signals[i], "TP3_HIT", "completed");
               continue;
              }
           }

         //--- 3) invalidation: only while nothing has been hit yet
         if(m_signals[i].status == SIG_ACTIVE)
           {
            //--- structure flipped against the idea (CHoCH the other way)
            if(st.recentCHoCH && st.bias == -m_signals[i].dir)
              {
               m_signals[i].status = SIG_CANCELLED;
               m_line.Push(StringFormat("❌ Signal Cancelled\n%s %s\nเหตุผล: โครงสร้างตลาดเปลี่ยนทิศ (CHoCH สวนทาง)\nSignal ID: %I64d",
                                        m_symbol, isBuy ? "BUY" : "SELL", m_signals[i].id));
               LogEvent(m_signals[i], "CANCELLED", "structure flipped (CHoCH)");
               continue;
              }
            //--- expired without reaching TP1
            if(m_expiryHours > 0 &&
               TimeCurrent() - m_signals[i].time >= (long)m_expiryHours * 3600)
              {
               m_signals[i].status = SIG_CANCELLED;
               m_line.Push(StringFormat("❌ Signal Cancelled\n%s %s\nเหตุผล: เกินเวลา %d ชม. โดยไม่ถึง TP1\nSignal ID: %I64d",
                                        m_symbol, isBuy ? "BUY" : "SELL", m_expiryHours, m_signals[i].id));
               LogEvent(m_signals[i], "CANCELLED", "expired");
              }
           }
        }
     }
  };

#endif // CG_SIGNAL_MANAGER_MQH
