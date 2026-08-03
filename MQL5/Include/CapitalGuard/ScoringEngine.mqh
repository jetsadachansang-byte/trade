//+------------------------------------------------------------------+
//|                                              ScoringEngine.mqh   |
//|  CapitalGuard v3 - SMC-first confidence scoring                  |
//|                                                                  |
//|  Smart Money Concepts are the PRIMARY decision system.           |
//|  Indicators only confirm - they can never be the reason to       |
//|  open a trade.                                                   |
//|                                                                  |
//|  Categories and default weights:                                 |
//|    Market Structure 25% | Liquidity 20% | BOS/CHoCH 20% |        |
//|    Order Block 15% | FVG 10% | Volume 5% |                       |
//|    Indicator Confirmation 5%                                     |
//|                                                                  |
//|  Direction comes from STRUCTURE (D1/H4/H1 swing patterns),       |
//|  never from indicators. Entry requires total >= threshold        |
//|  (default 90) plus the EA's sequential SMC pipeline.             |
//+------------------------------------------------------------------+
#ifndef CG_SCORING_ENGINE_MQH
#define CG_SCORING_ENGINE_MQH

#include "IndicatorSet.mqh"
#include "MarketStructure.mqh"
#include "Regime.mqh"
#include "SmartMoney.mqh"

//--- extra context the EA supplies for scoring
struct SScoreContext
  {
   SSmcAnalysis      smc;              // smart-money snapshot (entry TF)
   double            plannedRR;        // TP distance / SL distance
   string            session;          // for the reason string only
  };

//--- full evaluation result for one candidate signal
struct SSignal
  {
   int               direction;        // +1 buy, -1 sell, 0 no trade
   double            total;            // weighted total 0-100
   double            structureScore;   // Market Structure (25%)
   double            liquidityScore;   // Liquidity (20%)
   double            bosChochScore;    // BOS / CHoCH (20%)
   double            obScore;          // Order Block (15%)
   double            fvgScore;         // FVG (10%)
   double            volumeScore;      // Volume (5%)
   double            indicatorScore;   // Indicator confirmation (5%)
   string            reason;           // human-readable explanation
  };

//+------------------------------------------------------------------+
//| SMC-first scoring engine                                         |
//+------------------------------------------------------------------+
class CScoringEngine
  {
private:
   //--- category weights, normalized to sum 1.0 in Init
   double            m_wStructure, m_wLiquidity, m_wBosChoch, m_wOB;
   double            m_wFVG, m_wVolume, m_wIndicator;

   //--- clamp helper
   double            Clamp(const double v) const { return(MathMax(0.0, MathMin(100.0, v))); }

   //--- does this timeframe's structure support direction `dir`?
   //--- trend match = full, bias-only match = partial, opposing = veto
   double            TfSupport(const SStructureInfo &st, const int dir) const
     {
      ENUM_MS_TREND want = (dir > 0) ? MS_UPTREND : MS_DOWNTREND;
      ENUM_MS_TREND against = (dir > 0) ? MS_DOWNTREND : MS_UPTREND;
      if(st.trend == want)    return(1.0);
      if(st.trend == against) return(-1.0);
      if(st.bias == dir)      return(0.5);    // sideways swings, bias agrees
      if(st.bias == -dir)     return(-0.5);
      return(0.0);
     }

public:
   //--- configure weights (any scale; normalized internally)
   void              Init(const double wStructure, const double wLiquidity, const double wBosChoch,
                          const double wOB, const double wFVG, const double wVolume,
                          const double wIndicator)
     {
      double sum = wStructure + wLiquidity + wBosChoch + wOB + wFVG + wVolume + wIndicator;
      if(sum <= 0.0) sum = 1.0;
      m_wStructure = wStructure / sum;
      m_wLiquidity = wLiquidity / sum;
      m_wBosChoch  = wBosChoch / sum;
      m_wOB        = wOB / sum;
      m_wFVG       = wFVG / sum;
      m_wVolume    = wVolume / sum;
      m_wIndicator = wIndicator / sum;
     }

   //--- trade direction from MARKET STRUCTURE ONLY (never indicators):
   //--- H4 and H1 structure must agree; D1 must not actively oppose.
   //--- Counter-trend is only allowed when the entry TF printed a
   //--- CHoCH (clear reversal signal) - handled by the EA pipeline.
   int               DecideDirection(const SStructureInfo &d1, const SStructureInfo &h4,
                                     const SStructureInfo &h1)
     {
      int dirH4 = (h4.trend == MS_UPTREND) ? 1 : (h4.trend == MS_DOWNTREND) ? -1 : h4.bias;
      int dirH1 = (h1.trend == MS_UPTREND) ? 1 : (h1.trend == MS_DOWNTREND) ? -1 : h1.bias;
      if(dirH4 == 0 || dirH1 == 0) return(0);   // structure unclear = wait
      if(dirH4 != dirH1)           return(0);   // HTF conflict = wait
      //--- D1 actively opposing vetoes the idea
      int dirD1 = (d1.trend == MS_UPTREND) ? 1 : (d1.trend == MS_DOWNTREND) ? -1 : 0;
      if(dirD1 != 0 && dirD1 != dirH4) return(0);
      return(dirH4);
     }

   //--- Market Structure 25%: every timeframe must support the move
   double            ScoreStructure(const int dir, const SStructureInfo &d1,
                                    const SStructureInfo &h4, const SStructureInfo &h1,
                                    const SStructureInfo &entry)
     {
      double score = 0.0;
      double sD1 = TfSupport(d1, dir);
      double sH4 = TfSupport(h4, dir);
      double sH1 = TfSupport(h1, dir);
      double sEn = TfSupport(entry, dir);
      score += 20.0 * MathMax(0.0, sD1);
      score += 30.0 * MathMax(0.0, sH4);
      score += 30.0 * MathMax(0.0, sH1);
      score += 20.0 * MathMax(0.0, sEn);
      return(Clamp(score));
     }

   //--- Liquidity 20%: sweep already happened (fuel), a target pool
   //--- exists in profit direction, and price sits on the right side
   //--- of the dealing range
   double            ScoreLiquidity(const int dir, const SSmcAnalysis &smc)
     {
      double score = 0.0;
      if(dir > 0)
        {
         if(smc.sweepBull)     score += 50.0;   // sell-side grabbed = longs fueled
         if(smc.bslAbovePrice) score += 25.0;   // buy-side pool above = TP magnet
         if(smc.rangePos <= 0.5) score += 25.0; // buying in discount
        }
      else
        {
         if(smc.sweepBear)     score += 50.0;
         if(smc.sslBelowPrice) score += 25.0;
         if(smc.rangePos >= 0.5) score += 25.0; // selling in premium
        }
      return(Clamp(score));
     }

   //--- BOS/CHoCH 20%: a structure break in our direction is the
   //--- primary entry signal (MSS = CHoCH after a trend)
   double            ScoreBosChoch(const int dir, const SStructureInfo &entry)
     {
      double score = 0.0;
      if(entry.bias == dir && entry.recentBOS)   score += 60.0;
      if(entry.bias == dir && entry.recentCHoCH) score += 40.0;
      return(Clamp(score));
     }

   //--- Order Block 15%: the zone's own quality score, discounted
   //--- when price is not yet mitigating it
   double            ScoreOrderBlock(const int dir, const SSmcAnalysis &smc)
     {
      SOrderBlock ob;
      if(dir > 0) ob = smc.obBull; else ob = smc.obBear;
      if(!ob.valid) return(0.0);
      double score = ob.quality;
      if(!ob.mitigating) score *= 0.5;     // zone exists but price not there yet
      return(Clamp(score));
     }

   //--- FVG 10%: imbalance exists; mitigation (price returned into
   //--- the gap) is the high-probability moment
   double            ScoreFVG(const int dir, const SSmcAnalysis &smc)
     {
      double score = 0.0;
      bool exists    = (dir > 0) ? smc.fvgBull : smc.fvgBear;
      bool mitigated = (dir > 0) ? smc.fvgBullMitigated : smc.fvgBearMitigated;
      if(exists)    score += 50.0;
      if(mitigated) score += 50.0;
      return(Clamp(score));
     }

   //--- Volume 5%: participation behind the move
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
      if(dir > 0 && cmf > 0.0) score += 25.0;
      if(dir < 0 && cmf < 0.0) score += 25.0;
      return(Clamp(score));
     }

   //--- Indicator confirmation 5%: EMA/VWAP/RSI/MACD may only AGREE
   //--- or stay neutral - they carry too little weight to drive entry
   double            ScoreIndicator(const int dir, CIndicatorSet &entry, CIndicatorSet &h1)
     {
      double score = 0.0;
      if(entry.TrendDirection() == dir) score += 25.0;
      double vwap = entry.SessionVWAP();
      double c    = entry.Close(1);
      if(vwap != EMPTY_VALUE && c > 0.0)
        {
         if(dir > 0 && c > vwap) score += 25.0;
         if(dir < 0 && c < vwap) score += 25.0;
        }
      double rsi = entry.Rsi(1);
      if(rsi != EMPTY_VALUE)
        {
         if(dir > 0 && rsi > 50.0 && rsi < 70.0) score += 25.0;
         if(dir < 0 && rsi < 50.0 && rsi > 30.0) score += 25.0;
        }
      double mm = h1.MacdMain(1), ms = h1.MacdSignal(1);
      if(mm != EMPTY_VALUE && ms != EMPTY_VALUE)
        {
         if(dir > 0 && mm > ms) score += 25.0;
         if(dir < 0 && mm < ms) score += 25.0;
        }
      return(Clamp(score));
     }

   //--- full evaluation: fills `sig` with direction, scores and reason
   void              Evaluate(const int dir,
                              const SStructureInfo &d1, const SStructureInfo &h4,
                              const SStructureInfo &h1, const SStructureInfo &entrySt,
                              CIndicatorSet &entry, CIndicatorSet &indH1,
                              const SScoreContext &ctx, SSignal &sig)
     {
      sig.direction = dir;  sig.total = 0.0;
      sig.structureScore = 0.0; sig.liquidityScore = 0.0; sig.bosChochScore = 0.0;
      sig.obScore = 0.0; sig.fvgScore = 0.0; sig.volumeScore = 0.0;
      sig.indicatorScore = 0.0;
      sig.reason = "";
      if(dir == 0)
        {
         sig.reason = "Structure gives no direction / HTF conflict";
         return;
        }

      sig.structureScore = ScoreStructure(dir, d1, h4, h1, entrySt);
      sig.liquidityScore = ScoreLiquidity(dir, ctx.smc);
      sig.bosChochScore  = ScoreBosChoch(dir, entrySt);
      sig.obScore        = ScoreOrderBlock(dir, ctx.smc);
      sig.fvgScore       = ScoreFVG(dir, ctx.smc);
      sig.volumeScore    = ScoreVolume(dir, entry);
      sig.indicatorScore = ScoreIndicator(dir, entry, indH1);

      sig.total = sig.structureScore * m_wStructure
                + sig.liquidityScore * m_wLiquidity
                + sig.bosChochScore  * m_wBosChoch
                + sig.obScore        * m_wOB
                + sig.fvgScore       * m_wFVG
                + sig.volumeScore    * m_wVolume
                + sig.indicatorScore * m_wIndicator;

      sig.reason = StringFormat(
         "%s | Struct %.0f Liq %.0f BOS %.0f OB %.0f FVG %.0f Vol %.0f Ind %.0f | rangePos %.2f RR %.1f | %s",
         dir > 0 ? "BUY" : "SELL",
         sig.structureScore, sig.liquidityScore, sig.bosChochScore,
         sig.obScore, sig.fvgScore, sig.volumeScore, sig.indicatorScore,
         ctx.smc.rangePos, ctx.plannedRR, ctx.session);
     }
  };

#endif // CG_SCORING_ENGINE_MQH
