//+------------------------------------------------------------------+
//|                                               TradeManager.mqh   |
//|  CapitalGuard - Open position management                         |
//|                                                                  |
//|  - Break-even move once profit reaches 1R                        |
//|  - Optional partial close at 1R                                  |
//|  - ATR trailing stop after break-even                            |
//|  - Time exit for stale positions going nowhere                   |
//|                                                                  |
//|  The initial risk distance (R) of each position is stored in a   |
//|  terminal global variable keyed by ticket, so management         |
//|  survives EA restarts.                                           |
//+------------------------------------------------------------------+
#ifndef CG_TRADE_MANAGER_MQH
#define CG_TRADE_MANAGER_MQH

#include <Trade\Trade.mqh>
#include "IndicatorSet.mqh"
#include "MarketStructure.mqh"

//+------------------------------------------------------------------+
//| Trade manager                                                    |
//+------------------------------------------------------------------+
class CTradeManager
  {
private:
   CTrade           *m_trade;
   string            m_symbol;
   long              m_magic;
   //--- settings
   bool              m_useBreakEven;
   double            m_beTriggerR;      // move SL to BE at this many R
   int               m_beLockPoints;    // lock-in offset beyond entry
   bool              m_usePartial;
   double            m_partialR;        // take partial at this many R
   double            m_partialPct;      // fraction of volume to close (0-1)
   bool              m_useAtrTrail;
   double            m_atrTrailMult;    // trail distance = ATR * mult
   int               m_maxHoldHours;    // 0 = no time exit
   double            m_timeExitMinR;    // close if below this R when time is up
   bool              m_useEmergency;    // close when structure flips against us

   //--- global-variable keys per ticket
   string            KeyR(const ulong ticket)  const { return(StringFormat("CG_%I64d_R_%I64u", m_magic, ticket)); }
   string            KeyPC(const ulong ticket) const { return(StringFormat("CG_%I64d_PC_%I64u", m_magic, ticket)); }

   //--- broker minimum SL distance from current price
   double            MinStopDistance() const
     {
      long stopsLevel = SymbolInfoInteger(m_symbol, SYMBOL_TRADE_STOPS_LEVEL);
      return((stopsLevel + 1) * SymbolInfoDouble(m_symbol, SYMBOL_POINT));
     }

public:
   //--- configure; `trade` is the shared CTrade instance of the EA
   void              Init(CTrade *trade, const string symbol, const long magic,
                          const bool useBreakEven, const double beTriggerR, const int beLockPoints,
                          const bool usePartial, const double partialR, const double partialPct,
                          const bool useAtrTrail, const double atrTrailMult,
                          const int maxHoldHours, const double timeExitMinR,
                          const bool useEmergency)
     {
      m_trade        = trade;
      m_symbol       = symbol;
      m_magic        = magic;
      m_useBreakEven = useBreakEven;
      m_beTriggerR   = beTriggerR;
      m_beLockPoints = beLockPoints;
      m_usePartial   = usePartial;
      m_partialR     = partialR;
      m_partialPct   = partialPct;
      m_useAtrTrail  = useAtrTrail;
      m_atrTrailMult = atrTrailMult;
      m_maxHoldHours = maxHoldHours;
      m_timeExitMinR = timeExitMinR;
      m_useEmergency = useEmergency;
     }

   //--- remember initial risk distance of a freshly opened position
   void              RegisterPosition(const ulong ticket, const double riskDistance)
     {
      GlobalVariableSet(KeyR(ticket), riskDistance);
     }

   //--- drop per-ticket state after the position is fully closed
   void              ForgetPosition(const ulong ticket)
     {
      if(GlobalVariableCheck(KeyR(ticket)))  GlobalVariableDel(KeyR(ticket));
      if(GlobalVariableCheck(KeyPC(ticket))) GlobalVariableDel(KeyPC(ticket));
     }

   //--- manage all open positions of this EA; call every tick.
   //--- `st` is the latest market-structure snapshot (for emergency exit).
   void              Manage(CIndicatorSet &entryInd, const SStructureInfo &st)
     {
      int digits = (int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS);
      double point = SymbolInfoDouble(m_symbol, SYMBOL_POINT);

      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         ulong ticket = PositionGetTicket(i);
         if(ticket == 0) continue;
         if(PositionGetString(POSITION_SYMBOL) != m_symbol) continue;
         if(PositionGetInteger(POSITION_MAGIC) != m_magic) continue;

         long   type      = PositionGetInteger(POSITION_TYPE);
         double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
         double curSL     = PositionGetDouble(POSITION_SL);
         double curTP     = PositionGetDouble(POSITION_TP);
         double volume    = PositionGetDouble(POSITION_VOLUME);
         datetime opened  = (datetime)PositionGetInteger(POSITION_TIME);

         //--- recover R; fall back to |entry - SL| when GV was lost
         double rDist = GlobalVariableCheck(KeyR(ticket)) ? GlobalVariableGet(KeyR(ticket)) : 0.0;
         if(rDist <= 0.0 && curSL > 0.0)
            rDist = MathAbs(openPrice - curSL);
         if(rDist <= 0.0) continue;

         double bid = SymbolInfoDouble(m_symbol, SYMBOL_BID);
         double ask = SymbolInfoDouble(m_symbol, SYMBOL_ASK);
         bool   isBuy = (type == POSITION_TYPE_BUY);
         double refPrice = isBuy ? bid : ask;

         //--- current profit expressed in R multiples
         double profitDist = isBuy ? (bid - openPrice) : (openPrice - ask);
         double rNow = profitDist / rDist;

         //--- 0) emergency exit: a confirmed Change of Character against
         //--- our direction means the structure the trade was built on is
         //--- gone - exit immediately instead of waiting for the SL
         if(m_useEmergency && st.recentCHoCH && st.bias != 0)
           {
            int posDir = isBuy ? 1 : -1;
            if(st.bias == -posDir)
              {
               if(m_trade.PositionClose(ticket))
                 {
                  PrintFormat("Emergency exit #%I64u: structure flipped against position", ticket);
                  continue;
                 }
              }
           }

         //--- 1) time exit: stale position without progress
         if(m_maxHoldHours > 0 &&
            TimeCurrent() - opened >= (long)m_maxHoldHours * 3600 &&
            rNow < m_timeExitMinR)
           {
            if(m_trade.PositionClose(ticket))
               continue;   // position gone, nothing more to manage
           }

         //--- 2) partial close at partialR (once per position)
         if(m_usePartial && rNow >= m_partialR && !GlobalVariableCheck(KeyPC(ticket)))
           {
            double minLot  = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_MIN);
            double lotStep = SymbolInfoDouble(m_symbol, SYMBOL_VOLUME_STEP);
            double closeVol = volume * m_partialPct;
            if(lotStep > 0.0)
               closeVol = MathFloor(closeVol / lotStep) * lotStep;
            //--- only if both the closed part and remainder stay >= min lot
            if(closeVol >= minLot && volume - closeVol >= minLot)
              {
               if(m_trade.PositionClosePartial(ticket, closeVol))
                  GlobalVariableSet(KeyPC(ticket), 1.0);
              }
            else
               GlobalVariableSet(KeyPC(ticket), 0.0);   // mark as evaluated
           }

         //--- 3) break-even at beTriggerR
         double newSL = curSL;
         if(m_useBreakEven && rNow >= m_beTriggerR)
           {
            double be = isBuy ? openPrice + m_beLockPoints * point
                              : openPrice - m_beLockPoints * point;
            be = NormalizeDouble(be, digits);
            if(isBuy  && (curSL == 0.0 || curSL < be)) newSL = be;
            if(!isBuy && (curSL == 0.0 || curSL > be)) newSL = be;
           }

         //--- 4) ATR trailing stop, only after break-even secured
         if(m_useAtrTrail && rNow >= m_beTriggerR)
           {
            double atr = entryInd.Atr(1);
            if(atr != EMPTY_VALUE && atr > 0.0)
              {
               double trail = isBuy ? NormalizeDouble(bid - atr * m_atrTrailMult, digits)
                                    : NormalizeDouble(ask + atr * m_atrTrailMult, digits);
               if(isBuy  && trail > newSL) newSL = trail;
               if(!isBuy && (newSL == 0.0 || trail < newSL)) newSL = trail;
              }
           }

         //--- apply SL change if it improves and respects the stops level
         bool improved = isBuy ? (newSL > curSL)
                               : (curSL == 0.0 ? newSL > 0.0 : newSL < curSL);
         if(improved)
           {
            double minDist = MinStopDistance();
            bool valid = isBuy ? (refPrice - newSL >= minDist)
                               : (newSL - refPrice >= minDist);
            if(valid)
               m_trade.PositionModify(ticket, newSL, curTP);
           }
        }
     }
  };

#endif // CG_TRADE_MANAGER_MQH
