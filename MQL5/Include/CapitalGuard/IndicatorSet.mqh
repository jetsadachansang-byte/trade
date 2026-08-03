//+------------------------------------------------------------------+
//|                                               IndicatorSet.mqh   |
//|  CapitalGuard - Indicator bundle for one timeframe               |
//|                                                                  |
//|  Wraps: EMA 20/50/200, VWAP (session), RSI, MACD, ATR, ADX,      |
//|         Bollinger Bands, OBV, CMF, Ichimoku, SuperTrend,         |
//|         Daily Pivot Points, tick volume                          |
//+------------------------------------------------------------------+
#ifndef CG_INDICATOR_SET_MQH
#define CG_INDICATOR_SET_MQH

//+------------------------------------------------------------------+
//| One-timeframe indicator container                                |
//+------------------------------------------------------------------+
class CIndicatorSet
  {
private:
   string            m_symbol;
   ENUM_TIMEFRAMES   m_tf;
   int               m_hEma20;
   int               m_hEma50;
   int               m_hEma200;
   int               m_hRsi;
   int               m_hMacd;
   int               m_hAtr;
   int               m_hAdx;
   int               m_hBands;
   int               m_hObv;
   int               m_hIchimoku;

   //--- read one value from an indicator buffer; EMPTY_VALUE on failure
   double            Buf(const int handle, const int buffer, const int shift) const
     {
      double v[1];
      if(handle == INVALID_HANDLE) return(EMPTY_VALUE);
      if(CopyBuffer(handle, buffer, shift, 1, v) < 1) return(EMPTY_VALUE);
      return(v[0]);
     }

public:
                     CIndicatorSet() : m_hEma20(INVALID_HANDLE), m_hEma50(INVALID_HANDLE),
                                       m_hEma200(INVALID_HANDLE), m_hRsi(INVALID_HANDLE),
                                       m_hMacd(INVALID_HANDLE), m_hAtr(INVALID_HANDLE),
                                       m_hAdx(INVALID_HANDLE), m_hBands(INVALID_HANDLE),
                                       m_hObv(INVALID_HANDLE), m_hIchimoku(INVALID_HANDLE) {}

   //--- create all indicator handles for the given timeframe
   bool              Init(const string symbol, const ENUM_TIMEFRAMES tf,
                          const int emaFast, const int emaMid, const int emaSlow,
                          const int rsiPeriod, const int atrPeriod, const int adxPeriod,
                          const int bbPeriod, const double bbDev)
     {
      m_symbol = symbol;
      m_tf     = tf;
      m_hEma20  = iMA(symbol, tf, emaFast, 0, MODE_EMA, PRICE_CLOSE);
      m_hEma50  = iMA(symbol, tf, emaMid, 0, MODE_EMA, PRICE_CLOSE);
      m_hEma200 = iMA(symbol, tf, emaSlow, 0, MODE_EMA, PRICE_CLOSE);
      m_hRsi    = iRSI(symbol, tf, rsiPeriod, PRICE_CLOSE);
      m_hMacd   = iMACD(symbol, tf, 12, 26, 9, PRICE_CLOSE);
      m_hAtr    = iATR(symbol, tf, atrPeriod);
      m_hAdx    = iADX(symbol, tf, adxPeriod);
      m_hBands  = iBands(symbol, tf, bbPeriod, 0, bbDev, PRICE_CLOSE);
      m_hObv    = iOBV(symbol, tf, VOLUME_TICK);
      m_hIchimoku = iIchimoku(symbol, tf, 9, 26, 52);
      return(m_hEma20 != INVALID_HANDLE && m_hEma50 != INVALID_HANDLE &&
             m_hEma200 != INVALID_HANDLE && m_hRsi != INVALID_HANDLE &&
             m_hMacd != INVALID_HANDLE && m_hAtr != INVALID_HANDLE &&
             m_hAdx != INVALID_HANDLE && m_hBands != INVALID_HANDLE &&
             m_hObv != INVALID_HANDLE && m_hIchimoku != INVALID_HANDLE);
     }

   //--- release all handles; call from OnDeinit
   void              Release()
     {
      if(m_hEma20  != INVALID_HANDLE) IndicatorRelease(m_hEma20);
      if(m_hEma50  != INVALID_HANDLE) IndicatorRelease(m_hEma50);
      if(m_hEma200 != INVALID_HANDLE) IndicatorRelease(m_hEma200);
      if(m_hRsi    != INVALID_HANDLE) IndicatorRelease(m_hRsi);
      if(m_hMacd   != INVALID_HANDLE) IndicatorRelease(m_hMacd);
      if(m_hAtr    != INVALID_HANDLE) IndicatorRelease(m_hAtr);
      if(m_hAdx    != INVALID_HANDLE) IndicatorRelease(m_hAdx);
      if(m_hBands  != INVALID_HANDLE) IndicatorRelease(m_hBands);
      if(m_hObv    != INVALID_HANDLE) IndicatorRelease(m_hObv);
      if(m_hIchimoku != INVALID_HANDLE) IndicatorRelease(m_hIchimoku);
     }

   //--- value accessors (shift 1 = last closed bar)
   double            Ema20(const int shift)  const { return(Buf(m_hEma20, 0, shift)); }
   double            Ema50(const int shift)  const { return(Buf(m_hEma50, 0, shift)); }
   double            Ema200(const int shift) const { return(Buf(m_hEma200, 0, shift)); }
   double            Rsi(const int shift)    const { return(Buf(m_hRsi, 0, shift)); }
   double            MacdMain(const int shift)   const { return(Buf(m_hMacd, 0, shift)); }
   double            MacdSignal(const int shift) const { return(Buf(m_hMacd, 1, shift)); }
   double            Atr(const int shift)    const { return(Buf(m_hAtr, 0, shift)); }
   double            Adx(const int shift)    const { return(Buf(m_hAdx, 0, shift)); }
   double            PlusDI(const int shift) const { return(Buf(m_hAdx, 1, shift)); }
   double            MinusDI(const int shift)const { return(Buf(m_hAdx, 2, shift)); }
   double            BandUpper(const int shift) const { return(Buf(m_hBands, 1, shift)); }
   double            BandLower(const int shift) const { return(Buf(m_hBands, 2, shift)); }
   double            BandMid(const int shift)   const { return(Buf(m_hBands, 0, shift)); }
   double            Obv(const int shift)    const { return(Buf(m_hObv, 0, shift)); }
   double            Close(const int shift)  const { return(iClose(m_symbol, m_tf, shift)); }
   double            High(const int shift)   const { return(iHigh(m_symbol, m_tf, shift)); }
   double            Low(const int shift)    const { return(iLow(m_symbol, m_tf, shift)); }

   //--- average ATR over `bars` closed bars (volatility baseline)
   double            AtrAverage(const int bars) const
     {
      double buf[];
      if(m_hAtr == INVALID_HANDLE) return(EMPTY_VALUE);
      if(CopyBuffer(m_hAtr, 0, 1, bars, buf) < bars) return(EMPTY_VALUE);
      double sum = 0.0;
      for(int i = 0; i < bars; i++) sum += buf[i];
      return(sum / bars);
     }

   //--- ratio of current tick volume vs its `bars`-bar average
   double            VolumeRatio(const int bars) const
     {
      long vol[];
      if(CopyTickVolume(m_symbol, m_tf, 1, bars + 1, vol) < bars + 1) return(1.0);
      //--- vol[] is ordered oldest..newest; last element = last closed bar
      double sum = 0.0;
      int n = ArraySize(vol);
      for(int i = 0; i < n - 1; i++) sum += (double)vol[i];
      double avg = sum / (n - 1);
      if(avg <= 0.0) return(1.0);
      return((double)vol[n - 1] / avg);
     }

   //--- OBV slope over `bars` closed bars: +1 rising, -1 falling, 0 flat
   int               ObvSlope(const int bars) const
     {
      double a = Buf(m_hObv, 0, bars);
      double b = Buf(m_hObv, 0, 1);
      if(a == EMPTY_VALUE || b == EMPTY_VALUE) return(0);
      if(b > a) return(1);
      if(b < a) return(-1);
      return(0);
     }

   //--- session VWAP computed from the start of the current server day
   double            SessionVWAP() const
     {
      datetime dayStart = iTime(m_symbol, PERIOD_D1, 0);
      MqlRates rates[];
      int copied = CopyRates(m_symbol, PERIOD_M5, dayStart, TimeCurrent(), rates);
      if(copied < 1) return(EMPTY_VALUE);
      double pv = 0.0, vv = 0.0;
      for(int i = 0; i < copied; i++)
        {
         double typical = (rates[i].high + rates[i].low + rates[i].close) / 3.0;
         double vol     = (double)rates[i].tick_volume;
         pv += typical * vol;
         vv += vol;
        }
      if(vv <= 0.0) return(EMPTY_VALUE);
      return(pv / vv);
     }

   //--- Ichimoku accessors (9/26/52)
   double            Tenkan(const int shift) const { return(Buf(m_hIchimoku, 0, shift)); }
   double            Kijun(const int shift)  const { return(Buf(m_hIchimoku, 1, shift)); }
   double            SpanA(const int shift)  const { return(Buf(m_hIchimoku, 2, shift)); }
   double            SpanB(const int shift)  const { return(Buf(m_hIchimoku, 3, shift)); }

   //--- Ichimoku bias: +1 price above cloud & Tenkan>Kijun, -1 opposite
   int               IchimokuBias() const
     {
      double c  = Close(1);
      double sa = SpanA(1), sb = SpanB(1);
      double tk = Tenkan(1), kj = Kijun(1);
      if(c == 0.0 || sa == EMPTY_VALUE || sb == EMPTY_VALUE ||
         tk == EMPTY_VALUE || kj == EMPTY_VALUE)
         return(0);
      double cloudTop = MathMax(sa, sb);
      double cloudBot = MathMin(sa, sb);
      if(c > cloudTop && tk > kj) return(1);
      if(c < cloudBot && tk < kj) return(-1);
      return(0);
     }

   //--- Chaikin Money Flow over `bars` closed bars (-1..+1)
   double            Cmf(const int bars) const
     {
      MqlRates rates[];
      if(CopyRates(m_symbol, m_tf, 1, bars, rates) < bars) return(0.0);
      double mfvSum = 0.0, volSum = 0.0;
      for(int i = 0; i < bars; i++)
        {
         double range = rates[i].high - rates[i].low;
         if(range <= 0.0) continue;
         double mult = ((rates[i].close - rates[i].low) - (rates[i].high - rates[i].close)) / range;
         double vol  = (double)rates[i].tick_volume;
         mfvSum += mult * vol;
         volSum += vol;
        }
      if(volSum <= 0.0) return(0.0);
      return(mfvSum / volSum);
     }

   //--- SuperTrend direction at the last closed bar: +1 up, -1 down
   //--- computed from (H+L)/2 +/- mult*ATR with standard band trailing
   int               SuperTrendDir(const double mult, const int lookback) const
     {
      MqlRates rates[];
      double atrBuf[];
      if(CopyRates(m_symbol, m_tf, 0, lookback, rates) < lookback) return(0);
      if(m_hAtr == INVALID_HANDLE) return(0);
      if(CopyBuffer(m_hAtr, 0, 0, lookback, atrBuf) < lookback) return(0);
      //--- both arrays ordered oldest..newest over the same bars
      int    dir = 1;
      double fu = 0.0, fl = 0.0;    // final upper / lower bands
      int    last = lookback - 1;   // index of the forming bar
      for(int i = 1; i < last; i++)  // stop at the last CLOSED bar
        {
         double mid = (rates[i].high + rates[i].low) / 2.0;
         double bu  = mid + mult * atrBuf[i];
         double bl  = mid - mult * atrBuf[i];
         if(i == 1) { fu = bu; fl = bl; }
         //--- classic band trailing rules
         if(bu < fu || rates[i - 1].close > fu) fu = bu;
         if(bl > fl || rates[i - 1].close < fl) fl = bl;
         if(dir == 1 && rates[i].close < fl)      dir = -1;
         else if(dir == -1 && rates[i].close > fu) dir = 1;
        }
      return(dir);
     }

   //--- classic daily pivot points from the previous D1 candle
   void              DailyPivots(double &p, double &r1, double &s1, double &r2, double &s2) const
     {
      double h = iHigh(m_symbol, PERIOD_D1, 1);
      double l = iLow(m_symbol, PERIOD_D1, 1);
      double c = iClose(m_symbol, PERIOD_D1, 1);
      p  = (h + l + c) / 3.0;
      r1 = 2.0 * p - l;
      s1 = 2.0 * p - h;
      r2 = p + (h - l);
      s2 = p - (h - l);
     }

   //--- EMA-stack trend of this timeframe: +1 up, -1 down, 0 mixed
   int               TrendDirection() const
     {
      double c    = Close(1);
      double e20  = Ema20(1);
      double e50  = Ema50(1);
      double e200 = Ema200(1);
      if(c == 0.0 || e20 == EMPTY_VALUE || e50 == EMPTY_VALUE || e200 == EMPTY_VALUE)
         return(0);
      if(c > e20 && e20 > e50 && e50 > e200) return(1);
      if(c < e20 && e20 < e50 && e50 < e200) return(-1);
      //--- softer read: price vs mid/slow EMAs agree
      if(c > e50 && e50 > e200) return(1);
      if(c < e50 && e50 < e200) return(-1);
      return(0);
     }
  };

#endif // CG_INDICATOR_SET_MQH
