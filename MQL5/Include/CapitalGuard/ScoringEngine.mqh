//+------------------------------------------------------------------+
//|                                              ScoringEngine.mqh   |
//|  CapitalGuard - Weighted confluence decision engine              |
//|                                                                  |
//|  Combines Trend / Momentum / Volume / Structure / Volatility     |
//|  into a 0-100 confidence score. A trade is only allowed when     |
//|  the total score clears the threshold AND higher timeframes      |
//|  do not conflict with the trade direction.                       |
//|                                                                  |
//|  Category weights (defaults, configurable):                      |
//|    Trend 25% | Momentum 20% | Volume 20% | Structure 20% |       |
//|    Volatility 15%                                                |
//+------------------------------------------------------------------+
#ifndef CG_SCORING_ENGINE_MQH
#define CG_SCORING_ENGINE_MQH

#include "IndicatorSet.mqh"
#include "MarketStructure.mqh"
#include "Regime.mqh"

//--- full evaluation result for one candidate signal
struct SSignal
  {
   int               direction;     // +1 buy, -1 sell, 0 no trade
   double            total;         // weighted total 0-100
   double            trendScore;    // per-category raw scores 0-100
   double            momentumScore;
   double            volumeScore;
   double            structureScore;
   double            volatilityScore;
   string            reason;        // human-readable explanation
  };

//+------------------------------------------------------------------+
//| Weighted scoring engine                                          |
//+------------------------------------------------------------------+
class CScoringEngine
  {
private:
   //--- category weights, normalized to sum 1.0 in Init
   double            m_wTrend;
   double            m_wMomentum;
   double            m_wVolume;
   double            m_wStructure;
   double            m_wVolatility;

   //--- clamp helper
   double            Clamp(const double v, const double lo, const double hi) const
     {
      return(MathMax(lo, MathMin(hi, v)));
     }

public:
   //--- configure weights (any scale; normalized internally)
   void              Init(const double wTrend, const double wMomentum, const double wVolume,
                          const double wStructure, const double wVolatility)
     {
      double sum = wTrend + wMomentum + wVolume + wStructure + wVolatility;
      if(sum <= 0.0) sum = 1.0;
      m_wTrend      = wTrend / sum;
      m_wMomentum   = wMomentum / sum;
      m_wVolume     = wVolume / sum;
      m_wStructure  = wStructure / sum;
      m_wVolatility = wVolatility / sum;
     }

   //--- decide candidate direction from higher-timeframe trend votes;
   //--- returns 0 when H4 and H1 conflict (multi-TF rule: avoid entry)
   int               DecideDirection(CIndicatorSet &h4, CIndicatorSet &h1, CIndicatorSet &m15)
     {
      int tH4  = h4.TrendDirection();
      int tH1  = h1.TrendDirection();
      int tM15 = m15.TrendDirection();

      //--- hard rule: major timeframes conflicting => stand aside
      if(tH4 != 0 && tH1 != 0 && tH4 != tH1)
         return(0);

      //--- majority vote weighted toward the higher timeframe
      int vote = tH4 * 2 + tH1 * 2 + tM15;
      if(vote >= 2)  return(1);
      if(vote <= -2) return(-1);
      return(0);
     }

   //--- Trend category: EMA stack alignment across H4/H1/M15 + VWAP side
   double            ScoreTrend(const int dir, CIndicatorSet &h4, CIndicatorSet &h1,
                                CIndicatorSet &m15, CIndicatorSet &entry)
     {
      double score = 0.0;
      if(h4.TrendDirection()  == dir) score += 35.0;
      if(h1.TrendDirection()  == dir) score += 30.0;
      if(m15.TrendDirection() == dir) score += 20.0;
      //--- price on the favorable side of session VWAP
      double vwap = entry.SessionVWAP();
      double c    = entry.Close(1);
      if(vwap != EMPTY_VALUE && c > 0.0)
        {
         if(dir > 0 && c > vwap) score += 15.0;
         if(dir < 0 && c < vwap) score += 15.0;
        }
      return(Clamp(score, 0.0, 100.0));
     }

   //--- Momentum category: RSI zone + MACD agreement on the entry TF
   double            ScoreMomentum(const int dir, CIndicatorSet &entry, CIndicatorSet &h1)
     {
      double score = 0.0;
      double rsi     = entry.Rsi(1);
      double rsiPrev = entry.Rsi(2);
      if(rsi != EMPTY_VALUE && rsiPrev != EMPTY_VALUE)
        {
         if(dir > 0)
           {
            //--- buy: RSI in bullish zone and rising, not overbought
            if(rsi > 50.0 && rsi < 70.0) score += 25.0;
            if(rsi > rsiPrev)            score += 10.0;
           }
         else
           {
            if(rsi < 50.0 && rsi > 30.0) score += 25.0;
            if(rsi < rsiPrev)            score += 10.0;
           }
        }
      //--- MACD main vs signal on entry TF
      double mm = entry.MacdMain(1), ms = entry.MacdSignal(1);
      if(mm != EMPTY_VALUE && ms != EMPTY_VALUE)
        {
         if(dir > 0 && mm > ms) score += 25.0;
         if(dir < 0 && mm < ms) score += 25.0;
        }
      //--- MACD agreement on H1 confirms momentum on the higher TF
      double hm = h1.MacdMain(1), hs = h1.MacdSignal(1);
      if(hm != EMPTY_VALUE && hs != EMPTY_VALUE)
        {
         if(dir > 0 && hm > hs) score += 20.0;
         if(dir < 0 && hm < hs) score += 20.0;
        }
      //--- DI lines agree with direction
      double pdi = entry.PlusDI(1), mdi = entry.MinusDI(1);
      if(pdi != EMPTY_VALUE && mdi != EMPTY_VALUE)
        {
         if(dir > 0 && pdi > mdi) score += 20.0;
         if(dir < 0 && mdi > pdi) score += 20.0;
        }
      return(Clamp(score, 0.0, 100.0));
     }

   //--- Volume category: participation vs average + OBV slope agreement
   double            ScoreVolume(const int dir, CIndicatorSet &entry)
     {
      double score = 0.0;
      double vr = entry.VolumeRatio(20);
      if(vr >= 1.5)      score += 50.0;   // strong participation
      else if(vr >= 1.1) score += 35.0;
      else if(vr >= 0.8) score += 20.0;   // acceptable
      //--- OBV slope should push in the trade direction
      int obv = entry.ObvSlope(5);
      if(obv == dir)      score += 50.0;
      else if(obv == 0)   score += 20.0;
      return(Clamp(score, 0.0, 100.0));
     }

   //--- Structure category: bias match, recent BOS/CHoCH confirmation
   double            ScoreStructure(const int dir, const SStructureInfo &st)
     {
      double score = 0.0;
      if(st.bias == dir)          score += 50.0;  // trading with structure
      else if(st.bias == 0)       score += 15.0;  // unclear structure
      if(st.recentBOS && st.bias == dir)   score += 30.0;
      if(st.recentCHoCH && st.bias == dir) score += 20.0;
      return(Clamp(score, 0.0, 100.0));
     }

   //--- Volatility category: prefer normal volatility; penalize extremes
   double            ScoreVolatility(const SRegimeInfo &regime, CIndicatorSet &entry, const int dir)
     {
      double score = 0.0;
      switch(regime.vol)
        {
         case VOL_NORMAL: score += 70.0; break;    // ideal conditions
         case VOL_LOW:    score += 45.0; break;    // moves may not reach TP
         case VOL_HIGH:   score += 25.0; break;    // stop-hunt risk
        }
      //--- Bollinger position: avoid buying at the upper band / selling at lower
      double c  = entry.Close(1);
      double bu = entry.BandUpper(1);
      double bl = entry.BandLower(1);
      if(c > 0.0 && bu != EMPTY_VALUE && bl != EMPTY_VALUE && bu > bl)
        {
         double pos = (c - bl) / (bu - bl);     // 0 = lower band, 1 = upper band
         if(dir > 0 && pos < 0.75) score += 30.0;
         if(dir < 0 && pos > 0.25) score += 30.0;
        }
      return(Clamp(score, 0.0, 100.0));
     }

   //--- full evaluation: fills `sig` with direction, scores and reason
   void              Evaluate(CIndicatorSet &h4, CIndicatorSet &h1, CIndicatorSet &m30,
                              CIndicatorSet &m15, CIndicatorSet &entry,
                              const SStructureInfo &st, const SRegimeInfo &regime,
                              SSignal &sig)
     {
      sig.direction       = 0;
      sig.total           = 0.0;
      sig.trendScore      = 0.0;
      sig.momentumScore   = 0.0;
      sig.volumeScore     = 0.0;
      sig.structureScore  = 0.0;
      sig.volatilityScore = 0.0;
      sig.reason          = "";

      int dir = DecideDirection(h4, h1, m15);
      if(dir == 0)
        {
         sig.reason = "No direction / HTF conflict";
         return;
        }

      sig.direction       = dir;
      sig.trendScore      = ScoreTrend(dir, h4, h1, m15, entry);
      sig.momentumScore   = ScoreMomentum(dir, entry, h1);
      sig.volumeScore     = ScoreVolume(dir, entry);
      sig.structureScore  = ScoreStructure(dir, st);
      sig.volatilityScore = ScoreVolatility(regime, entry, dir);

      sig.total = sig.trendScore      * m_wTrend
                + sig.momentumScore   * m_wMomentum
                + sig.volumeScore     * m_wVolume
                + sig.structureScore  * m_wStructure
                + sig.volatilityScore * m_wVolatility;

      sig.reason = StringFormat("%s | Trend %.0f Mom %.0f Vol %.0f Struct %.0f Volat %.0f | %s %s",
                                dir > 0 ? "BUY" : "SELL",
                                sig.trendScore, sig.momentumScore, sig.volumeScore,
                                sig.structureScore, sig.volatilityScore,
                                CRegimeDetector::RegimeName(regime.regime),
                                CRegimeDetector::VolName(regime.vol));
     }
  };

#endif // CG_SCORING_ENGINE_MQH
