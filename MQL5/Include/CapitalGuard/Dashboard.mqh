//+------------------------------------------------------------------+
//|                                                  Dashboard.mqh   |
//|  CapitalGuard - On-chart dashboard                               |
//|                                                                  |
//|  Shows balance, equity, daily/weekly/monthly P/L, drawdown,      |
//|  win rate, open trades, last confidence score, news status and   |
//|  market regime via the chart Comment (lightweight & robust).     |
//+------------------------------------------------------------------+
#ifndef CG_DASHBOARD_MQH
#define CG_DASHBOARD_MQH

#include "RiskManager.mqh"
#include "Regime.mqh"
#include "ScoringEngine.mqh"

//+------------------------------------------------------------------+
//| Dashboard                                                        |
//+------------------------------------------------------------------+
class CDashboard
  {
private:
   string            m_symbol;
   long              m_magic;
   datetime          m_lastUpdate;
   int               m_updateSeconds;   // refresh throttle

   //--- realized P/L, wins and losses from history since `from`
   void              HistoryStats(const datetime from, double &profit, int &wins, int &losses)
     {
      profit = 0.0; wins = 0; losses = 0;
      if(!HistorySelect(from, TimeCurrent() + 60)) return;
      for(int i = 0; i < HistoryDealsTotal(); i++)
        {
         ulong t = HistoryDealGetTicket(i);
         if(t == 0) continue;
         if(HistoryDealGetInteger(t, DEAL_MAGIC) != m_magic) continue;
         if(HistoryDealGetString(t, DEAL_SYMBOL) != m_symbol) continue;
         if((ENUM_DEAL_ENTRY)HistoryDealGetInteger(t, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;
         double p = HistoryDealGetDouble(t, DEAL_PROFIT)
                  + HistoryDealGetDouble(t, DEAL_SWAP)
                  + HistoryDealGetDouble(t, DEAL_COMMISSION);
         profit += p;
         if(p >= 0.0) wins++; else losses++;
        }
     }

   //--- count open positions of this EA
   int               OpenTrades() const
     {
      int count = 0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         ulong ticket = PositionGetTicket(i);
         if(ticket == 0) continue;
         if(PositionGetString(POSITION_SYMBOL) != m_symbol) continue;
         if(PositionGetInteger(POSITION_MAGIC) != m_magic) continue;
         count++;
        }
      return(count);
     }

public:
   //--- configure
   void              Init(const string symbol, const long magic, const int updateSeconds)
     {
      m_symbol        = symbol;
      m_magic         = magic;
      m_updateSeconds = updateSeconds;
      m_lastUpdate    = 0;
     }

   //--- redraw the panel (throttled); pass live state from the EA
   void              Update(CRiskManager &risk, const SRegimeInfo &regime,
                            const SSignal &lastSignal, const string newsStatus,
                            const string tradingStatus, const string session)
     {
      if(TimeCurrent() - m_lastUpdate < m_updateSeconds) return;
      m_lastUpdate = TimeCurrent();

      double balance = AccountInfoDouble(ACCOUNT_BALANCE);
      double equity  = AccountInfoDouble(ACCOUNT_EQUITY);

      //--- monthly realized P/L
      MqlDateTime dt;
      TimeToStruct(TimeCurrent(), dt);
      dt.day = 1; dt.hour = 0; dt.min = 0; dt.sec = 0;
      datetime monthStart = StructToTime(dt);
      double monthProfit; int wins, losses;
      HistoryStats(monthStart, monthProfit, wins, losses);
      //--- all-time win rate (last 90 days window keeps it responsive)
      double allProfit; int aw, al;
      HistoryStats(TimeCurrent() - 90 * 86400, allProfit, aw, al);
      double winRate = (aw + al > 0) ? 100.0 * aw / (aw + al) : 0.0;

      string text = "\n";
      text += "========== CAPITAL GUARD EA ==========\n";
      text += StringFormat("  Balance: %.2f   Equity: %.2f\n", balance, equity);
      text += StringFormat("  Day P/L: %+.2f%%   Week P/L: %+.2f%%   Month: %+.2f USD\n",
                           risk.DayPLPercent(), risk.WeekPLPercent(), monthProfit);
      text += StringFormat("  Drawdown: %.2f%%   Risk scale: x%.2f\n",
                           risk.DrawdownPercent(), risk.RiskScale());
      text += StringFormat("  Daily target: %.1f%%  [%s]\n",
                           risk.DailyTargetPct(),
                           risk.DailyTargetReached() ? "REACHED" : "working");
      text += "--------------------------------------\n";
      text += StringFormat("  Win rate (90d): %.1f%%  (W%d / L%d)\n", winRate, aw, al);
      text += StringFormat("  Open trades: %d   Trades today: %d\n", OpenTrades(), risk.TradesToday());
      text += "--------------------------------------\n";
      text += StringFormat("  Regime: %s | %s (ADX %.1f, ATRx %.2f)\n",
                           CRegimeDetector::RegimeName(regime.regime),
                           CRegimeDetector::VolName(regime.vol),
                           regime.adx == EMPTY_VALUE ? 0.0 : regime.adx, regime.atrRatio);
      text += StringFormat("  Session: %s\n", session);
      text += StringFormat("  News: %s\n", newsStatus == "" ? "clear" : newsStatus);
      text += StringFormat("  Last signal: %s (score %.1f)\n",
                           lastSignal.direction == 0 ? "none" : (lastSignal.direction > 0 ? "BUY" : "SELL"),
                           lastSignal.total);
      text += StringFormat("  Status: %s\n", tradingStatus);
      text += "======================================\n";
      Comment(text);
     }

   //--- clear the panel on shutdown
   void              Clear() { Comment(""); }
  };

#endif // CG_DASHBOARD_MQH
