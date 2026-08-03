//+------------------------------------------------------------------+
//|                                                     Regime.mqh   |
//|  CapitalGuard - Market regime detection                          |
//|                                                                  |
//|  Classifies the market into Trending / Range and                 |
//|  High / Normal / Low volatility using ADX and ATR ratio.         |
//|  The scoring engine and RR target adapt to the regime.           |
//+------------------------------------------------------------------+
#ifndef CG_REGIME_MQH
#define CG_REGIME_MQH

#include "IndicatorSet.mqh"

//--- market regime classification
enum ENUM_REGIME
  {
   REGIME_TREND_UP,      // Trending up
   REGIME_TREND_DOWN,    // Trending down
   REGIME_RANGE,         // Ranging / sideways
   REGIME_UNKNOWN        // Not enough data
  };

//--- volatility state
enum ENUM_VOL_STATE
  {
   VOL_LOW,              // ATR well below average
   VOL_NORMAL,           // ATR near average
   VOL_HIGH              // ATR well above average
  };

//--- combined snapshot for one detection pass
struct SRegimeInfo
  {
   ENUM_REGIME       regime;
   ENUM_VOL_STATE    vol;
   double            adx;         // ADX value used
   double            atrRatio;    // current ATR / average ATR
  };

//+------------------------------------------------------------------+
//| Regime detector                                                  |
//+------------------------------------------------------------------+
class CRegimeDetector
  {
private:
   double            m_adxTrendMin;    // ADX above this = trending
   double            m_highVolRatio;   // ATR ratio above this = high vol
   double            m_lowVolRatio;    // ATR ratio below this = low vol
   int               m_atrAvgBars;     // baseline window for ATR average

public:
   //--- configure thresholds
   void              Init(const double adxTrendMin, const double highVolRatio,
                          const double lowVolRatio, const int atrAvgBars)
     {
      m_adxTrendMin  = adxTrendMin;
      m_highVolRatio = highVolRatio;
      m_lowVolRatio  = lowVolRatio;
      m_atrAvgBars   = atrAvgBars;
     }

   //--- classify current market using the given indicator set (H1 suggested)
   void              Detect(CIndicatorSet &ind, SRegimeInfo &out)
     {
      out.regime   = REGIME_UNKNOWN;
      out.vol      = VOL_NORMAL;
      out.adx      = ind.Adx(1);
      out.atrRatio = 1.0;

      double atrNow = ind.Atr(1);
      double atrAvg = ind.AtrAverage(m_atrAvgBars);
      if(atrNow != EMPTY_VALUE && atrAvg != EMPTY_VALUE && atrAvg > 0.0)
         out.atrRatio = atrNow / atrAvg;

      //--- volatility state
      if(out.atrRatio >= m_highVolRatio)      out.vol = VOL_HIGH;
      else if(out.atrRatio <= m_lowVolRatio)  out.vol = VOL_LOW;
      else                                    out.vol = VOL_NORMAL;

      //--- trend vs range
      if(out.adx == EMPTY_VALUE)
         return;
      if(out.adx >= m_adxTrendMin)
        {
         double pdi = ind.PlusDI(1);
         double mdi = ind.MinusDI(1);
         if(pdi != EMPTY_VALUE && mdi != EMPTY_VALUE)
            out.regime = (pdi > mdi) ? REGIME_TREND_UP : REGIME_TREND_DOWN;
         else
            out.regime = REGIME_RANGE;
        }
      else
         out.regime = REGIME_RANGE;
     }

   //--- human-readable regime label for logs / dashboard
   static string     RegimeName(const ENUM_REGIME r)
     {
      switch(r)
        {
         case REGIME_TREND_UP:   return("TREND UP");
         case REGIME_TREND_DOWN: return("TREND DOWN");
         case REGIME_RANGE:      return("RANGE");
        }
      return("UNKNOWN");
     }

   //--- human-readable volatility label
   static string     VolName(const ENUM_VOL_STATE v)
     {
      switch(v)
        {
         case VOL_LOW:  return("LOW VOL");
         case VOL_HIGH: return("HIGH VOL");
        }
      return("NORMAL VOL");
     }
  };

#endif // CG_REGIME_MQH
