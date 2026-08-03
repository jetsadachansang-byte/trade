//+------------------------------------------------------------------+
//|                                              SymbolAnalyst.mqh   |
//|  CapitalGuard - Per-symbol SMC/ICT analyst                       |
//|                                                                  |
//|  Encapsulates the full analysis stack for ONE symbol so the      |
//|  signal EA can run many analysts in parallel with priorities:    |
//|   Tier 1: XAUUSD - analysed continuously (every scan cycle)      |
//|   Tier 2: major pairs - analysed on every closed entry bar       |
//|   Tier 3: crosses - analysed on closed bars, stricter threshold  |
//|                                                                  |
//|  Each analyst owns its indicator sets, multi-TF structure        |
//|  scanners (W1/D1/H4/H1/entry), smart-money scanner and regime    |
//|  detector, and produces a complete signal candidate when every   |
//|  SMC pipeline step passes.                                       |
//+------------------------------------------------------------------+
#ifndef CG_SYMBOL_ANALYST_MQH
#define CG_SYMBOL_ANALYST_MQH

#include "IndicatorSet.mqh"
#include "MarketStructure.mqh"
#include "SmartMoney.mqh"
#include "Regime.mqh"
#include "ScoringEngine.mqh"

//--- shared configuration filled from EA inputs
struct SAnalystConfig
  {
   ENUM_TIMEFRAMES   entryTF;
   //--- indicators
   int               emaFast, emaMid, emaSlow;
   int               rsiPeriod, atrPeriod, adxPeriod, bbPeriod;
   double            bbDev;
   //--- structure / SMC detection
   int               swingBars, structLookback, smcWindow;
   //--- regime
   double            adxTrendMin, highVolRatio, lowVolRatio;
   int               atrAvgBars;
   //--- SL model
   double            atrMultSL, maxSLAtrMult, minSLAtrMult;
   //--- pipeline gates
   bool              allowCounterTrend;
   bool              reqLiquidityTarget;
   bool              reqBosChoch;
   bool              reqOrderBlock;
   double            minOBQuality;
   bool              reqFVG;
   bool              reqSweep;
   bool              reqPremiumDiscount;
   double            discountMax;
   bool              reqMitigation;
   bool              reqTrendConfirm;
   bool              reqWeeklyBias;
   bool              reqOTE;
   //--- targets
   double            tp1R, tp2R, tp3R;
   int               maxSpreadPoints;
  };

//--- a fully specified signal candidate (not yet sent)
struct SSignalCandidate
  {
   bool              valid;
   string            symbol;
   int               tier;
   int               dir;           // +1 buy, -1 sell
   double            entry;         // reference entry price
   double            entryLow;      // entry zone bottom
   double            entryHigh;     // entry zone top
   double            sl;
   double            tp1, tp2, tp3;
   double            rr;            // headline RR (TP2)
   double            score;
   string            reasons;
   string            notes;
   string            tf;
  };

//+------------------------------------------------------------------+
//| One symbol's complete analysis stack                             |
//+------------------------------------------------------------------+
class CSymbolAnalyst
  {
private:
   string            m_symbol;
   int               m_tier;
   SAnalystConfig    m_cfg;
   //--- per-symbol modules
   CIndicatorSet     m_indH1, m_indM30, m_indM15, m_indEntry;
   CMarketStructure  m_structW1, m_structD1, m_structH4, m_structH1, m_structEntry;
   CSmartMoney       m_smc;
   CRegimeDetector   m_regimeDet;
   //--- state
   datetime          m_lastBarTime;
   SStructureInfo    m_stW1, m_stD1, m_stH4, m_stH1, m_stEntry;
   SRegimeInfo       m_regime;
   string            m_status;

public:
   //--- create all per-symbol resources; false when handles fail
   bool              Init(const string symbol, const int tier, const SAnalystConfig &cfg)
     {
      m_symbol = symbol;
      m_tier   = tier;
      m_cfg    = cfg;
      m_lastBarTime = 0;
      m_status = "initialising";
      m_stEntry.bias = 0;
      m_stEntry.recentBOS = false;
      m_stEntry.recentCHoCH = false;

      bool ok = true;
      ok &= m_indH1.Init(symbol, PERIOD_H1, cfg.emaFast, cfg.emaMid, cfg.emaSlow,
                         cfg.rsiPeriod, cfg.atrPeriod, cfg.adxPeriod, cfg.bbPeriod, cfg.bbDev);
      ok &= m_indM30.Init(symbol, PERIOD_M30, cfg.emaFast, cfg.emaMid, cfg.emaSlow,
                          cfg.rsiPeriod, cfg.atrPeriod, cfg.adxPeriod, cfg.bbPeriod, cfg.bbDev);
      ok &= m_indM15.Init(symbol, PERIOD_M15, cfg.emaFast, cfg.emaMid, cfg.emaSlow,
                          cfg.rsiPeriod, cfg.atrPeriod, cfg.adxPeriod, cfg.bbPeriod, cfg.bbDev);
      ok &= m_indEntry.Init(symbol, cfg.entryTF, cfg.emaFast, cfg.emaMid, cfg.emaSlow,
                            cfg.rsiPeriod, cfg.atrPeriod, cfg.adxPeriod, cfg.bbPeriod, cfg.bbDev);
      if(!ok) return(false);

      m_structW1.Init(symbol, PERIOD_W1, cfg.swingBars, cfg.structLookback);
      m_structD1.Init(symbol, PERIOD_D1, cfg.swingBars, cfg.structLookback);
      m_structH4.Init(symbol, PERIOD_H4, cfg.swingBars, cfg.structLookback);
      m_structH1.Init(symbol, PERIOD_H1, cfg.swingBars, cfg.structLookback);
      m_structEntry.Init(symbol, cfg.entryTF, cfg.swingBars, cfg.structLookback);
      m_smc.Init(symbol, cfg.entryTF, cfg.smcWindow);
      m_regimeDet.Init(cfg.adxTrendMin, cfg.highVolRatio, cfg.lowVolRatio, cfg.atrAvgBars);
      m_status = "ready";
      return(true);
     }

   //--- release indicator handles
   void              Release()
     {
      m_indH1.Release(); m_indM30.Release(); m_indM15.Release(); m_indEntry.Release();
     }

   //--- accessors for the EA / dashboards
   string            Symbol() const { return(m_symbol); }
   int               Tier()   const { return(m_tier); }
   string            Status() const { return(m_status); }
   //--- entry-TF structure (signal invalidation checks)
   void              EntryStructure(SStructureInfo &out) const { out = m_stEntry; }
   //--- compact multi-TF bias string, e.g. "W:UP D:UP H4:SIDE H1:UP"
   string            BiasSummary() const
     {
      return(StringFormat("W:%s D:%s H4:%s H1:%s",
                          TrendLabel(m_stW1), TrendLabel(m_stD1),
                          TrendLabel(m_stH4), TrendLabel(m_stH1)));
     }
   static string     TrendLabel(const SStructureInfo &st)
     {
      if(st.trend == MS_UPTREND)   return("UP");
      if(st.trend == MS_DOWNTREND) return("DOWN");
      return("SIDE");
     }

   //--- true when a fresh entry-TF bar has closed since the last call
   bool              IsNewBar()
     {
      datetime t = iTime(m_symbol, m_cfg.entryTF, 0);
      if(t == m_lastBarTime || t == 0)
         return(false);
      m_lastBarTime = t;
      return(true);
     }

   //--- refresh every structure snapshot + regime
   void              RefreshStructures()
     {
      m_structW1.Scan(m_stW1);
      m_structD1.Scan(m_stD1);
      m_structH4.Scan(m_stH4);
      m_structH1.Scan(m_stH1);
      m_structEntry.Scan(m_stEntry);
      m_regimeDet.Detect(m_indH1, m_regime);
     }

   //--- SL distance from swings + ATR bounds (no broker stops needed
   //--- for signals - the user places the order manually)
   double            ComputeSLDistance(const int dir, const double refPrice)
     {
      double atr = m_indEntry.Atr(1);
      if(atr == EMPTY_VALUE || atr <= 0.0) return(0.0);
      double buffer  = atr * 0.3;
      double slLevel = (dir > 0) ? m_structEntry.BuyStopLevel(m_stEntry, buffer)
                                 : m_structEntry.SellStopLevel(m_stEntry, buffer);
      double dist = 0.0;
      if(slLevel > 0.0)
         dist = (dir > 0) ? (refPrice - slLevel) : (slLevel - refPrice);
      if(dist <= 0.0)
         dist = atr * m_cfg.atrMultSL;
      dist = MathMax(dist, atr * m_cfg.minSLAtrMult);
      dist = MathMin(dist, atr * m_cfg.maxSLAtrMult);
      return(dist);
     }

   //--- full SMC pipeline for this symbol.
   //--- `force` re-analyses even without a new bar (Tier-1 behaviour).
   //--- Returns true and fills `out` when EVERY step passes AND the
   //--- confidence score clears `threshold`.
   bool              Analyse(const bool force, CScoringEngine &scoring,
                             const double threshold, SSignalCandidate &out)
     {
      out.valid = false;
      bool newBar = IsNewBar();
      if(!newBar && !force)
         return(false);
      RefreshStructures();

      //--- spread gate (per symbol)
      long spread = SymbolInfoInteger(m_symbol, SYMBOL_SPREAD);
      if(spread > m_cfg.maxSpreadPoints)
        { m_status = StringFormat("spread %d pts too high", (int)spread); return(false); }

      //--- STEP 1: structure direction (D1/H4/H1) + weekly bias
      int dir = scoring.DecideDirection(m_stD1, m_stH4, m_stH1);
      if(dir == 0 && m_cfg.allowCounterTrend && m_stH1.recentCHoCH && m_stH1.bias != 0)
         dir = m_stH1.bias;
      if(dir == 0)
        { m_status = "structure unclear / HTF conflict"; return(false); }
      if(m_cfg.reqWeeklyBias)
        {
         int w = (m_stW1.trend == MS_UPTREND) ? 1 : (m_stW1.trend == MS_DOWNTREND) ? -1 : 0;
         if(w != 0 && w != dir)
           { m_status = "weekly bias opposing"; return(false); }
        }
      bool isBuy = (dir > 0);

      //--- STEP 2: liquidity map
      double atr = m_indEntry.Atr(1);
      SSmcAnalysis smc;
      if(!m_smc.Scan(m_stEntry, atr == EMPTY_VALUE ? 0.0 : atr, smc))
        { m_status = "insufficient data for SMC scan"; return(false); }
      if(m_cfg.reqLiquidityTarget && !(isBuy ? smc.bslAbovePrice : smc.sslBelowPrice))
        { m_status = "no liquidity pool in profit direction"; return(false); }

      //--- STEP 3+4: BOS / CHoCH
      if(m_cfg.reqBosChoch)
        {
         bool broke = (m_stEntry.bias == dir && (m_stEntry.recentBOS || m_stEntry.recentCHoCH));
         if(!broke) { m_status = "no BOS/CHoCH in direction"; return(false); }
        }

      //--- STEP 5: order block
      SOrderBlock ob;
      if(isBuy) ob = smc.obBull; else ob = smc.obBear;
      if(m_cfg.reqOrderBlock)
        {
         if(!ob.valid) { m_status = "no valid order block"; return(false); }
         if(ob.quality < m_cfg.minOBQuality)
           { m_status = StringFormat("OB quality %.0f too low", ob.quality); return(false); }
        }

      //--- STEP 6: FVG
      if(m_cfg.reqFVG && !(isBuy ? smc.fvgBull : smc.fvgBear))
        { m_status = "no FVG in direction"; return(false); }

      //--- STEP 7: liquidity sweep must have occurred
      if(m_cfg.reqSweep && !(isBuy ? smc.sweepBull : smc.sweepBear))
        { m_status = "no liquidity sweep yet"; return(false); }

      //--- STEP 8: premium / discount (+ OTE when required)
      if(m_cfg.reqPremiumDiscount)
        {
         if(isBuy && smc.rangePos > m_cfg.discountMax)
           { m_status = StringFormat("price in premium (%.2f)", smc.rangePos); return(false); }
         if(!isBuy && smc.rangePos < 1.0 - m_cfg.discountMax)
           { m_status = StringFormat("price in discount (%.2f)", smc.rangePos); return(false); }
        }
      bool ote = (isBuy) ? (smc.rangePos >= 0.21 && smc.rangePos <= 0.38)
                         : (smc.rangePos >= 0.62 && smc.rangePos <= 0.79);
      if(m_cfg.reqOTE && !ote)
        { m_status = "not in OTE zone"; return(false); }

      //--- STEP 9: mitigation
      if(m_cfg.reqMitigation)
        {
         bool mitigating = (ob.valid && ob.mitigating) ||
                           (isBuy ? smc.fvgBullMitigated : smc.fvgBearMitigated);
         if(!mitigating) { m_status = "not mitigating OB/FVG yet"; return(false); }
        }

      //--- STEP 10: confirmation (lower TFs must not oppose)
      if(m_cfg.reqTrendConfirm)
        {
         if(m_indM30.TrendDirection() == -dir || m_indM15.TrendDirection() == -dir)
           { m_status = "lower timeframe opposing"; return(false); }
        }

      //--- SL / TP plan
      double refPrice = isBuy ? SymbolInfoDouble(m_symbol, SYMBOL_ASK)
                              : SymbolInfoDouble(m_symbol, SYMBOL_BID);
      double slDist = ComputeSLDistance(dir, refPrice);
      if(slDist <= 0.0) { m_status = "cannot compute SL"; return(false); }

      //--- confidence score
      SScoreContext ctx;
      ctx.smc       = smc;
      ctx.plannedRR = m_cfg.tp2R;
      ctx.session   = "";
      SSignal sig;
      scoring.Evaluate(dir, m_stD1, m_stH4, m_stH1, m_stEntry,
                       m_indEntry, m_indH1, ctx, sig);
      if(sig.total < threshold)
        { m_status = StringFormat("score %.1f < %.1f", sig.total, threshold); return(false); }

      //--- build the candidate ---------------------------------------
      int digits = (int)SymbolInfoInteger(m_symbol, SYMBOL_DIGITS);
      out.valid  = true;
      out.symbol = m_symbol;
      out.tier   = m_tier;
      out.dir    = dir;
      out.entry  = refPrice;
      //--- entry zone: the order block when usable, else price +/- 0.2 ATR
      if(ob.valid && ob.mitigating)
        { out.entryLow = ob.bottom; out.entryHigh = ob.top; }
      else
        {
         double z = (atr == EMPTY_VALUE ? 0.0 : atr) * 0.2;
         out.entryLow = refPrice - z; out.entryHigh = refPrice + z;
        }
      out.entryLow  = NormalizeDouble(out.entryLow, digits);
      out.entryHigh = NormalizeDouble(out.entryHigh, digits);
      out.sl  = NormalizeDouble(isBuy ? refPrice - slDist : refPrice + slDist, digits);
      out.tp1 = NormalizeDouble(isBuy ? refPrice + slDist * m_cfg.tp1R : refPrice - slDist * m_cfg.tp1R, digits);
      out.tp2 = NormalizeDouble(isBuy ? refPrice + slDist * m_cfg.tp2R : refPrice - slDist * m_cfg.tp2R, digits);
      out.tp3 = NormalizeDouble(isBuy ? refPrice + slDist * m_cfg.tp3R : refPrice - slDist * m_cfg.tp3R, digits);
      out.rr  = m_cfg.tp2R;
      out.score = sig.total;

      string tfName = EnumToString(m_cfg.entryTF);
      StringReplace(tfName, "PERIOD_", "");
      out.tf = tfName;

      //--- reasons (per the required notification format)
      string reasons = "";
      reasons += "• Market Structure: " + BiasSummary() + "\n";
      if(m_stEntry.recentBOS)   reasons += "• BOS ✔\n";
      if(m_stEntry.recentCHoCH) reasons += "• CHoCH ✔\n";
      if(isBuy ? smc.sweepBull : smc.sweepBear) reasons += "• Liquidity Sweep ✔\n";
      if(ob.valid) reasons += StringFormat("• Order Block ✔ (คุณภาพ %.0f/100)\n", ob.quality);
      if(isBuy ? smc.fvgBull : smc.fvgBear)
        {
         reasons += "• Fair Value Gap ✔";
         if(isBuy ? smc.fvgBullMitigated : smc.fvgBearMitigated) reasons += " (mitigated)";
         reasons += "\n";
        }
      reasons += StringFormat("• %s Zone ✔ (rangePos %.2f)%s\n",
                              isBuy ? "Discount" : "Premium", smc.rangePos,
                              ote ? " | OTE ✔" : "");
      reasons += StringFormat("• Regime: %s / %s",
                              CRegimeDetector::RegimeName(m_regime.regime),
                              CRegimeDetector::VolName(m_regime.vol));
      out.reasons = reasons;

      //--- standard cautionary notes
      string notes = "";
      notes += "• รอแท่งเทียน " + tfName + " ปิดยืนยันก่อนเข้า\n";
      notes += StringFormat("• ยกเลิกสัญญาณหากราคาปิดเลย SL (%s) ก่อนเข้าไม้\n",
                            DoubleToString(out.sl, digits));
      notes += "• หลีกเลี่ยงการเข้าใกล้ช่วงประกาศข่าวสำคัญ (ระบบเว้น ±45 นาทีให้แล้ว)";
      out.notes = notes;

      m_status = StringFormat("CANDIDATE %s score %.1f", isBuy ? "BUY" : "SELL", sig.total);
      return(true);
     }
  };

#endif // CG_SYMBOL_ANALYST_MQH
