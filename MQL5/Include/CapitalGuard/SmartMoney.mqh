//+------------------------------------------------------------------+
//|                                                 SmartMoney.mqh   |
//|  CapitalGuard - Smart Money Concepts detection                   |
//|                                                                  |
//|  Simplified, rule-based detectors for:                           |
//|   - Liquidity Sweep : wick through a swing level, close back in  |
//|   - Order Block     : last opposite candle before an impulse,    |
//|                       currently being retested                   |
//|   - Fair Value Gap  : 3-candle imbalance still unfilled          |
//|                                                                  |
//|  These are approximations of discretionary SMC concepts; each    |
//|  detector is intentionally conservative and fully commented so   |
//|  the rules can be tightened or relaxed later.                    |
//+------------------------------------------------------------------+
#ifndef CG_SMART_MONEY_MQH
#define CG_SMART_MONEY_MQH

#include "MarketStructure.mqh"

//--- one scan result; "bull" flags support longs, "bear" support shorts
struct SSmartMoney
  {
   bool              sweepBull;     // liquidity grabbed below a low, reclaimed
   bool              sweepBear;     // liquidity grabbed above a high, rejected
   bool              obBull;        // price retesting a bullish order block
   bool              obBear;        // price retesting a bearish order block
   bool              fvgBull;       // bullish FVG below/at price, not invalidated
   bool              fvgBear;       // bearish FVG above/at price, not invalidated
  };

//+------------------------------------------------------------------+
//| Smart money scanner                                              |
//+------------------------------------------------------------------+
class CSmartMoney
  {
private:
   string            m_symbol;
   ENUM_TIMEFRAMES   m_tf;
   int               m_window;        // how many recent bars each pattern may span

public:
   //--- configure the scanner
   void              Init(const string symbol, const ENUM_TIMEFRAMES tf, const int window)
     {
      m_symbol = symbol;
      m_tf     = tf;
      m_window = window;
     }

   //--- run all detectors; `st` supplies swing levels, `atr` scales zones
   bool              Scan(const SStructureInfo &st, const double atr, SSmartMoney &out)
     {
      out.sweepBull = false; out.sweepBear = false;
      out.obBull    = false; out.obBear    = false;
      out.fvgBull   = false; out.fvgBear   = false;
      if(atr <= 0.0) return(false);

      int need = m_window + 5;
      MqlRates r[];
      if(CopyRates(m_symbol, m_tf, 0, need, r) < need) return(false);
      int last = ArraySize(r) - 2;          // last CLOSED bar (n-1 is forming)
      int from = MathMax(2, last - m_window);
      double close = r[last].close;

      //--- 1) Liquidity Sweep -------------------------------------------
      //--- bull: a recent bar wicked BELOW a known swing low but closed
      //--- back above it (stop-hunt of longs, then reclaim)
      for(int i = from; i <= last && !out.sweepBull; i++)
        {
         if(st.lastSwingLow > 0.0 && r[i].low < st.lastSwingLow && r[i].close > st.lastSwingLow)
            out.sweepBull = true;
         if(st.prevSwingLow > 0.0 && r[i].low < st.prevSwingLow && r[i].close > st.prevSwingLow)
            out.sweepBull = true;
        }
      //--- bear: wick ABOVE a swing high, close back below (buy-stop grab)
      for(int i = from; i <= last && !out.sweepBear; i++)
        {
         if(st.lastSwingHigh > 0.0 && r[i].high > st.lastSwingHigh && r[i].close < st.lastSwingHigh)
            out.sweepBear = true;
         if(st.prevSwingHigh > 0.0 && r[i].high > st.prevSwingHigh && r[i].close < st.prevSwingHigh)
            out.sweepBear = true;
        }

      //--- 2) Order Block ----------------------------------------------
      //--- bull OB: the last bearish candle immediately before an
      //--- impulsive up-move (next 2 closes clear its high by 0.5 ATR);
      //--- valid while price is retesting the zone [low, high + 0.3 ATR]
      for(int i = last - 2; i >= from && !out.obBull; i--)
        {
         bool bearishCandle = (r[i].close < r[i].open);
         if(!bearishCandle) continue;
         bool impulseUp = (r[i + 2].close > r[i].high + 0.5 * atr);
         if(!impulseUp) continue;
         double zoneLo = r[i].low;
         double zoneHi = r[i].high + 0.3 * atr;
         if(close >= zoneLo && close <= zoneHi)
            out.obBull = true;
        }
      //--- bear OB: mirror image
      for(int i = last - 2; i >= from && !out.obBear; i--)
        {
         bool bullishCandle = (r[i].close > r[i].open);
         if(!bullishCandle) continue;
         bool impulseDown = (r[i + 2].close < r[i].low - 0.5 * atr);
         if(!impulseDown) continue;
         double zoneHi = r[i].high;
         double zoneLo = r[i].low - 0.3 * atr;
         if(close <= zoneHi && close >= zoneLo)
            out.obBear = true;
        }

      //--- 3) Fair Value Gap -------------------------------------------
      //--- bull FVG: low of bar i above high of bar i-2 (imbalance);
      //--- still valid while price has not closed below the gap bottom
      for(int i = from; i <= last && !out.fvgBull; i++)
        {
         double gapBottom = r[i - 2].high;
         double gapTop    = r[i].low;
         if(gapTop <= gapBottom) continue;                  // no gap
         if(gapTop - gapBottom < 0.2 * atr) continue;       // ignore noise gaps
         bool invalidated = false;
         for(int j = i + 1; j <= last; j++)
            if(r[j].close < gapBottom) { invalidated = true; break; }
         if(!invalidated && close > gapBottom)
            out.fvgBull = true;
        }
      //--- bear FVG: mirror image
      for(int i = from; i <= last && !out.fvgBear; i++)
        {
         double gapTop    = r[i - 2].low;
         double gapBottom = r[i].high;
         if(gapTop <= gapBottom) continue;
         if(gapTop - gapBottom < 0.2 * atr) continue;
         bool invalidated = false;
         for(int j = i + 1; j <= last; j++)
            if(r[j].close > gapTop) { invalidated = true; break; }
         if(!invalidated && close < gapTop)
            out.fvgBear = true;
        }

      return(true);
     }
  };

#endif // CG_SMART_MONEY_MQH
