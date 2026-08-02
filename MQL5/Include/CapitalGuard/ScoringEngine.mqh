//+------------------------------------------------------------------+
//|                                              ScoringEngine.mqh   |
//|  CapitalGuard v2 - Weighted confluence decision engine           |
//|                                                                  |
//|  Ten scored categories combined into a 0-100 confidence score:   |
//|    Trend 20% | Market Structure 20% | Momentum 15% | Volume 10%  |
//|    Liquidity (SMC) 10% | Volatility 10% | News 10% | RR 5% |     |
//|    Spread 5% | Session 5%                                        |
//|                                                                  |
//|  A trade needs the total to clear the threshold (default 90)     |
//|  AND every hard checklist item enabled in the EA to pass.        |
//|  Higher-timeframe conflict always vetoes the trade.              |
//+------------------------------------------------------------------+
#ifndef CG_SCORING_ENGINE_MQH
#define CG_SCORING_ENGINE_MQH

#include "IndicatorSet.mqh"
#include "MarketStructure.mqh"
#include "Regime.mqh"
#include "SmartMoney.mqh"

//--- extra market context the EA supplies for scoring
struct SScoreContext
  {
   double            spreadPoints;     // current spread in points
   double            maxSpreadPoints;  // EA spread limit (for ratio scoring)
   string            session;          // "Overlap"/"London"/"NewYork"/"Asian"/""
   bool              newsClear;        // no high-impact event blocking now
   bool              newsNearby;       // high-impact event within soft window
   double            plannedRR;        // TP distance / SL distance of this setup
   SSmartMoney       smc;              // smart-money scan result
  };

//--- full evaluation result for one candidate signal
struct SSignal
  {
   int               direction;        // +1 buy, -1 sell, 0 no trade
   double            total;            // weighted total 0-100
   double            trendScore;
   double            structureScore;
   double            momentumScore;
   double            volumeScore;
   double            liquidityScore;
   double            volatilityScore;
   double            newsScore;
   double            rrScore;
   double            spreadScore;
   double            sessionScore;
   string            reason;           // human-readable explanation
  };

//+------------------------------------------------------------------+
//| Weighted scoring engine                                          |
//+------------------------------------------------------------------+
class CScoringEngine
  {
private:
   //--- category weights, normalized to sum 1.0 in Init
   double            m_wTrend, m_wStructure, m_wMomentum, m_wVolume, m_wLiquidity;
   double            m_wVolatility, m_wNews, m_wRR, m_wSpread, m_wSession;

   //--- clamp helper
   double            Clamp(const double v) const { return(MathMax(0.0, MathMin(100.0, v))); }

public:
   //--- configure weights (any scale; normalized internally)
   void              Init(const double wTrend, const double wStructure, const double wMomentum,
                          const double wVolume, const double wLiquidity, const double wVolatility,
                          const double wNews, const double wRR, const double wSpread,
                          const double wSession)
     {
      double sum = wTrend + wStructure + wMomentum + wVolume + wLiquidity
                 + wVolatility + wNews + wRR + wSpread + wSession;
      if(sum <= 0.0) sum = 1.0;
      m_wTrend      = wTrend / sum;
      m_wStructure  = wStructure / sum;
      m_wMomentum   = wMomentum / sum;
      m_wVolume     = wVolume / sum;
      m_wLiquidity  = wLiquidity / sum;
      m_wVolatility = wVolatility / sum;
      m_wNews       = wNews / sum;
      m_wRR         = wRR / sum;
      m_wSpread     = wSpread / sum;
      m_wSession    = wSession / sum;
     }

   //--- candidate direction from higher-timeframe votes;
   //--- 0 when H4 and H1 conflict (hard multi-TF veto)
   int               DecideDirection(CIndicatorSet &h4, CIndicatorSet &h1, CIndicatorSet &m15)
     {
      int tH4  = h4.TrendDirection();
      int tH1  = h1.TrendDirection();
      int tM15 = m15.TrendDirection();
      if(tH4 != 0 && tH1 != 0 && tH4 != tH1)
         return(0);
      int vote = tH4 * 2 + tH1 * 2 + tM15;
      if(vote >= 2)  return(1);
      if(vote <= -2) return(-1);
      return(0);
     }

   //--- Trend 20%: EMA stacks on H4/H1/M15, VWAP side, Ichimoku, SuperTrend
   double            ScoreTrend(const int dir, CIndicatorSet &h4, CIndicatorSet &h1,
                                CIndicatorSet &m15, CIndicatorSet &entry)
     {
      double score = 0.0;
      if(h4.TrendDirection()  == dir) score += 25.0;
      if(h1.TrendDirection()  == dir) score += 20.0;
      if(m15.TrendDirection() == dir) score += 15.0;
      double vwap = entry.SessionVWAP();
      double c    = entry.Close(1);
      if(vwap != EMPTY_VALUE && c > 0.0)
        {
         if(dir > 0 && c > vwap) score += 15.0;
         if(dir < 0 && c < vwap) score += 15.0;
        }
      if(h1.IchimokuBias() == dir) score += 15.0;
      if(entry.SuperTrendDir(3.0, 120) == dir) score += 10.0;
      return(Clamp(score));
     }

   //--- Structure 20%: bias match, BOS, CHoCH, Fibonacci golden-zone pullback
   double            ScoreStructure(const int dir, const SStructureInfo &st, CIndicatorSet &entry)
     {
      double score = 0.0;
      if(st.bias == dir)        score += 40.0;
      else if(st.bias == 0)     score += 10.0;
      if(st.recentBOS && st.bias == dir)   score += 30.0;
      if(st.recentCHoCH && st.bias == dir) score += 15.0;
      //--- Fibonacci: entry inside the 38.2-61.8% retracement of the
      //--- last swing leg is a discounted price in the trade direction
      double c = entry.Close(1);
      if(c > 0.0 && st.lastSwingHigh > st.lastSwingLow && st.lastSwingLow > 0.0)
        {
         double range = st.lastSwingHigh - st.lastSwingLow;
         if(range > 0.0)
           {
            double retr = (dir > 0) ? (st.lastSwingHigh - c) / range
                                    : (c - st.lastSwingLow) / range;
            if(retr >= 0.382 && retr <= 0.618) score += 15.0;
           }
        }
      return(Clamp(score));
     }

   //--- Momentum 15%: RSI zone+slope, MACD (entry+H1), DI, Tenkan/Kijun
   double            ScoreMomentum(const int dir, CIndicatorSet &entry, CIndicatorSet &h1)
     {
      double score = 0.0;
      double rsi = entry.Rsi(1), rsiPrev = entry.Rsi(2);
      if(rsi != EMPTY_VALUE && rsiPrev != EMPTY_VALUE)
        {
         if(dir > 0)
           {
            if(rsi > 50.0 && rsi < 70.0) score += 20.0;   // bullish, not overbought
            if(rsi > rsiPrev)            score += 10.0;
           }
         else
           {
            if(rsi < 50.0 && rsi > 30.0) score += 20.0;
            if(rsi < rsiPrev)            score += 10.0;
           }
        }
      double mm = entry.MacdMain(1), ms = entry.MacdSignal(1);
      if(mm != EMPTY_VALUE && ms != EMPTY_VALUE)
        {
         if(dir > 0 && mm > ms) score += 20.0;
         if(dir < 0 && mm < ms) score += 20.0;
        }
      double hm = h1.MacdMain(1), hs = h1.MacdSignal(1);
      if(hm != EMPTY_VALUE && hs != EMPTY_VALUE)
        {
         if(dir > 0 && hm > hs) score += 15.0;
         if(dir < 0 && hm < hs) score += 15.0;
        }
      double pdi = entry.PlusDI(1), mdi = entry.MinusDI(1);
      if(pdi != EMPTY_VALUE && mdi != EMPTY_VALUE)
        {
         if(dir > 0 && pdi > mdi) score += 15.0;
         if(dir < 0 && mdi > pdi) score += 15.0;
        }
      double tk = entry.Tenkan(1), kj = entry.Kijun(1);
      if(tk != EMPTY_VALUE && kj != EMPTY_VALUE)
        {
         if(dir > 0 && tk > kj) score += 20.0;
         if(dir < 0 && tk < kj) score += 20.0;
        }
      return(Clamp(score));
     }

   //--- Volume 10%: participation vs average, OBV slope, CMF sign
   double            ScoreVolume(const int dir, CIndicatorSet &entry)
     {
      double score = 0.0;
      double vr = entry.VolumeRatio(20);
      if(vr >= 1.5)      score += 40.0;
      else if(vr >= 1.1) score += 30.0;
      else if(vr >= 0.8) score += 15.0;
      int obv = entry.ObvSlope(5);
      if(obv == dir)     score += 35.0;
      else if(obv == 0)  score += 10.0;
      double cmf = entry.Cmf(20);
      if(dir > 0 && cmf > 0.0)  score += 25.0;
      if(dir < 0 && cmf < 0.0)  score += 25.0;
      return(Clamp(score));
     }

   //--- Liquidity 10%: sweep, order block retest, fair value gap
   double            ScoreLiquidity(const int dir, const SSmartMoney &smc)
     {
      double score = 0.0;
      if(dir > 0)
        {
         if(smc.sweepBull) score += 40.0;   // stop-hunt done = fuel for longs
         if(smc.obBull)    score += 30.0;   // entering from institutional zone
         if(smc.fvgBull)   score += 30.0;   // imbalance supports the move
        }
      else
        {
         if(smc.sweepBear) score += 40.0;
         if(smc.obBear)    score += 30.0;
         if(smc.fvgBear)   score += 30.0;
        }
      return(Clamp(score));
     }

   //--- Volatility 10%: regime state, Bollinger position, ATR band
   double            ScoreVolatility(const SRegimeInfo &regime, CIndicatorSet &entry, const int dir)
     {
      double score = 0.0;
      switch(regime.vol)
        {
         case VOL_NORMAL: score += 50.0; break;
         case VOL_LOW:    score += 30.0; break;
         case VOL_HIGH:   score += 15.0; break;
        }
      double c = entry.Close(1), bu = entry.BandUpper(1), bl = entry.BandLower(1);
      if(c > 0.0 && bu != EMPTY_VALUE && bl != EMPTY_VALUE && bu > bl)
        {
         double pos = (c - bl) / (bu - bl);
         if(dir > 0 && pos < 0.75) score += 30.0;   // not buying the top band
         if(dir < 0 && pos > 0.25) score += 30.0;
        }
      //--- ATR inside the healthy band (not dead, not chaotic)
      if(regime.atrRatio >= 0.7 && regime.atrRatio <= 1.4) score += 20.0;
      return(Clamp(score));
     }

   //--- News 10%: clear = full score, event looming = degraded
   double            ScoreNews(const SScoreContext &ctx)
     {
      if(!ctx.newsClear)  return(0.0);
      if(ctx.newsNearby)  return(40.0);
      return(100.0);
     }

   //--- RR 5%: reward the planned risk:reward of this specific setup
   double            ScoreRR(const SScoreContext &ctx)
     {
      if(ctx.plannedRR >= 2.5) return(100.0);
      if(ctx.plannedRR >= 2.0) return(80.0);
      if(ctx.plannedRR >= 1.5) return(50.0);
      return(0.0);
     }

   //--- Spread 5%: tighter spread = better execution quality
   double            ScoreSpread(const SScoreContext &ctx)
     {
      if(ctx.maxSpreadPoints <= 0.0) return(50.0);
      double ratio = ctx.spreadPoints / ctx.maxSpreadPoints;
      if(ratio <= 0.4) return(100.0);
      if(ratio <= 0.7) return(70.0);
      if(ratio <= 1.0) return(40.0);
      return(0.0);
     }

   //--- Session 5%: London/NY overlap is the highest-quality window
   double            ScoreSession(const SScoreContext &ctx)
     {
      if(ctx.session == "Overlap")  return(100.0);
      if(ctx.session == "London")   return(80.0);
      if(ctx.session == "NewYork")  return(80.0);
      if(ctx.session == "Asian")    return(40.0);
      return(0.0);
     }

   //--- full evaluation: fills `sig` with direction, scores and reason
   void              Evaluate(CIndicatorSet &h4, CIndicatorSet &h1, CIndicatorSet &m30,
                              CIndicatorSet &m15, CIndicatorSet &entry,
                              const SStructureInfo &st, const SRegimeInfo &regime,
                              const SScoreContext &ctx, SSignal &sig)
     {
      sig.direction = 0;   sig.total = 0.0;
      sig.trendScore = 0.0; sig.structureScore = 0.0; sig.momentumScore = 0.0;
      sig.volumeScore = 0.0; sig.liquidityScore = 0.0; sig.volatilityScore = 0.0;
      sig.newsScore = 0.0; sig.rrScore = 0.0; sig.spreadScore = 0.0; sig.sessionScore = 0.0;
      sig.reason = "";

      int dir = DecideDirection(h4, h1, m15);
      if(dir == 0)
        {
         sig.reason = "No direction / HTF conflict";
         return;
        }

      sig.direction       = dir;
      sig.trendScore      = ScoreTrend(dir, h4, h1, m15, entry);
      sig.structureScore  = ScoreStructure(dir, st, entry);
      sig.momentumScore   = ScoreMomentum(dir, entry, h1);
      sig.volumeScore     = ScoreVolume(dir, entry);
      sig.liquidityScore  = ScoreLiquidity(dir, ctx.smc);
      sig.volatilityScore = ScoreVolatility(regime, entry, dir);
      sig.newsScore       = ScoreNews(ctx);
      sig.rrScore         = ScoreRR(ctx);
      sig.spreadScore     = ScoreSpread(ctx);
      sig.sessionScore    = ScoreSession(ctx);

      sig.total = sig.trendScore      * m_wTrend
                + sig.structureScore  * m_wStructure
                + sig.momentumScore   * m_wMomentum
                + sig.volumeScore     * m_wVolume
                + sig.liquidityScore  * m_wLiquidity
                + sig.volatilityScore * m_wVolatility
                + sig.newsScore       * m_wNews
                + sig.rrScore         * m_wRR
                + sig.spreadScore     * m_wSpread
                + sig.sessionScore    * m_wSession;

      sig.reason = StringFormat(
         "%s | Tr %.0f St %.0f Mo %.0f Vo %.0f Liq %.0f Vola %.0f News %.0f RR %.0f Spr %.0f Ses %.0f | %s %s | %s",
         dir > 0 ? "BUY" : "SELL",
         sig.trendScore, sig.structureScore, sig.momentumScore, sig.volumeScore,
         sig.liquidityScore, sig.volatilityScore, sig.newsScore, sig.rrScore,
         sig.spreadScore, sig.sessionScore,
         CRegimeDetector::RegimeName(regime.regime),
         CRegimeDetector::VolName(regime.vol),
         ctx.session);
     }
  };

#endif // CG_SCORING_ENGINE_MQH
