//+------------------------------------------------------------------+
//|                                                     Logger.mqh   |
//|  CapitalGuard - Trade logging (CSV + JSON Lines)                 |
//|                                                                  |
//|  Every order records: time, entry, SL, TP, lot, entry reason,    |
//|  indicators used, confidence score, and on close: result,        |
//|  realized RR, profit. Files are written to MQL5/Files/           |
//|  CapitalGuard/ and can be consumed by the Python ML pipeline.    |
//+------------------------------------------------------------------+
#ifndef CG_LOGGER_MQH
#define CG_LOGGER_MQH

#include "ScoringEngine.mqh"

//+------------------------------------------------------------------+
//| CSV + JSONL trade logger                                         |
//+------------------------------------------------------------------+
class CTradeLogger
  {
private:
   string            m_csvFile;
   string            m_jsonFile;
   long              m_magic;

   //--- append one line of text to a file, creating it when missing
   void              AppendLine(const string filename, const string line, const string header)
     {
      int fh = FileOpen(filename, FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_SHARE_READ);
      if(fh == INVALID_HANDLE)
        {
         PrintFormat("Logger: cannot open %s (err %d)", filename, GetLastError());
         return;
        }
      if(FileSize(fh) == 0 && header != "")
         FileWriteString(fh, header + "\n");
      FileSeek(fh, 0, SEEK_END);
      FileWriteString(fh, line + "\n");
      FileClose(fh);
     }

   //--- escape a string for embedding in CSV (quotes + commas)
   string            CsvEscape(const string s) const
     {
      string out = s;
      StringReplace(out, "\"", "'");
      StringReplace(out, ",", ";");
      StringReplace(out, "\n", " ");
      return(out);
     }

   //--- escape a string for embedding in JSON
   string            JsonEscape(const string s) const
     {
      string out = s;
      StringReplace(out, "\\", "\\\\");
      StringReplace(out, "\"", "\\\"");
      StringReplace(out, "\n", " ");
      return(out);
     }

public:
   //--- configure output files (relative to MQL5/Files)
   void              Init(const long magic)
     {
      m_magic    = magic;
      m_csvFile  = StringFormat("CapitalGuard\\trades_%I64d.csv", magic);
      m_jsonFile = StringFormat("CapitalGuard\\trades_%I64d.jsonl", magic);
     }

   //--- log a newly opened position with its full decision context
   void              LogOpen(const ulong ticket, const string symbol, const int direction,
                             const double entry, const double sl, const double tp,
                             const double lot, const SSignal &sig, const string regime,
                             const string session, const string sizingNote)
     {
      string ts  = TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS);
      double rr  = (MathAbs(entry - sl) > 0.0) ? MathAbs(tp - entry) / MathAbs(entry - sl) : 0.0;

      string csvHeader = "event,time,ticket,symbol,dir,entry,sl,tp,lot,planned_rr,score,trend,momentum,volume,structure,volatility,regime,session,reason,sizing,profit,realized_rr";
      string csv = StringFormat("OPEN,%s,%I64u,%s,%s,%.5f,%.5f,%.5f,%.2f,%.2f,%.1f,%.0f,%.0f,%.0f,%.0f,%.0f,%s,%s,%s,%s,,",
                                ts, ticket, symbol, direction > 0 ? "BUY" : "SELL",
                                entry, sl, tp, lot, rr, sig.total,
                                sig.trendScore, sig.momentumScore, sig.volumeScore,
                                sig.structureScore, sig.volatilityScore,
                                CsvEscape(regime), CsvEscape(session),
                                CsvEscape(sig.reason), CsvEscape(sizingNote));
      AppendLine(m_csvFile, csv, csvHeader);

      string json = StringFormat(
         "{\"event\":\"open\",\"time\":\"%s\",\"ticket\":%I64u,\"symbol\":\"%s\",\"dir\":\"%s\","
         "\"entry\":%.5f,\"sl\":%.5f,\"tp\":%.5f,\"lot\":%.2f,\"planned_rr\":%.2f,"
         "\"score\":%.1f,\"scores\":{\"trend\":%.0f,\"momentum\":%.0f,\"volume\":%.0f,"
         "\"structure\":%.0f,\"volatility\":%.0f},\"regime\":\"%s\",\"session\":\"%s\","
         "\"reason\":\"%s\",\"sizing\":\"%s\"}",
         ts, ticket, symbol, direction > 0 ? "BUY" : "SELL",
         entry, sl, tp, lot, rr, sig.total,
         sig.trendScore, sig.momentumScore, sig.volumeScore,
         sig.structureScore, sig.volatilityScore,
         JsonEscape(regime), JsonEscape(session),
         JsonEscape(sig.reason), JsonEscape(sizingNote));
      AppendLine(m_jsonFile, json, "");
     }

   //--- log a close deal (full or partial) with realized outcome
   void              LogClose(const ulong dealTicket)
     {
      if(!HistoryDealSelect(dealTicket)) return;
      if(HistoryDealGetInteger(dealTicket, DEAL_MAGIC) != m_magic) return;
      if((ENUM_DEAL_ENTRY)HistoryDealGetInteger(dealTicket, DEAL_ENTRY) != DEAL_ENTRY_OUT) return;

      string symbol   = HistoryDealGetString(dealTicket, DEAL_SYMBOL);
      double profit   = HistoryDealGetDouble(dealTicket, DEAL_PROFIT)
                      + HistoryDealGetDouble(dealTicket, DEAL_SWAP)
                      + HistoryDealGetDouble(dealTicket, DEAL_COMMISSION);
      double volume   = HistoryDealGetDouble(dealTicket, DEAL_VOLUME);
      double price    = HistoryDealGetDouble(dealTicket, DEAL_PRICE);
      ulong  posId    = HistoryDealGetInteger(dealTicket, DEAL_POSITION_ID);
      string ts       = TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS);

      //--- realized RR: profit relative to the money risked at entry.
      //--- Find the entry deal of this position for entry price and volume.
      double entryPrice = 0.0, entrySL = 0.0;
      if(HistorySelectByPosition(posId))
        {
         for(int i = 0; i < HistoryDealsTotal(); i++)
           {
            ulong t = HistoryDealGetTicket(i);
            if(t == 0) continue;
            if((ENUM_DEAL_ENTRY)HistoryDealGetInteger(t, DEAL_ENTRY) == DEAL_ENTRY_IN)
              {
               entryPrice = HistoryDealGetDouble(t, DEAL_PRICE);
               break;
              }
           }
        }
      //--- realized distance in price, expressed vs entry
      double realizedRR = 0.0;
      if(entryPrice > 0.0)
        {
         //--- use stored R distance when available
         string keyR = StringFormat("CG_%I64d_R_%I64u", m_magic, posId);
         double rDist = GlobalVariableCheck(keyR) ? GlobalVariableGet(keyR) : 0.0;
         if(rDist > 0.0)
            realizedRR = MathAbs(price - entryPrice) / rDist * (profit >= 0.0 ? 1.0 : -1.0);
        }

      string csv = StringFormat("CLOSE,%s,%I64u,%s,,%.5f,,,%.2f,,,,,,,,,,,,%.2f,%.2f",
                                ts, posId, symbol, price, volume, profit, realizedRR);
      AppendLine(m_csvFile, csv, "");

      string json = StringFormat(
         "{\"event\":\"close\",\"time\":\"%s\",\"position\":%I64u,\"symbol\":\"%s\","
         "\"price\":%.5f,\"volume\":%.2f,\"profit\":%.2f,\"realized_rr\":%.2f}",
         ts, posId, symbol, price, volume, profit, realizedRR);
      AppendLine(m_jsonFile, json, "");
     }

   //--- log a skipped setup so filters can be audited later
   void              LogSkip(const string stage, const string reason)
     {
      string ts = TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS);
      string json = StringFormat("{\"event\":\"skip\",\"time\":\"%s\",\"stage\":\"%s\",\"reason\":\"%s\"}",
                                 ts, JsonEscape(stage), JsonEscape(reason));
      AppendLine(m_jsonFile, json, "");
     }
  };

#endif // CG_LOGGER_MQH
