//+------------------------------------------------------------------+
//|                                                RiskManager.mqh   |
//|  CapitalGuard - Risk management module (Capital Preservation)    |
//|                                                                  |
//|  Responsibilities:                                               |
//|   - Dynamic position sizing from risk % and SL distance          |
//|   - Daily / weekly loss limits, max drawdown circuit breaker     |
//|   - Auto risk reduction while in drawdown                        |
//|   - Trades-per-day limit, daily profit target tracking           |
//+------------------------------------------------------------------+
#ifndef CG_RISK_MANAGER_MQH
#define CG_RISK_MANAGER_MQH

//--- policy when risk-based lot is below broker minimum lot
enum ENUM_MINLOT_POLICY
  {
   MINLOT_SKIP,          // Skip trade (strict risk control)
   MINLOT_USE_IF_CAPPED  // Use min lot if loss <= hard cap %
  };

//+------------------------------------------------------------------+
//| Risk manager class                                               |
//+------------------------------------------------------------------+
class CRiskManager
  {
private:
   string            m_symbol;
   long              m_magic;
   //--- limits (all in percent)
   double            m_riskPerTrade;      // base risk per trade
   double            m_maxDailyLoss;      // stop trading for the day
   double            m_maxWeeklyLoss;     // stop trading for the week
   double            m_maxDrawdown;       // hard circuit breaker
   double            m_hardRiskCap;       // absolute max loss allowed on one trade
   int               m_maxTradesPerDay;
   double            m_dailyTargetPct;    // daily profit target
   ENUM_MINLOT_POLICY m_minLotPolicy;
   //--- rollover state
   datetime          m_curDay;
   datetime          m_curWeek;
   double            m_dayStartBalance;
   double            m_weekStartBalance;

   //--- terminal global-variable name (persists across restarts)
   string            GV(const string suffix) const
     {
      return(StringFormat("CG_%I64d_%s_%s", m_magic, m_symbol, suffix));
     }

public:
   //--- configure the module; call once from OnInit
   void              Init(const string symbol, const long magic,
                          const double riskPerTrade, const double maxDailyLoss,
                          const double maxWeeklyLoss, const double maxDrawdown,
                          const int maxTradesPerDay, const double dailyTargetPct,
                          const ENUM_MINLOT_POLICY minLotPolicy, const double hardRiskCap)
     {
      m_symbol          = symbol;
      m_magic           = magic;
      m_riskPerTrade    = riskPerTrade;
      m_maxDailyLoss    = maxDailyLoss;
      m_maxWeeklyLoss   = maxWeeklyLoss;
      m_maxDrawdown     = maxDrawdown;
      m_maxTradesPerDay = maxTradesPerDay;
      m_dailyTargetPct  = dailyTargetPct;
      m_minLotPolicy    = minLotPolicy;
      m_hardRiskCap     = hardRiskCap;
      m_curDay          = 0;
      m_curWeek         = 0;
      m_dayStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
      m_weekStartBalance= AccountInfoDouble(ACCOUNT_BALANCE);
      UpdateRollover();
     }

   //--- detect new trading day / week and snapshot starting balances
   void              UpdateRollover()
     {
      datetime day  = iTime(m_symbol, PERIOD_D1, 0);
      datetime week = iTime(m_symbol, PERIOD_W1, 0);
      if(day != m_curDay && day > 0)
        {
         m_curDay = day;
         m_dayStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
        }
      if(week != m_curWeek && week > 0)
        {
         m_curWeek = week;
         m_weekStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
        }
      //--- track all-time peak equity for drawdown measurement
      double eq = AccountInfoDouble(ACCOUNT_EQUITY);
      if(!GlobalVariableCheck(GV("PEAK")) || GlobalVariableGet(GV("PEAK")) < eq)
         GlobalVariableSet(GV("PEAK"), eq);
     }

   //--- day profit/loss in percent of day-start balance (incl. floating)
   double            DayPLPercent() const
     {
      if(m_dayStartBalance <= 0.0) return(0.0);
      return((AccountInfoDouble(ACCOUNT_EQUITY) - m_dayStartBalance) / m_dayStartBalance * 100.0);
     }

   //--- week profit/loss in percent of week-start balance
   double            WeekPLPercent() const
     {
      if(m_weekStartBalance <= 0.0) return(0.0);
      return((AccountInfoDouble(ACCOUNT_EQUITY) - m_weekStartBalance) / m_weekStartBalance * 100.0);
     }

   //--- drawdown from peak equity in percent
   double            DrawdownPercent() const
     {
      double peak = GlobalVariableCheck(GV("PEAK")) ? GlobalVariableGet(GV("PEAK")) : AccountInfoDouble(ACCOUNT_EQUITY);
      if(peak <= 0.0) return(0.0);
      return((peak - AccountInfoDouble(ACCOUNT_EQUITY)) / peak * 100.0);
     }

   //--- number of entry deals executed today with our magic number
   int               TradesToday() const
     {
      int count = 0;
      if(!HistorySelect(m_curDay, TimeCurrent() + 60))
         return(0);
      for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
        {
         ulong ticket = HistoryDealGetTicket(i);
         if(ticket == 0) continue;
         if(HistoryDealGetInteger(ticket, DEAL_MAGIC) != m_magic) continue;
         if(HistoryDealGetString(ticket, DEAL_SYMBOL) != m_symbol) continue;
         if((ENUM_DEAL_ENTRY)HistoryDealGetInteger(ticket, DEAL_ENTRY) == DEAL_ENTRY_IN)
            count++;
        }
      return(count);
     }

   //--- true when today's profit already reached the daily target
   bool              DailyTargetReached() const
     {
      return(m_dailyTargetPct > 0.0 && DayPLPercent() >= m_dailyTargetPct);
     }

   //--- risk multiplier that shrinks automatically while in drawdown
   double            RiskScale() const
     {
      double dd = DrawdownPercent();
      if(dd >= m_maxDrawdown)      return(0.0);   // circuit breaker
      if(dd >= m_maxDrawdown*0.66) return(0.50);  // deep drawdown: half risk
      if(dd >= m_maxDrawdown*0.33) return(0.75);  // mild drawdown: reduce risk
      return(1.0);
     }

   //--- master gate: can we open a new trade right now?
   bool              TradingAllowed(string &reason)
     {
      UpdateRollover();
      if(-DayPLPercent() >= m_maxDailyLoss)
        { reason = StringFormat("Daily loss limit hit (%.2f%%)", -DayPLPercent()); return(false); }
      if(-WeekPLPercent() >= m_maxWeeklyLoss)
        { reason = StringFormat("Weekly loss limit hit (%.2f%%)", -WeekPLPercent()); return(false); }
      if(DrawdownPercent() >= m_maxDrawdown)
        { reason = StringFormat("Max drawdown hit (%.2f%%)", DrawdownPercent()); return(false); }
      if(m_maxTradesPerDay > 0 && TradesToday() >= m_maxTradesPerDay)
        { reason = "Max trades per day reached"; return(false); }
      reason = "";
      return(true);
     }

   //--- normalize a raw lot value to broker min/max/step
   double            NormalizeLot(double lots) const
     {
      double minLot  = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MIN);
      double maxLot  = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MAX);
      double lotStep = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_STEP);
      if(lotStep > 0.0)
         lots = MathFloor(lots / lotStep) * lotStep;
      lots = MathMax(minLot, MathMin(maxLot, lots));
      return(NormalizeDouble(lots, 2));
     }

   //--- money lost per 1.0 lot if price moves slDistance against us
   double            LossPerLot(const double slDistance) const
     {
      double tickValue = SymbolInfoDouble(m_symbol, SYMBOL_TRADE_TICK_VALUE);
      double tickSize  = SymbolInfoDouble(m_symbol, SYMBOL_TRADE_TICK_SIZE);
      if(tickValue <= 0.0 || tickSize <= 0.0 || slDistance <= 0.0)
         return(0.0);
      return(slDistance / tickSize * tickValue);
     }

   //--- dynamic position size; returns 0.0 when trade must be skipped
   //--- outReason explains the sizing decision (for the trade log)
   double            CalcLot(const double slDistance, string &outReason)
     {
      double balance    = AccountInfoDouble(ACCOUNT_BALANCE);
      double scale      = RiskScale();
      double riskPct    = m_riskPerTrade * scale;
      if(scale <= 0.0)
        { outReason = "Risk scale 0 (drawdown circuit breaker)"; return(0.0); }
      double riskMoney  = balance * riskPct / 100.0;
      double lossPerLot = LossPerLot(slDistance);
      if(lossPerLot <= 0.0)
        { outReason = "Cannot compute loss per lot"; return(0.0); }

      double rawLot = riskMoney / lossPerLot;
      double minLot = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MIN);

      //--- micro-account case: risk-based lot is below broker minimum
      if(rawLot < minLot)
        {
         double minLotLossPct = minLot * lossPerLot / balance * 100.0;
         if(m_minLotPolicy == MINLOT_SKIP)
           {
            outReason = StringFormat("Skipped: min lot risks %.2f%% > target %.2f%%", minLotLossPct, riskPct);
            return(0.0);
           }
         if(minLotLossPct > m_hardRiskCap)
           {
            outReason = StringFormat("Skipped: min lot risks %.2f%% > hard cap %.2f%%", minLotLossPct, m_hardRiskCap);
            return(0.0);
           }
         outReason = StringFormat("Min lot used, actual risk %.2f%% (cap %.2f%%)", minLotLossPct, m_hardRiskCap);
         return(NormalizeLot(minLot));
        }

      outReason = StringFormat("Risk %.2f%% (scale %.2f) = %.2f USD", riskPct, scale, riskMoney);
      return(NormalizeLot(rawLot));
     }

   //--- verify free margin is sufficient for the order
   bool              MarginOK(const ENUM_ORDER_TYPE type, const double lots, const double price) const
     {
      double margin = 0.0;
      if(!OrderCalcMargin(type, m_symbol, lots, price, margin))
         return(false);
      return(margin < AccountInfoDouble(ACCOUNT_MARGIN_FREE) * 0.5);
     }

   //--- accessors used by dashboard / main EA
   double            DayStartBalance()  const { return(m_dayStartBalance); }
   double            WeekStartBalance() const { return(m_weekStartBalance); }
   double            DailyTargetPct()   const { return(m_dailyTargetPct); }
  };

#endif // CG_RISK_MANAGER_MQH
