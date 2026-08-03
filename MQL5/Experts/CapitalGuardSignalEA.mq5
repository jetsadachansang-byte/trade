//+------------------------------------------------------------------+
//|                                       CapitalGuardSignalEA.mq5   |
//|  CapitalGuard Signal - SMC/ICT market analyst -> LINE OA         |
//|                                                                  |
//|  This EA NEVER opens orders. It works as an institutional-style  |
//|  market analyst running 24h while the market is open:            |
//|   - analyses every tick, every timeframe (W1 D1 H4 H1 M30 M15)   |
//|   - entry analysis on M5/M1                                      |
//|   - SMC core: structure, liquidity, BOS/CHoCH/MSS, order block,  |
//|     FVG, premium/discount, mitigation                            |
//|   - ICT layer: weekly/daily bias, kill zones, OTE, session       |
//|     analysis (PO3 is expressed as sweep->BOS sequence; SMT       |
//|     divergence proxied by the optional DXY filter)               |
//|   - indicators (EMA/VWAP/ATR/ADX/RSI/MACD/Volume) confirm only   |
//|   - signals sent to LINE OA when confidence >= 90 ONLY           |
//|   - tracks TP1/TP2/TP3/SL and cancellation, notifying via LINE   |
//|   - on-chart + mobile (HTML) dashboard, CSV/JSONL logging        |
//|                                                                  |
//|  No signal for days is normal behaviour: quality over quantity.  |
//+------------------------------------------------------------------+
#property copyright "CapitalGuard"
#property version   "1.00"

#include <CapitalGuard\IndicatorSet.mqh>
#include <CapitalGuard\MarketStructure.mqh>
#include <CapitalGuard\SmartMoney.mqh>
#include <CapitalGuard\Regime.mqh>
#include <CapitalGuard\NewsFilter.mqh>
#include <CapitalGuard\ScoringEngine.mqh>
#include <CapitalGuard\LineNotify.mqh>
#include <CapitalGuard\SignalManager.mqh>

//--- Inputs: General ----------------------------------------------------
input group             "=== General ==="
input long              InpMagic            = 20260804;         // Instance id (log file names)
input ENUM_TIMEFRAMES   InpEntryTF          = PERIOD_M5;        // Entry analysis timeframe (M1/M5)
input int               InpMaxSpreadPoints  = 45;               // Max spread to signal (points)

//--- Inputs: LINE Official Account --------------------------------------
input group             "=== LINE OA ==="
input bool              InpLineEnabled      = true;             // Send messages to LINE OA
input string            InpLineToken        = "";               // Channel access token (Messaging API)
input string            InpLineUserId       = "";               // Target userId (empty = broadcast)

//--- Inputs: Signal issuing ---------------------------------------------
input group             "=== Signals ==="
input double            InpScoreThreshold   = 90.0;             // Min confidence score (0-100)
input int               InpMaxSignalsPerDay = 3;                // Max signals per day
input int               InpCooldownMinutes  = 60;               // Min minutes between signals
input int               InpSignalExpiryHrs  = 12;               // Cancel if TP1 not reached (hours)
input double            InpTP1R             = 1.0;              // TP1 distance (R multiples)
input double            InpTP2R             = 2.0;              // TP2 distance (R multiples)
input double            InpTP3R             = 3.0;              // TP3 distance (R multiples)

//--- Inputs: SMC score weights ------------------------------------------
input group             "=== SMC Confidence Score ==="
input double            InpWeightStructure  = 25.0;             // Weight: Market Structure
input double            InpWeightLiquidity  = 20.0;             // Weight: Liquidity
input double            InpWeightBosChoch   = 20.0;             // Weight: BOS / CHoCH
input double            InpWeightOB         = 15.0;             // Weight: Order Block
input double            InpWeightFVG        = 10.0;             // Weight: Fair Value Gap
input double            InpWeightVolume     = 5.0;              // Weight: Volume
input double            InpWeightIndicator  = 5.0;              // Weight: Indicator confirmation

//--- Inputs: SMC pipeline (sequential hard gates) -----------------------
input group             "=== SMC Pipeline ==="
input bool              InpAllowCounterTrend = false;           // Allow counter-trend on clear CHoCH
input bool              InpReqLiquidityTarget = false;          // Require BSL/SSL pool in profit direction
input bool              InpReqBosChoch      = true;             // Require BOS or CHoCH in direction
input bool              InpReqOrderBlock    = true;             // Require quality order block
input double            InpMinOBQuality     = 60.0;             // Min order block quality (0-100)
input bool              InpReqFVG           = true;             // Require fair value gap
input bool              InpReqSweep         = true;             // Require liquidity sweep first
input bool              InpReqPremiumDiscount = true;           // Buy discount / sell premium only
input double            InpDiscountMax      = 0.50;             // Discount zone = rangePos <= this
input bool              InpReqMitigation    = true;             // Require price mitigating OB or FVG
input bool              InpReqTrendConfirm  = true;             // M30/M15 must not oppose direction

//--- Inputs: ICT layer --------------------------------------------------
input group             "=== ICT ==="
input bool              InpReqWeeklyBias    = true;             // Weekly structure must not oppose
input bool              InpUseKillZones     = true;             // Signal only inside kill zones
input int               InpLondonKZStart    = 9;                // London KZ start hour (server)
input int               InpLondonKZEnd      = 12;               // London KZ end hour (server)
input int               InpNYKZStart        = 15;               // New York KZ start hour (server)
input int               InpNYKZEnd          = 18;               // New York KZ end hour (server)
input bool              InpReqOTE           = false;            // Require OTE zone (0.62-0.79 pullback)

//--- Inputs: Indicators (confirmation only) -----------------------------
input group             "=== Indicators (confirmation only) ==="
input int               InpEmaFast          = 20;               // EMA fast
input int               InpEmaMid           = 50;               // EMA mid
input int               InpEmaSlow          = 200;              // EMA slow
input int               InpRsiPeriod        = 14;               // RSI period
input int               InpAtrPeriod        = 14;               // ATR period
input int               InpAdxPeriod        = 14;               // ADX period
input int               InpBBPeriod         = 20;               // Bollinger period
input double            InpBBDev            = 2.0;              // Bollinger deviation

//--- Inputs: Structure / SMC detection ----------------------------------
input group             "=== Structure Detection ==="
input int               InpSwingBars        = 3;                // Swing confirmation bars
input int               InpStructLookback   = 80;               // Structure scan lookback (bars)
input int               InpSmcWindow        = 30;               // Smart-money pattern window (bars)

//--- Inputs: Regime / SL sizing -----------------------------------------
input group             "=== Regime & SL ==="
input double            InpAdxTrendMin      = 23.0;             // ADX >= this = trending
input double            InpHighVolRatio     = 1.4;              // ATR ratio for high volatility
input double            InpLowVolRatio      = 0.65;             // ATR ratio for low volatility
input int               InpAtrAvgBars       = 100;              // ATR baseline window (bars)
input double            InpAtrMultSL        = 1.5;              // ATR multiplier for SL fallback
input double            InpMaxSLAtrMult     = 2.5;              // Max SL distance (x ATR)
input double            InpMinSLAtrMult     = 0.8;              // Min SL distance (x ATR)

//--- Inputs: Sessions (server time hours) -------------------------------
input group             "=== Sessions ==="
input bool              InpTradeAsian       = false;            // Analyse Asian session
input int               InpAsianStart       = 1;                // Asian start hour
input int               InpAsianEnd         = 9;                // Asian end hour
input bool              InpTradeLondon      = true;             // Analyse London session
input int               InpLondonStart      = 10;               // London start hour
input int               InpLondonEnd        = 18;               // London end hour
input bool              InpTradeNewYork     = true;             // Analyse New York session
input int               InpNYStart          = 15;               // New York start hour
input int               InpNYEnd            = 23;               // New York end hour

//--- Inputs: News filter ------------------------------------------------
input group             "=== News Filter ==="
input bool              InpNewsEnabled      = true;             // Enable news filter
input int               InpNewsPreMin       = 45;               // Block before news (minutes)
input int               InpNewsPostMin      = 45;               // Block after news (minutes)
input string            InpNewsCurrencies   = "USD";            // Currencies to watch (comma list)
input string            InpNewsManualTimes  = "";               // Manual blocks "yyyy.mm.dd hh:mi;..."

//--- Inputs: External correlation (SMT proxy, optional) -----------------
input group             "=== Correlation / SMT (optional) ==="
input bool              InpUseDxyFilter     = false;            // Veto when DXY trends WITH direction
input string            InpDxySymbol        = "";               // Broker's dollar-index symbol

//--- Inputs: Dashboard --------------------------------------------------
input group             "=== Dashboard ==="
input bool              InpShowDashboard    = true;             // Show on-chart dashboard
input int               InpDashboardSecs    = 3;                // Chart dashboard refresh (seconds)
input bool              InpWriteWebDash     = true;             // Write mobile HTML dashboard file
input int               InpWebDashSecs      = 60;               // HTML dashboard refresh (seconds)

//--- Module instances ---------------------------------------------------
CIndicatorSet     indH1, indM30, indM15, indEntry;
CMarketStructure  structW1, structD1, structH4, structH1, structEntry;
CSmartMoney       smartMoney;
CRegimeDetector   regimeDetector;
CNewsFilter       news;
CScoringEngine    scoring;
CLineNotify       line;
CSignalManager    signalMgr;

//--- Runtime state ------------------------------------------------------
datetime          g_lastBarTime   = 0;
datetime          g_lastSignalAt  = 0;
datetime          g_lastDashAt    = 0;
datetime          g_lastWebDashAt = 0;
SSignal           g_lastEval;
SRegimeInfo       g_regime;
SStructureInfo    g_stW1, g_stD1, g_stH4, g_stH1, g_stEntry;
string            g_newsStatus  = "";
string            g_status      = "starting";
string            g_lastSkipMsg = "";

//+------------------------------------------------------------------+
//| Expert initialization                                            |
//+------------------------------------------------------------------+
int OnInit()
  {
   bool ok = true;
   ok &= indH1.Init(_Symbol, PERIOD_H1, InpEmaFast, InpEmaMid, InpEmaSlow, InpRsiPeriod, InpAtrPeriod, InpAdxPeriod, InpBBPeriod, InpBBDev);
   ok &= indM30.Init(_Symbol, PERIOD_M30, InpEmaFast, InpEmaMid, InpEmaSlow, InpRsiPeriod, InpAtrPeriod, InpAdxPeriod, InpBBPeriod, InpBBDev);
   ok &= indM15.Init(_Symbol, PERIOD_M15, InpEmaFast, InpEmaMid, InpEmaSlow, InpRsiPeriod, InpAtrPeriod, InpAdxPeriod, InpBBPeriod, InpBBDev);
   ok &= indEntry.Init(_Symbol, InpEntryTF, InpEmaFast, InpEmaMid, InpEmaSlow, InpRsiPeriod, InpAtrPeriod, InpAdxPeriod, InpBBPeriod, InpBBDev);
   if(!ok)
     {
      Print("CapitalGuard Signal: failed to create indicator handles");
      return(INIT_FAILED);
     }

   //--- structure on every analysed timeframe (Weekly down to entry)
   structW1.Init(_Symbol, PERIOD_W1, InpSwingBars, InpStructLookback);
   structD1.Init(_Symbol, PERIOD_D1, InpSwingBars, InpStructLookback);
   structH4.Init(_Symbol, PERIOD_H4, InpSwingBars, InpStructLookback);
   structH1.Init(_Symbol, PERIOD_H1, InpSwingBars, InpStructLookback);
   structEntry.Init(_Symbol, InpEntryTF, InpSwingBars, InpStructLookback);

   smartMoney.Init(_Symbol, InpEntryTF, InpSmcWindow);
   regimeDetector.Init(InpAdxTrendMin, InpHighVolRatio, InpLowVolRatio, InpAtrAvgBars);
   news.Init(InpNewsEnabled, InpNewsPreMin, InpNewsPostMin, InpNewsCurrencies, InpNewsManualTimes);
   scoring.Init(InpWeightStructure, InpWeightLiquidity, InpWeightBosChoch,
                InpWeightOB, InpWeightFVG, InpWeightVolume, InpWeightIndicator);
   line.Init(InpLineEnabled, InpLineToken, InpLineUserId);
   signalMgr.Init(GetPointer(line), _Symbol, InpMagic, InpSignalExpiryHrs);

   if(InpUseDxyFilter && InpDxySymbol != "")
      SymbolSelect(InpDxySymbol, true);

   g_lastEval.direction = 0;
   g_lastEval.total     = 0.0;
   g_stEntry.bias       = 0;
   g_stEntry.recentBOS  = false;
   g_stEntry.recentCHoCH = false;

   line.Push("🤖 CapitalGuard Signal เริ่มทำงาน\n" + _Symbol + " | เฝ้าตลาดตลอดเวลา ส่งเฉพาะสัญญาณคะแนน >= " +
             DoubleToString(InpScoreThreshold, 0));
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Expert deinitialization                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   indH1.Release(); indM30.Release(); indM15.Release(); indEntry.Release();
   Comment("");
  }

//+------------------------------------------------------------------+
//| Expert tick: monitor signals + analyse for new setups            |
//+------------------------------------------------------------------+
void OnTick()
  {
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   //--- 1) tick-level lifecycle of live signals (TP/SL/cancel)
   signalMgr.Monitor(bid, ask, g_stEntry);

   //--- 2) dashboards (throttled)
   regimeDetector.Detect(indH1, g_regime);
   if(InpShowDashboard && TimeCurrent() - g_lastDashAt >= InpDashboardSecs)
     {
      g_lastDashAt = TimeCurrent();
      DrawChartDashboard();
     }
   if(InpWriteWebDash && TimeCurrent() - g_lastWebDashAt >= InpWebDashSecs)
     {
      g_lastWebDashAt = TimeCurrent();
      WriteWebDashboard();
     }

   //--- 3) full SMC analysis once per closed entry-TF bar
   if(!IsNewBar())
      return;
   structW1.Scan(g_stW1);
   structD1.Scan(g_stD1);
   structH4.Scan(g_stH4);
   structH1.Scan(g_stH1);
   structEntry.Scan(g_stEntry);
   TryAnalyse();
  }

//+------------------------------------------------------------------+
//| New bar detection on the entry timeframe                         |
//+------------------------------------------------------------------+
bool IsNewBar()
  {
   datetime t = iTime(_Symbol, InpEntryTF, 0);
   if(t == g_lastBarTime || t == 0)
      return(false);
   g_lastBarTime = t;
   return(true);
  }

//+------------------------------------------------------------------+
//| Session name; "Overlap" when both London and NY are active       |
//+------------------------------------------------------------------+
string CurrentSessionName()
  {
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   int h = dt.hour;
   bool london = (InpTradeLondon  && h >= InpLondonStart && h < InpLondonEnd);
   bool ny     = (InpTradeNewYork && h >= InpNYStart     && h < InpNYEnd);
   if(london && ny) return("Overlap");
   if(london)       return("London");
   if(ny)           return("NewYork");
   if(InpTradeAsian && h >= InpAsianStart && h < InpAsianEnd) return("Asian");
   return("");
  }

//+------------------------------------------------------------------+
//| ICT kill zone check (server hours)                               |
//+------------------------------------------------------------------+
bool InKillZone()
  {
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   int h = dt.hour;
   if(h >= InpLondonKZStart && h < InpLondonKZEnd) return(true);
   if(h >= InpNYKZStart && h < InpNYKZEnd)         return(true);
   return(false);
  }

//+------------------------------------------------------------------+
//| ICT OTE: price pulled back 62-79% of the last swing leg          |
//+------------------------------------------------------------------+
bool InOTEZone(const int dir, const double rangePos)
  {
   //--- rangePos 0=swing low, 1=swing high. A 62-79% pullback of an
   //--- up-leg puts price at 0.21-0.38 of the range (discount OTE).
   if(dir > 0)  return(rangePos >= 0.21 && rangePos <= 0.38);
   return(rangePos >= 0.62 && rangePos <= 0.79);
  }

//+------------------------------------------------------------------+
//| Simple external-symbol trend (SMT/DXY proxy)                     |
//+------------------------------------------------------------------+
int ExternalTrendDir(const string sym)
  {
   double closes[];
   if(CopyClose(sym, PERIOD_H1, 1, 50, closes) < 50)
      return(0);
   double smaFast = 0.0, smaSlow = 0.0;
   for(int i = 30; i < 50; i++) smaFast += closes[i];
   smaFast /= 20.0;
   for(int i = 0; i < 50; i++) smaSlow += closes[i];
   smaSlow /= 50.0;
   double last = closes[49];
   if(last > smaFast && smaFast > smaSlow) return(1);
   if(last < smaFast && smaFast < smaSlow) return(-1);
   return(0);
  }

//+------------------------------------------------------------------+
//| SL distance from structure swings + ATR bounds                   |
//+------------------------------------------------------------------+
double ComputeSLDistance(const int direction, const double refPrice)
  {
   double atr = indEntry.Atr(1);
   if(atr == EMPTY_VALUE || atr <= 0.0)
      return(0.0);
   double buffer  = atr * 0.3;
   double slLevel = (direction > 0) ? structEntry.BuyStopLevel(g_stEntry, buffer)
                                    : structEntry.SellStopLevel(g_stEntry, buffer);
   double dist = 0.0;
   if(slLevel > 0.0)
      dist = (direction > 0) ? (refPrice - slLevel) : (slLevel - refPrice);
   if(dist <= 0.0)
      dist = atr * InpAtrMultSL;
   dist = MathMax(dist, atr * InpMinSLAtrMult);
   dist = MathMin(dist, atr * InpMaxSLAtrMult);
   return(dist);
  }

//+------------------------------------------------------------------+
//| Record a failed analysis stage (status + de-duplicated)          |
//+------------------------------------------------------------------+
void FailStage(const string what)
  {
   g_status = "waiting: " + what;
   g_lastSkipMsg = what;
  }

//+------------------------------------------------------------------+
//| Full analysis: admin gates -> SMC pipeline -> ICT -> signal      |
//+------------------------------------------------------------------+
void TryAnalyse()
  {
   //--- administrative gates -----------------------------------------
   string session = CurrentSessionName();
   if(session == "")
     { g_status = "outside analysed sessions"; return; }
   if(InpUseKillZones && !InKillZone())
     { g_status = "waiting for kill zone"; return; }
   if(news.IsBlocked(g_newsStatus))
     { g_status = "news pause: " + g_newsStatus; return; }
   g_newsStatus = "";
   long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(spread > InpMaxSpreadPoints)
     { g_status = StringFormat("spread too high (%d pts)", (int)spread); return; }
   if(signalMgr.HasActiveSignal())
     { g_status = "signal already active - tracking it"; return; }
   datetime dayStart = iTime(_Symbol, PERIOD_D1, 0);
   if(InpMaxSignalsPerDay > 0 && signalMgr.SignalsSince(dayStart) >= InpMaxSignalsPerDay)
     { g_status = "daily signal cap reached"; return; }
   if(g_lastSignalAt > 0 && TimeCurrent() - g_lastSignalAt < (long)InpCooldownMinutes * 60)
     { g_status = "cooldown between signals"; return; }

   //--- STEP 1: Market Structure + Weekly/Daily bias -----------------
   int dir = scoring.DecideDirection(g_stD1, g_stH4, g_stH1);
   if(dir == 0 && InpAllowCounterTrend && g_stH1.recentCHoCH && g_stH1.bias != 0)
      dir = g_stH1.bias;
   if(dir == 0)
     { g_status = "structure unclear or HTF conflict - waiting"; return; }
   //--- ICT weekly bias must not oppose
   if(InpReqWeeklyBias)
     {
      int w = (g_stW1.trend == MS_UPTREND) ? 1 : (g_stW1.trend == MS_DOWNTREND) ? -1 : 0;
      if(w != 0 && w != dir)
        { FailStage("weekly bias opposing"); return; }
     }
   bool isBuy = (dir > 0);

   //--- STEP 2: Liquidity map ----------------------------------------
   double atr = indEntry.Atr(1);
   SSmcAnalysis smc;
   if(!smartMoney.Scan(g_stEntry, atr == EMPTY_VALUE ? 0.0 : atr, smc))
     { FailStage("liquidity scan failed"); return; }
   if(InpReqLiquidityTarget && !(isBuy ? smc.bslAbovePrice : smc.sslBelowPrice))
     { FailStage("no liquidity pool in profit direction"); return; }

   //--- STEP 3+4: BOS / CHoCH ----------------------------------------
   if(InpReqBosChoch)
     {
      bool broke = (g_stEntry.bias == dir && (g_stEntry.recentBOS || g_stEntry.recentCHoCH));
      if(!broke) { FailStage("no BOS/CHoCH in direction"); return; }
     }

   //--- STEP 5: Order Block ------------------------------------------
   SOrderBlock ob;
   if(isBuy) ob = smc.obBull; else ob = smc.obBear;
   if(InpReqOrderBlock)
     {
      if(!ob.valid) { FailStage("no valid order block"); return; }
      if(ob.quality < InpMinOBQuality)
        { FailStage(StringFormat("OB quality %.0f < %.0f", ob.quality, InpMinOBQuality)); return; }
     }

   //--- STEP 6: Fair Value Gap ---------------------------------------
   if(InpReqFVG && !(isBuy ? smc.fvgBull : smc.fvgBear))
     { FailStage("no FVG in direction"); return; }

   //--- STEP 7: Liquidity Sweep --------------------------------------
   if(InpReqSweep && !(isBuy ? smc.sweepBull : smc.sweepBear))
     { FailStage("no liquidity sweep yet"); return; }

   //--- STEP 8: Premium / Discount + ICT OTE -------------------------
   if(InpReqPremiumDiscount)
     {
      if(isBuy && smc.rangePos > InpDiscountMax)
        { FailStage(StringFormat("price in premium (%.2f)", smc.rangePos)); return; }
      if(!isBuy && smc.rangePos < 1.0 - InpDiscountMax)
        { FailStage(StringFormat("price in discount (%.2f)", smc.rangePos)); return; }
     }
   bool ote = InOTEZone(dir, smc.rangePos);
   if(InpReqOTE && !ote)
     { FailStage("not in OTE zone (0.62-0.79 pullback)"); return; }

   //--- STEP 9: Mitigation -------------------------------------------
   if(InpReqMitigation)
     {
      bool mitigating = (ob.valid && ob.mitigating) ||
                        (isBuy ? smc.fvgBullMitigated : smc.fvgBearMitigated);
      if(!mitigating) { FailStage("price not mitigating OB/FVG yet"); return; }
     }

   //--- STEP 10: Confirmation ----------------------------------------
   if(InpReqTrendConfirm)
     {
      if(indM30.TrendDirection() == -dir || indM15.TrendDirection() == -dir)
        { FailStage("lower timeframe opposing"); return; }
     }
   if(InpUseDxyFilter && InpDxySymbol != "")
     {
      int dxy = ExternalTrendDir(InpDxySymbol);
      if(dxy != 0 && dxy == dir)
        { FailStage("SMT/DXY veto: dollar trending with direction"); return; }
     }

   //--- SL / TP plan --------------------------------------------------
   double refPrice = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                           : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double slDist = ComputeSLDistance(dir, refPrice);
   if(slDist <= 0.0) { FailStage("cannot compute SL distance"); return; }

   //--- confidence score ---------------------------------------------
   SScoreContext ctx;
   ctx.smc       = smc;
   ctx.plannedRR = InpTP2R;
   ctx.session   = session;
   scoring.Evaluate(dir, g_stD1, g_stH4, g_stH1, g_stEntry, indEntry, indH1, ctx, g_lastEval);
   if(g_lastEval.total < InpScoreThreshold)
     {
      g_status = StringFormat("score %.1f < %.1f - waiting", g_lastEval.total, InpScoreThreshold);
      return;
     }

   //--- build the signal ---------------------------------------------
   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double entry  = refPrice;
   double sl     = isBuy ? NormalizeDouble(entry - slDist, digits)
                         : NormalizeDouble(entry + slDist, digits);
   double tp1    = isBuy ? NormalizeDouble(entry + slDist * InpTP1R, digits)
                         : NormalizeDouble(entry - slDist * InpTP1R, digits);
   double tp2    = isBuy ? NormalizeDouble(entry + slDist * InpTP2R, digits)
                         : NormalizeDouble(entry - slDist * InpTP2R, digits);
   double tp3    = isBuy ? NormalizeDouble(entry + slDist * InpTP3R, digits)
                         : NormalizeDouble(entry - slDist * InpTP3R, digits);

   //--- reasons exactly per the required format + ICT extras
   string reasons = "";
   if(g_stEntry.recentBOS)    reasons += "• BOS ✔\n";
   if(g_stEntry.recentCHoCH)  reasons += "• CHoCH ✔\n";
   if(isBuy ? smc.sweepBull : smc.sweepBear)   reasons += "• Liquidity Sweep ✔\n";
   if(ob.valid)               reasons += StringFormat("• Order Block ✔ (คุณภาพ %.0f)\n", ob.quality);
   if(isBuy ? smc.fvgBull : smc.fvgBear)       reasons += "• FVG ✔";
   if(isBuy ? smc.fvgBullMitigated : smc.fvgBearMitigated) reasons += " (mitigated)";
   reasons += "\n";
   reasons += "• Trend Confirmation ✔ (W1/D1/H4/H1 aligned)\n";
   reasons += StringFormat("• %s ✔ | Kill Zone %s | rangePos %.2f",
                           isBuy ? "Discount Zone" : "Premium Zone",
                           InKillZone() ? "✔" : "-", smc.rangePos);
   if(ote) reasons += " | OTE ✔";

   string tfName = EnumToString(InpEntryTF);
   StringReplace(tfName, "PERIOD_", "");

   signalMgr.NewSignal(dir, entry, sl, tp1, tp2, tp3, InpTP2R,
                       g_lastEval.total, reasons, tfName);
   g_lastSignalAt = TimeCurrent();
   g_status = StringFormat("SIGNAL SENT: %s score %.1f", isBuy ? "BUY" : "SELL", g_lastEval.total);
   Print("CapitalGuard Signal: ", g_status);
  }

//+------------------------------------------------------------------+
//| Trend label helper for dashboards                                |
//+------------------------------------------------------------------+
string TrendLabel(const SStructureInfo &st)
  {
   if(st.trend == MS_UPTREND)   return("UP");
   if(st.trend == MS_DOWNTREND) return("DOWN");
   return("SIDE");
  }

//+------------------------------------------------------------------+
//| On-chart dashboard                                               |
//+------------------------------------------------------------------+
void DrawChartDashboard()
  {
   int wins, losses, cancelled;
   signalMgr.Stats(wins, losses, cancelled);
   double winRate = (wins + losses > 0) ? 100.0 * wins / (wins + losses) : 0.0;
   datetime dayStart = iTime(_Symbol, PERIOD_D1, 0);

   string text = "\n";
   text += "====== CAPITAL GUARD SIGNAL (no auto-trade) ======\n";
   text += StringFormat("  Market: %s | Session: %s | KillZone: %s\n",
                        _Symbol, CurrentSessionName() == "" ? "closed hrs" : CurrentSessionName(),
                        InKillZone() ? "YES" : "no");
   text += StringFormat("  Bias  W1:%s D1:%s H4:%s H1:%s | Regime: %s %s\n",
                        TrendLabel(g_stW1), TrendLabel(g_stD1), TrendLabel(g_stH4), TrendLabel(g_stH1),
                        CRegimeDetector::RegimeName(g_regime.regime),
                        CRegimeDetector::VolName(g_regime.vol));
   text += StringFormat("  News: %s | Spread: %d pts\n",
                        g_newsStatus == "" ? "clear" : g_newsStatus,
                        (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD));
   text += "--------------------------------------------------\n";
   text += StringFormat("  Last score: %.1f (%s)\n", g_lastEval.total,
                        g_lastEval.direction == 0 ? "none" : (g_lastEval.direction > 0 ? "BUY" : "SELL"));
   text += StringFormat("  Signals today: %d | Win rate (session): %.1f%% (W%d/L%d/C%d)\n",
                        signalMgr.SignalsSince(dayStart), winRate, wins, losses, cancelled);
   text += StringFormat("  LINE: %s\n", line.FailCount() == 0 ? "OK" : StringFormat("%d fails", line.FailCount()));
   text += StringFormat("  Status: %s\n", g_status);
   text += "==================================================\n";
   Comment(text);
  }

//+------------------------------------------------------------------+
//| Mobile dashboard: self-refreshing HTML in MQL5/Files/CapitalGuard|
//+------------------------------------------------------------------+
void WriteWebDashboard()
  {
   int wins, losses, cancelled;
   signalMgr.Stats(wins, losses, cancelled);
   double winRate = (wins + losses > 0) ? 100.0 * wins / (wins + losses) : 0.0;
   datetime dayStart = iTime(_Symbol, PERIOD_D1, 0);

   SSignalRecord lastSig;
   bool hasSig = signalMgr.LastSignal(lastSig);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   string lastSigHtml = "-";
   if(hasSig)
      lastSigHtml = StringFormat("%s @ %s | SL %s | TP2 %s | score %.0f | %s",
                                 lastSig.dir > 0 ? "BUY" : "SELL",
                                 DoubleToString(lastSig.entry, digits),
                                 DoubleToString(lastSig.sl, digits),
                                 DoubleToString(lastSig.tp2, digits),
                                 lastSig.score, CSignalManager::StatusName(lastSig.status));

   string html;
   html  = "<meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>";
   html += "<meta http-equiv='refresh' content='60'>";
   html += "<title>CapitalGuard Signal</title>";
   html += "<style>body{font-family:sans-serif;background:#111;color:#eee;margin:1em}"
           "h2{color:#ffd700}table{width:100%;border-collapse:collapse}"
           "td{padding:6px;border-bottom:1px solid #333}td:first-child{color:#999}</style>";
   html += "<h2>CapitalGuard Signal — " + _Symbol + "</h2><table>";
   html += "<tr><td>อัปเดตล่าสุด</td><td>" + TimeToString(TimeCurrent(), TIME_DATE|TIME_MINUTES) + " (server)</td></tr>";
   html += "<tr><td>สถานะตลาด</td><td>" + (CurrentSessionName() == "" ? "นอกช่วงวิเคราะห์" : CurrentSessionName())
         + (InKillZone() ? " (Kill Zone)" : "") + "</td></tr>";
   html += "<tr><td>Trend/Bias</td><td>W1:" + TrendLabel(g_stW1) + " D1:" + TrendLabel(g_stD1)
         + " H4:" + TrendLabel(g_stH4) + " H1:" + TrendLabel(g_stH1) + "</td></tr>";
   html += "<tr><td>Regime</td><td>" + CRegimeDetector::RegimeName(g_regime.regime) + " / "
         + CRegimeDetector::VolName(g_regime.vol) + "</td></tr>";
   html += "<tr><td>ข่าว</td><td>" + (g_newsStatus == "" ? "ไม่มีข่าวแรงขณะนี้" : g_newsStatus) + "</td></tr>";
   html += "<tr><td>ความเสี่ยง (Spread)</td><td>" + IntegerToString(SymbolInfoInteger(_Symbol, SYMBOL_SPREAD)) + " points</td></tr>";
   html += StringFormat("<tr><td>Confidence Score ล่าสุด</td><td>%.1f / 100</td></tr>", g_lastEval.total);
   html += "<tr><td>สัญญาณล่าสุด</td><td>" + lastSigHtml + "</td></tr>";
   html += StringFormat("<tr><td>สัญญาณวันนี้</td><td>%d</td></tr>", signalMgr.SignalsSince(dayStart));
   html += StringFormat("<tr><td>Win Rate (session)</td><td>%.1f%% (W%d / L%d / ยกเลิก %d)</td></tr>",
                        winRate, wins, losses, cancelled);
   html += "<tr><td>สถานะระบบ</td><td>" + g_status + "</td></tr>";
   html += "</table><p style='color:#666'>รีเฟรชอัตโนมัติทุก 60 วินาที · CapitalGuard</p>";

   //--- UTF-8 so Thai text renders correctly in the browser
   int fh = FileOpen("CapitalGuard\\dashboard.html", FILE_WRITE|FILE_TXT|FILE_ANSI, ';', CP_UTF8);
   if(fh != INVALID_HANDLE)
     {
      FileWriteString(fh, html);
      FileClose(fh);
     }
  }
//+------------------------------------------------------------------+
