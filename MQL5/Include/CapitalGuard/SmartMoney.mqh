//+------------------------------------------------------------------+
//|                                                 SmartMoney.mqh   |
//|  CapitalGuard v3 - Smart Money Concepts analysis (core engine)   |
//|                                                                  |
//|  SMC is the PRIMARY decision system of the bot. This module      |
//|  detects, per scan:                                              |
//|   - Liquidity      : equal highs/lows (BSL/SSL pools),           |
//|                      liquidity grab / sweep                      |
//|   - Order Blocks   : quality-scored zones (freshness, touches,   |
//|                      volume, structure position, liquidity link) |
//|   - Fair Value Gaps: existence + mitigation state                |
//|   - Premium/Discount: price position inside the dealing range    |
//|                                                                  |
//|  Detectors are rule-based approximations of discretionary SMC;   |
//|  every rule is commented so it can be tightened or relaxed.      |
//+------------------------------------------------------------------+
#ifndef CG_SMART_MONEY_MQH
#define CG_SMART_MONEY_MQH

#include "MarketStructure.mqh"

//--- one quality-scored order block zone
struct SOrderBlock
  {
   bool              valid;         // a usable zone was found
   double            top;           // zone upper price
   double            bottom;        // zone lower price
   double            quality;       // 0-100 quality score
   int               barsAgo;       // bars since the zone formed (freshness)
   int               touches;       // times price tapped the zone after forming
   bool              mitigating;    // price is inside the zone right now
  };

//--- full SMC snapshot for one timeframe
struct SSmcAnalysis
  {
   //--- liquidity map
   bool              equalHighs;       // equal-high cluster = buy-side liquidity pool
   bool              equalLows;        // equal-low cluster  = sell-side liquidity pool
   bool              bslAbovePrice;    // untapped buy-side liquidity above price
   bool              sslBelowPrice;    // untapped sell-side liquidity below price
   bool              sweepBull;        // sell-side liquidity grabbed, reclaimed (fuel for longs)
   bool              sweepBear;        // buy-side liquidity grabbed, rejected (fuel for shorts)
   //--- order blocks
   SOrderBlock       obBull;           // bullish (demand) order block
   SOrderBlock       obBear;           // bearish (supply) order block
   //--- fair value gaps
   bool              fvgBull;          // bullish FVG exists, not invalidated
   bool              fvgBear;          // bearish FVG exists, not invalidated
   bool              fvgBullMitigated; // price has returned into the bullish gap
   bool              fvgBearMitigated; // price has returned into the bearish gap
   //--- premium / discount
   double            rangePos;         // 0 = range low ... 1 = range high (0.5 = EQ)
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

   //--- true when bar `i` is a local high over +/- 2 neighbours
   bool              IsLocalHigh(const MqlRates &r[], const int i) const
     {
      return(r[i].high > r[i-1].high && r[i].high > r[i-2].high &&
             r[i].high > r[i+1].high && r[i].high > r[i+2].high);
     }

   //--- true when bar `i` is a local low over +/- 2 neighbours
   bool              IsLocalLow(const MqlRates &r[], const int i) const
     {
      return(r[i].low < r[i-1].low && r[i].low < r[i-2].low &&
             r[i].low < r[i+1].low && r[i].low < r[i+2].low);
     }

public:
   //--- configure the scanner
   void              Init(const string symbol, const ENUM_TIMEFRAMES tf, const int window)
     {
      m_symbol = symbol;
      m_tf     = tf;
      m_window = window;
     }

   //--- run all detectors; `st` supplies the dealing range and swing
   //--- levels, `atr` scales all zone tolerances
   bool              Scan(const SStructureInfo &st, const double atr, SSmcAnalysis &out)
     {
      //--- reset the snapshot
      out.equalHighs = false;      out.equalLows = false;
      out.bslAbovePrice = false;   out.sslBelowPrice = false;
      out.sweepBull = false;       out.sweepBear = false;
      out.obBull.valid = false;    out.obBull.quality = 0.0;
      out.obBull.mitigating = false; out.obBull.touches = 0; out.obBull.barsAgo = 0;
      out.obBull.top = 0.0;        out.obBull.bottom = 0.0;
      out.obBear.valid = false;    out.obBear.quality = 0.0;
      out.obBear.mitigating = false; out.obBear.touches = 0; out.obBear.barsAgo = 0;
      out.obBear.top = 0.0;        out.obBear.bottom = 0.0;
      out.fvgBull = false;         out.fvgBear = false;
      out.fvgBullMitigated = false; out.fvgBearMitigated = false;
      out.rangePos = 0.5;
      if(atr <= 0.0) return(false);

      int need = m_window + 8;
      MqlRates r[];
      if(CopyRates(m_symbol, m_tf, 0, need, r) < need) return(false);
      int last = ArraySize(r) - 2;          // last CLOSED bar (n-1 is forming)
      int from = MathMax(3, last - m_window);
      double close = r[last].close;

      //--- average volume of the window (for order block scoring)
      double avgVol = 0.0;
      for(int i = from; i <= last; i++) avgVol += (double)r[i].tick_volume;
      avgVol /= MathMax(1, last - from + 1);

      //--- 0) Premium / Discount ---------------------------------------
      //--- dealing range = last confirmed swing low .. swing high;
      //--- 0.5 is equilibrium: buy only below (discount), sell above
      if(st.lastSwingHigh > st.lastSwingLow && st.lastSwingLow > 0.0)
        {
         double pos = (close - st.lastSwingLow) / (st.lastSwingHigh - st.lastSwingLow);
         out.rangePos = MathMax(0.0, MathMin(1.0, pos));
        }

      //--- 1) Liquidity pools: equal highs / equal lows ----------------
      //--- two local extremes within 0.15 ATR of each other form a pool
      //--- of resting stops (institutional target)
      double tol = 0.15 * atr;
      for(int i = from; i <= last - 2 && !out.equalHighs; i++)
        {
         if(!IsLocalHigh(r, i)) continue;
         for(int j = i + 2; j <= last - 2; j++)
           {
            if(!IsLocalHigh(r, j)) continue;
            if(MathAbs(r[i].high - r[j].high) <= tol)
              {
               out.equalHighs = true;
               if(MathMax(r[i].high, r[j].high) > close) out.bslAbovePrice = true;
               break;
              }
           }
        }
      for(int i = from; i <= last - 2 && !out.equalLows; i++)
        {
         if(!IsLocalLow(r, i)) continue;
         for(int j = i + 2; j <= last - 2; j++)
           {
            if(!IsLocalLow(r, j)) continue;
            if(MathAbs(r[i].low - r[j].low) <= tol)
              {
               out.equalLows = true;
               if(MathMin(r[i].low, r[j].low) < close) out.sslBelowPrice = true;
               break;
              }
           }
        }

      //--- 2) Liquidity Sweep ------------------------------------------
      //--- bull: a recent bar wicked BELOW a known swing low but closed
      //--- back above it (sell-side liquidity grabbed, then reclaimed);
      //--- remember the sweep bar to link it to order blocks later
      int sweepBullBar = -1, sweepBearBar = -1;
      for(int i = from; i <= last; i++)
        {
         if(!out.sweepBull)
           {
            if((st.lastSwingLow > 0.0 && r[i].low < st.lastSwingLow && r[i].close > st.lastSwingLow) ||
               (st.prevSwingLow > 0.0 && r[i].low < st.prevSwingLow && r[i].close > st.prevSwingLow))
              { out.sweepBull = true; sweepBullBar = i; }
           }
         if(!out.sweepBear)
           {
            if((st.lastSwingHigh > 0.0 && r[i].high > st.lastSwingHigh && r[i].close < st.lastSwingHigh) ||
               (st.prevSwingHigh > 0.0 && r[i].high > st.prevSwingHigh && r[i].close < st.prevSwingHigh))
              { out.sweepBear = true; sweepBearBar = i; }
           }
        }

      //--- 3) Order Blocks with quality scoring ------------------------
      //--- bull OB: the last bearish candle before an impulsive up-move
      //--- (a close within 3 bars clears its high by 0.5 ATR).
      //--- Quality (0-100) rewards: freshness, few touches, high volume,
      //--- discount location, and formation right after a sweep.
      for(int i = last - 3; i >= from; i--)
        {
         if(r[i].close >= r[i].open) continue;              // need bearish candle
         bool impulse = false;
         for(int k = 1; k <= 3 && i + k <= last; k++)
            if(r[i + k].close > r[i].high + 0.5 * atr) { impulse = true; break; }
         if(!impulse) continue;

         double top = r[i].high, bottom = r[i].low;
         //--- invalidated when price later closed below the zone
         bool invalid = false;
         int  touches = 0;
         for(int j = i + 3; j <= last; j++)
           {
            if(r[j].close < bottom) { invalid = true; break; }
            if(r[j].low <= top) touches++;
           }
         if(invalid) continue;

         out.obBull.valid   = true;
         out.obBull.top     = top;
         out.obBull.bottom  = bottom;
         out.obBull.barsAgo = last - i;
         out.obBull.touches = touches;
         out.obBull.mitigating = (r[last].low <= top + 0.2 * atr && close >= bottom);
         //--- quality components, 20 points each
         double q = 0.0;
         if(out.obBull.barsAgo <= 10)      q += 20.0;       // fresh zone
         else if(out.obBull.barsAgo <= 20) q += 12.0;
         else                              q += 5.0;
         if(touches <= 1)                  q += 20.0;       // barely tested
         else if(touches == 2)             q += 10.0;
         if((double)r[i].tick_volume >= 1.2 * avgVol) q += 20.0;   // real participation
         else if((double)r[i].tick_volume >= 0.8 * avgVol) q += 10.0;
         if(out.rangePos <= 0.5)           q += 20.0;       // demand in discount
         else                              q += 5.0;
         if(sweepBullBar >= 0 && MathAbs(i - sweepBullBar) <= 3) q += 20.0; // born from a sweep
         else                              q += 8.0;
         out.obBull.quality = q;
         break;                                             // most recent valid OB wins
        }
      //--- bear OB: mirror image (last bullish candle before down impulse)
      for(int i = last - 3; i >= from; i--)
        {
         if(r[i].close <= r[i].open) continue;
         bool impulse = false;
         for(int k = 1; k <= 3 && i + k <= last; k++)
            if(r[i + k].close < r[i].low - 0.5 * atr) { impulse = true; break; }
         if(!impulse) continue;

         double top = r[i].high, bottom = r[i].low;
         bool invalid = false;
         int  touches = 0;
         for(int j = i + 3; j <= last; j++)
           {
            if(r[j].close > top) { invalid = true; break; }
            if(r[j].high >= bottom) touches++;
           }
         if(invalid) continue;

         out.obBear.valid   = true;
         out.obBear.top     = top;
         out.obBear.bottom  = bottom;
         out.obBear.barsAgo = last - i;
         out.obBear.touches = touches;
         out.obBear.mitigating = (r[last].high >= bottom - 0.2 * atr && close <= top);
         double q = 0.0;
         if(out.obBear.barsAgo <= 10)      q += 20.0;
         else if(out.obBear.barsAgo <= 20) q += 12.0;
         else                              q += 5.0;
         if(touches <= 1)                  q += 20.0;
         else if(touches == 2)             q += 10.0;
         if((double)r[i].tick_volume >= 1.2 * avgVol) q += 20.0;
         else if((double)r[i].tick_volume >= 0.8 * avgVol) q += 10.0;
         if(out.rangePos >= 0.5)           q += 20.0;       // supply in premium
         else                              q += 5.0;
         if(sweepBearBar >= 0 && MathAbs(i - sweepBearBar) <= 3) q += 20.0;
         else                              q += 8.0;
         out.obBear.quality = q;
         break;
        }

      //--- 4) Fair Value Gaps with mitigation state --------------------
      //--- bull FVG: low[i] above high[i-2] leaves an imbalance.
      //--- Mitigated = price later traded back INTO the gap (partial
      //--- fill) without closing below it - the classic entry moment.
      for(int i = from; i <= last && !out.fvgBull; i++)
        {
         double gapBottom = r[i - 2].high;
         double gapTop    = r[i].low;
         if(gapTop <= gapBottom) continue;                  // no gap
         if(gapTop - gapBottom < 0.2 * atr) continue;       // ignore noise gaps
         bool invalidated = false, mitigated = false;
         for(int j = i + 1; j <= last; j++)
           {
            if(r[j].close < gapBottom) { invalidated = true; break; }
            if(r[j].low <= gapTop) mitigated = true;        // dipped into the gap
           }
         if(!invalidated && close > gapBottom)
           {
            out.fvgBull = true;
            out.fvgBullMitigated = mitigated;
           }
        }
      //--- bear FVG: mirror image
      for(int i = from; i <= last && !out.fvgBear; i++)
        {
         double gapTop    = r[i - 2].low;
         double gapBottom = r[i].high;
         if(gapTop <= gapBottom) continue;
         if(gapTop - gapBottom < 0.2 * atr) continue;
         bool invalidated = false, mitigated = false;
         for(int j = i + 1; j <= last; j++)
           {
            if(r[j].close > gapTop) { invalidated = true; break; }
            if(r[j].high >= gapBottom) mitigated = true;
           }
         if(!invalidated && close < gapTop)
           {
            out.fvgBear = true;
            out.fvgBearMitigated = mitigated;
           }
        }

      return(true);
     }
  };

#endif // CG_SMART_MONEY_MQH
