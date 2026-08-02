//+------------------------------------------------------------------+
//|                                            MarketStructure.mqh   |
//|  CapitalGuard - Market structure analysis                        |
//|                                                                  |
//|  Detects swing highs/lows, Break of Structure (BOS) and          |
//|  Change of Character (CHoCH). Provides swing levels for          |
//|  structure-based stop loss placement.                            |
//+------------------------------------------------------------------+
#ifndef CG_MARKET_STRUCTURE_MQH
#define CG_MARKET_STRUCTURE_MQH

//--- result of one structure scan
struct SStructureInfo
  {
   double            lastSwingHigh;    // most recent confirmed swing high
   double            lastSwingLow;     // most recent confirmed swing low
   double            prevSwingHigh;    // the one before it
   double            prevSwingLow;
   int               bias;             // +1 bullish, -1 bearish, 0 unclear
   bool              recentBOS;        // structure break in recent bars
   bool              recentCHoCH;      // direction flip in recent bars
  };

//+------------------------------------------------------------------+
//| Market structure scanner (stateless per call)                    |
//+------------------------------------------------------------------+
class CMarketStructure
  {
private:
   string            m_symbol;
   ENUM_TIMEFRAMES   m_tf;
   int               m_swingBars;      // bars each side to confirm a swing
   int               m_lookback;       // how many bars to scan

   //--- true when bar `i` is a swing high (higher than k bars each side)
   bool              IsSwingHigh(const int i, const int k, const double &high[]) const
     {
      for(int j = 1; j <= k; j++)
        {
         if(high[i] <= high[i - j]) return(false);
         if(high[i] <= high[i + j]) return(false);
        }
      return(true);
     }

   //--- true when bar `i` is a swing low (lower than k bars each side)
   bool              IsSwingLow(const int i, const int k, const double &low[]) const
     {
      for(int j = 1; j <= k; j++)
        {
         if(low[i] >= low[i - j]) return(false);
         if(low[i] >= low[i + j]) return(false);
        }
      return(true);
     }

public:
   //--- configure the scanner
   void              Init(const string symbol, const ENUM_TIMEFRAMES tf,
                          const int swingBars, const int lookback)
     {
      m_symbol    = symbol;
      m_tf        = tf;
      m_swingBars = swingBars;
      m_lookback  = lookback;
     }

   //--- scan recent bars and fill the structure snapshot
   bool              Scan(SStructureInfo &info)
     {
      info.lastSwingHigh = 0.0;
      info.lastSwingLow  = 0.0;
      info.prevSwingHigh = 0.0;
      info.prevSwingLow  = 0.0;
      info.bias          = 0;
      info.recentBOS     = false;
      info.recentCHoCH   = false;

      int need = m_lookback + m_swingBars * 2 + 2;
      double high[], low[], close[];
      if(CopyHigh(m_symbol, m_tf, 0, need, high) < need)  return(false);
      if(CopyLow(m_symbol, m_tf, 0, need, low) < need)    return(false);
      if(CopyClose(m_symbol, m_tf, 0, need, close) < need) return(false);
      //--- index 0 = oldest, last index = current bar
      int last = ArraySize(high) - 1;

      //--- collect swing points from oldest to newest (skip unconfirmed edge)
      double swingHighs[]; double swingLows[];
      int    shIdx[];      int    slIdx[];
      for(int i = m_swingBars; i <= last - m_swingBars - 1; i++)
        {
         if(IsSwingHigh(i, m_swingBars, high))
           {
            int n = ArraySize(swingHighs);
            ArrayResize(swingHighs, n + 1); ArrayResize(shIdx, n + 1);
            swingHighs[n] = high[i]; shIdx[n] = i;
           }
         if(IsSwingLow(i, m_swingBars, low))
           {
            int n = ArraySize(swingLows);
            ArrayResize(swingLows, n + 1); ArrayResize(slIdx, n + 1);
            swingLows[n] = low[i]; slIdx[n] = i;
           }
        }

      int nh = ArraySize(swingHighs);
      int nl = ArraySize(swingLows);
      if(nh < 2 || nl < 2) return(false);

      info.lastSwingHigh = swingHighs[nh - 1];
      info.prevSwingHigh = swingHighs[nh - 2];
      info.lastSwingLow  = swingLows[nl - 1];
      info.prevSwingLow  = swingLows[nl - 2];

      //--- determine bias from the latest confirmed structure break:
      //--- close above last swing high => bullish BOS,
      //--- close below last swing low  => bearish BOS
      double lastClose = close[last - 1];   // last closed bar
      int    bosDir    = 0;
      int    bosBarsAgo = -1;
      for(int i = last - 1; i > MathMax(last - 1 - m_lookback, m_swingBars); i--)
        {
         //--- find the most recent close that broke a swing formed before it
         for(int h = nh - 1; h >= 0; h--)
           {
            if(shIdx[h] < i && close[i] > swingHighs[h] && close[i - 1] <= swingHighs[h])
              { bosDir = 1; bosBarsAgo = last - 1 - i; break; }
           }
         if(bosDir != 0) break;
         for(int l = nl - 1; l >= 0; l--)
           {
            if(slIdx[l] < i && close[i] < swingLows[l] && close[i - 1] >= swingLows[l])
              { bosDir = -1; bosBarsAgo = last - 1 - i; break; }
           }
         if(bosDir != 0) break;
        }

      //--- fallback bias: higher-highs/higher-lows pattern
      if(bosDir != 0)
         info.bias = bosDir;
      else
        {
         bool hhhl = (info.lastSwingHigh > info.prevSwingHigh && info.lastSwingLow > info.prevSwingLow);
         bool lllh = (info.lastSwingHigh < info.prevSwingHigh && info.lastSwingLow < info.prevSwingLow);
         if(hhhl) info.bias = 1;
         if(lllh) info.bias = -1;
        }

      info.recentBOS = (bosBarsAgo >= 0 && bosBarsAgo <= 10);

      //--- CHoCH: latest break direction opposes the swing pattern before it
      if(info.recentBOS)
        {
         bool wasDown = (info.lastSwingHigh < info.prevSwingHigh);
         bool wasUp   = (info.lastSwingLow  > info.prevSwingLow);
         if(bosDir == 1 && wasDown)  info.recentCHoCH = true;
         if(bosDir == -1 && wasUp)   info.recentCHoCH = true;
        }

      return(true);
     }

   //--- structure-based SL level for a buy: below the last swing low
   //--- returns 0.0 when no valid swing is available
   double            BuyStopLevel(const SStructureInfo &info, const double buffer) const
     {
      if(info.lastSwingLow <= 0.0) return(0.0);
      return(info.lastSwingLow - buffer);
     }

   //--- structure-based SL level for a sell: above the last swing high
   double            SellStopLevel(const SStructureInfo &info, const double buffer) const
     {
      if(info.lastSwingHigh <= 0.0) return(0.0);
      return(info.lastSwingHigh + buffer);
     }
  };

#endif // CG_MARKET_STRUCTURE_MQH
