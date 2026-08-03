//+------------------------------------------------------------------+
//|                                             CapitalGuardEA.mq5   |
//|  CapitalGuard v3 - SMC-first, Capital Preservation trading bot   |
//|                                                                  |
//|  CORE STRATEGY: Smart Money Concepts (SMC).                      |
//|  Indicators NEVER drive entries - they only confirm (5% weight). |
//|                                                                  |
//|  Sequential analysis before every order (any step fails = wait): |
//|   1. Market Structure (D1/H4/H1/entry, HH-HL-LH-LL)              |
//|   2. Liquidity map (equal highs/lows, BSL/SSL pools)             |
//|   3. BOS (Break of Structure)                                    |
//|   4. CHoCH / MSS (Change of Character)                           |
//|   5. Order Block (quality-scored)                                |
//|   6. Fair Value Gap                                              |
//|   7. Liquidity Sweep (must have occurred first)                  |
//|   8. Premium / Discount zone (buy discount, sell premium)        |
//|   9. Mitigation (price returning into OB / FVG)                  |
//|  10. Entry Confirmation (confidence score >= 90)                 |
//|  11. Risk Management (sizing, RR >= 1:2, margin)                 |
//|                                                                  |
//|  Waiting through long periods without a setup is CORRECT         |
//|  behaviour, not a malfunction.                                   |
//|                                                                  |
//|  Hard rules: no Martingale, no Grid, no averaging down, SL is    |
//|  never widened, every order carries SL and TP, daily loss stops  |
//|  trading immediately.                                            |
//+------------------------------------------------------------------+
#property copyright "CapitalGuard"
#property version   "3.00"

#include <Trade\Trade.mqh>
#include <CapitalGuard\RiskManager.mqh>
#include <CapitalGuard\IndicatorSet.mqh>
#include <CapitalGuard\MarketStructure.mqh>
#include <CapitalGuard\SmartMoney.mqh>
#include <CapitalGuard\Regime.mqh>
#include <CapitalGuard\NewsFilter.mqh>
#include <CapitalGuard\ScoringEngine.mqh>
#include <CapitalGuard\TradeManager.mqh>
#include <CapitalGuard\Logger.mqh>
#include <CapitalGuard\Dashboard.mqh>

//--- behaviour after the daily profit target is reached
enum ENUM_AFTER_TARGET
  {
   AFTER_TARGET_STOP,      // Stop trading for the day
   AFTER_TARGET_QUALITY    // Continue: highest-quality setups only
  };

//--- Inputs: General ----------------------------------------------------
input group             "=== General ==="
input long              InpMagic            = 20260803;         // Magic number
input ENUM_TIMEFRAMES   InpEntryTF          = PERIOD_M5;        // Entry timeframe (M1/M5)
input int               InpMaxPositions     = 1;                // Max simultaneous positions
input int               InpSlippagePoints   = 30;               // Max slippage (points)
input int               InpMaxSpreadPoints  = 45;               // Max spread to trade (points)

//--- Inputs: Risk (capital preservation first) --------------------------
input group             "=== Risk Management ==="
input double            InpRiskPerTrade     = 0.75;             // Risk per trade (%) [0.5-1]
input double            InpMaxDailyLoss     = 3.0;              // Max daily loss (%) - stop for the day
input double            InpMaxWeeklyLoss    = 8.0;              // Max weekly loss (%) - stop for the week
input double            InpMaxDrawdown      = 15.0;             // Max drawdown (%) - circuit breaker
input int               InpMaxTradesPerDay  = 2;                // Max trades per day
input double            InpDailyTargetPct   = 2.0;              // Daily profit target (%)
input ENUM_AFTER_TARGET InpAfterTarget      = AFTER_TARGET_STOP; // After target reached
input ENUM_MINLOT_POLICY InpMinLotPolicy    = MINLOT_USE_IF_CAPPED; // When lot < broker minimum
input double            InpHardRiskCap      = 3.0;              // Hard cap per trade with min lot (%)

//--- Inputs: SMC confidence score (weights sum ~100) --------------------
input group             "=== SMC Confidence Score ==="
input double            InpScoreThreshold   = 90.0;             // Min confidence score (0-100)
input double            InpQualityThreshold = 95.0;             // Score after daily target (quality mode)
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
input bool              InpReqTrendConfirm  = true;             // Lower TFs must not oppose direction

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

//--- Inputs: Regime detection (RR adaptation) ---------------------------
input group             "=== Market Regime ==="
input double            InpAdxTrendMin      = 23.0;             // ADX >= this = trending
input double            InpHighVolRatio     = 1.4;              // ATR ratio for high volatility
input double            InpLowVolRatio      = 0.65;             // ATR ratio for low volatility
input int               InpAtrAvgBars       = 100;              // ATR baseline window (bars)

//--- Inputs: Stop Loss / Take Profit ------------------------------------
input group             "=== SL / TP ==="
input double            InpAtrMultSL        = 1.5;              // ATR multiplier for SL fallback
input double            InpMaxSLAtrMult     = 2.5;              // Max SL distance (x ATR)
input double            InpMinSLAtrMult     = 0.8;              // Min SL distance (x ATR)
input double            InpMinRR            = 2.0;              // Minimum Risk:Reward
input double            InpBaseRR           = 2.0;              // Target Risk:Reward
input double            InpStrongTrendRR    = 2.5;              // RR when trend is strong (ADX>=30)

//--- Inputs: Trade management -------------------------------------------
input group             "=== Trade Management ==="
input bool              InpUseBreakEven     = true;             // Move SL to BE at trigger R
input double            InpBETriggerR       = 1.0;              // BE trigger (R multiples)
input int               InpBELockPoints     = 30;               // BE lock-in offset (points)
input bool              InpUsePartial       = true;             // Partial close at first target
input double            InpPartialR         = 1.0;              // Partial close trigger (R)
input double            InpPartialPct       = 0.5;              // Fraction closed (0-1)
input bool              InpUseAtrTrail      = true;             // ATR trailing after BE
input double            InpAtrTrailMult     = 1.2;              // Trailing distance (x ATR)
input int               InpMaxHoldHours     = 48;               // Time exit after N hours (0=off)
input double            InpTimeExitMinR     = 0.3;              // Time-exit only if profit < this R
input bool              InpUseEmergencyExit = true;             // Close on structure flip (CHoCH)

//--- Inputs: Sessions (server time hours) -------------------------------
input group             "=== Sessions ==="
input bool              InpTradeAsian       = false;            // Trade Asian session
input int               InpAsianStart       = 1;                // Asian start hour
input int               InpAsianEnd         = 9;                // Asian end hour
input bool              InpTradeLondon      = true;             // Trade London session
input int               InpLondonStart      = 10;               // London start hour
input int               InpLondonEnd        = 18;               // London end hour
input bool              InpTradeNewYork     = true;             // Trade New York session
input int               InpNYStart          = 15;               // New York start hour
input int               InpNYEnd            = 23;               // New York end hour

//--- Inputs: News filter ------------------------------------------------
input group             "=== News Filter ==="
input bool              InpNewsEnabled      = true;             // Enable news filter
input int               InpNewsPreMin       = 45;               // Hard block before news (minutes)
input int               InpNewsPostMin      = 45;               // Hard block after news (minutes)
input string            InpNewsCurrencies   = "USD";            // Currencies to watch (comma list)
input string            InpNewsManualTimes  = "";               // Manual blocks "yyyy.mm.dd hh:mi;..."

//--- Inputs: External correlation (optional) ----------------------------
input group             "=== Correlation (optional) ==="
input bool              InpUseDxyFilter     = false;            // Veto when DXY trends WITH gold direction
input string            InpDxySymbol        = "";               // Broker's dollar-index symbol (e.g. USDX)

//--- Inputs: Dashboard --------------------------------------------------
input group             "=== Dashboard ==="
input bool              InpShowDashboard    = true;             // Show on-chart dashboard
input int               InpDashboardSecs    = 3;                // Dashboard refresh (seconds)

//--- Module instances ---------------------------------------------------
CTrade            trade;
CRiskManager      risk;
CIndicatorSet     indH1, indM30, indM15, indEntry;
CMarketStructure  structD1, structH4, structH1, structEntry;
CSmartMoney       smartMoney;
CRegimeDetector   regimeDetector;
CNewsFilter       news;
CScoringEngine    scoring;
CTradeManager     tradeMgr;
CTradeLogger      logger;
CDashboard        dashboard;

//--- Runtime state ------------------------------------------------------
datetime          g_lastBarTime = 0;
SSignal           g_lastSignal;
SRegimeInfo       g_regime;
SStructureInfo    g_stD1, g_stH4, g_stH1, g_stEntry;
string            g_newsStatus    = "";
string            g_tradingStatus = "starting";
string            g_lastSkipMsg   = "";     // avoid flooding the skip log

//+------------------------------------------------------------------+
//| Expert initialization                                            |
//+------------------------------------------------------------------+
int OnInit()
  {
   //--- trade executor
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);

   //--- indicator bundles (confirmation + volume + regime + ATR)
   bool ok = true;
   ok &= indH1.Init(_Symbol, PERIOD_H1, InpEmaFast, InpEmaMid, InpEmaSlow, InpRsiPeriod, InpAtrPeriod, InpAdxPeriod, InpBBPeriod, InpBBDev);
   ok &= indM30.Init(_Symbol, PERIOD_M30, InpEmaFast, InpEmaMid, InpEmaSlow, InpRsiPeriod, InpAtrPeriod, InpAdxPeriod, InpBBPeriod, InpBBDev);
   ok &= indM15.Init(_Symbol, PERIOD_M15, InpEmaFast, InpEmaMid, InpEmaSlow, InpRsiPeriod, InpAtrPeriod, InpAdxPeriod, InpBBPeriod, InpBBDev);
   ok &= indEntry.Init(_Symbol, InpEntryTF, InpEmaFast, InpEmaMid, InpEmaSlow, InpRsiPeriod, InpAtrPeriod, InpAdxPeriod, InpBBPeriod, InpBBDev);
   if(!ok)
     {
      Print("CapitalGuard: failed to create indicator handles");
      return(INIT_FAILED);
     }

   //--- structure scanners: SMC reads structure on every key timeframe
   structD1.Init(_Symbol, PERIOD_D1, InpSwingBars, InpStructLookback);
   structH4.Init(_Symbol, PERIOD_H4, InpSwingBars, InpStructLookback);
   structH1.Init(_Symbol, PERIOD_H1, InpSwingBars, InpStructLookback);
   structEntry.Init(_Symbol, InpEntryTF, InpSwingBars, InpStructLookback);

   //--- modules
   risk.Init(_Symbol, InpMagic, InpRiskPerTrade, InpMaxDailyLoss, InpMaxWeeklyLoss,
             InpMaxDrawdown, InpMaxTradesPerDay, InpDailyTargetPct,
             InpMinLotPolicy, InpHardRiskCap);
   smartMoney.Init(_Symbol, InpEntryTF, InpSmcWindow);
   regimeDetector.Init(InpAdxTrendMin, InpHighVolRatio, InpLowVolRatio, InpAtrAvgBars);
   news.Init(InpNewsEnabled, InpNewsPreMin, InpNewsPostMin, InpNewsCurrencies, InpNewsManualTimes);
   scoring.Init(InpWeightStructure, InpWeightLiquidity, InpWeightBosChoch,
                InpWeightOB, InpWeightFVG, InpWeightVolume, InpWeightIndicator);
   tradeMgr.Init(GetPointer(trade), _Symbol, InpMagic,
                 InpUseBreakEven, InpBETriggerR, InpBELockPoints,
                 InpUsePartial, InpPartialR, InpPartialPct,
                 InpUseAtrTrail, InpAtrTrailMult,
                 InpMaxHoldHours, InpTimeExitMinR, InpUseEmergencyExit);
   logger.Init(InpMagic);
   dashboard.Init(_Symbol, InpMagic, InpDashboardSecs);

   //--- sanity checks on inputs
   if(InpMinRR < 1.0 || InpBaseRR < InpMinRR || InpStrongTrendRR < InpBaseRR)
     {
      Print("CapitalGuard: invalid RR configuration");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpRiskPerTrade <= 0.0 || InpRiskPerTrade > 2.0)
     {
      Print("CapitalGuard: risk per trade must be between 0 and 2 percent");
      return(INIT_PARAMETERS_INCORRECT);
     }

   //--- make sure the correlation symbol is subscribed when requested
   if(InpUseDxyFilter && InpDxySymbol != "")
      SymbolSelect(InpDxySymbol, true);

   g_lastSignal.direction = 0;
   g_lastSignal.total     = 0.0;
   g_stEntry.bias         = 0;
   g_stEntry.recentBOS    = false;
   g_stEntry.recentCHoCH  = false;
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Expert deinitialization                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   indH1.Release(); indM30.Release(); indM15.Release(); indEntry.Release();
   dashboard.Clear();
  }

//+------------------------------------------------------------------+
//| Expert tick: analyse always, trade rarely                        |
//+------------------------------------------------------------------+
void OnTick()
  {
   //--- keep risk day/week snapshots fresh
   risk.UpdateRollover();

   //--- manage open positions every tick (emergency / BE / partial /
   //--- trail / time exit) using the latest entry-TF structure
   tradeMgr.Manage(indEntry, g_stEntry);

   //--- refresh regime + dashboard
   regimeDetector.Detect(indH1, g_regime);
   if(InpShowDashboard)
      dashboard.Update(risk, g_regime, g_lastSignal, g_newsStatus, g_tradingStatus, CurrentSessionName());

   //--- evaluate entries once per closed bar on the entry timeframe
   if(!IsNewBar())
      return;

   //--- refresh every structure snapshot (SMC reads structure first)
   structD1.Scan(g_stD1);
   structH4.Scan(g_stH4);
   structH1.Scan(g_stH1);
   structEntry.Scan(g_stEntry);

   TryEnter();
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
//| Count open positions belonging to this EA                        |
//+------------------------------------------------------------------+
int CountOwnPositions()
  {
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      count++;
     }
   return(count);
  }

//+------------------------------------------------------------------+
//| Simple external-symbol trend (SMA20 vs SMA50 on H1)              |
//| Used for the optional DXY correlation veto. 0 = unknown/flat.    |
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
//| Dynamic RR target based on regime strength                       |
//+------------------------------------------------------------------+
double DynamicRR()
  {
   bool trending = (g_regime.regime == REGIME_TREND_UP || g_regime.regime == REGIME_TREND_DOWN);
   if(trending && g_regime.adx != EMPTY_VALUE && g_regime.adx >= 30.0)
      return(InpStrongTrendRR);       // strong trend: let winners run
   return(InpBaseRR);                 // otherwise the standard 1:2
  }

//+------------------------------------------------------------------+
//| Compute SL distance combining structure swings and ATR bounds    |
//+------------------------------------------------------------------+
double ComputeSLDistance(const int direction, const SStructureInfo &st, const double refPrice)
  {
   double atr = indEntry.Atr(1);
   if(atr == EMPTY_VALUE || atr <= 0.0)
      return(0.0);

   double buffer  = atr * 0.3;    // liquidity buffer beyond the swing
   double slLevel = (direction > 0) ? structEntry.BuyStopLevel(st, buffer)
                                    : structEntry.SellStopLevel(st, buffer);
   double dist = 0.0;
   if(slLevel > 0.0)
      dist = (direction > 0) ? (refPrice - slLevel) : (slLevel - refPrice);

   //--- fall back to pure ATR stop when the swing is unusable
   if(dist <= 0.0)
      dist = atr * InpAtrMultSL;

   //--- clamp: not tighter than MinSLAtrMult x ATR (noise protection),
   //--- not wider than MaxSLAtrMult x ATR (risk control)
   dist = MathMax(dist, atr * InpMinSLAtrMult);
   dist = MathMin(dist, atr * InpMaxSLAtrMult);

   //--- respect broker stops level + spread
   long stopsLevel = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double minDist  = (stopsLevel + 1) * _Point
                   + SymbolInfoInteger(_Symbol, SYMBOL_SPREAD) * _Point;
   return(MathMax(dist, minDist));
  }

//+------------------------------------------------------------------+
//| Fail one SMC pipeline step: set status + audit log               |
//+------------------------------------------------------------------+
void FailStep(const int step, const string what)
  {
   g_tradingStatus = StringFormat("SMC step %d failed: %s", step, what);
   //--- log each distinct reason once, not on every bar
   if(what != g_lastSkipMsg)
     {
      logger.LogSkip(StringFormat("smc_step_%d", step), what);
      g_lastSkipMsg = what;
     }
  }

//+------------------------------------------------------------------+
//| Full entry pipeline: admin gates -> 11 SMC steps -> execution    |
//+------------------------------------------------------------------+
void TryEnter()
  {
   //--- administrative gates (risk limits / session / news / spread) ---
   string blockReason = "";
   if(!risk.TradingAllowed(blockReason))
     {
      g_tradingStatus = "BLOCKED: " + blockReason;
      return;
     }
   string session = CurrentSessionName();
   if(session == "")
     {
      g_tradingStatus = "outside trading sessions";
      return;
     }
   if(news.IsBlocked(g_newsStatus))
     {
      g_tradingStatus = "news pause";
      return;
     }
   g_newsStatus = "";
   long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(spread > InpMaxSpreadPoints)
     {
      g_tradingStatus = StringFormat("spread too high (%d pts)", (int)spread);
      return;
     }
   if(CountOwnPositions() >= InpMaxPositions)
     {
      g_tradingStatus = "position already open";
      return;
     }
   double threshold = InpScoreThreshold;
   if(risk.DailyTargetReached())
     {
      if(InpAfterTarget == AFTER_TARGET_STOP)
        {
         g_tradingStatus = "daily target reached - done for today";
         return;
        }
      threshold = InpQualityThreshold;
     }

   //================= SMC SEQUENTIAL PIPELINE =========================

   //--- STEP 1: Market Structure -------------------------------------
   //--- direction comes from D1/H4/H1 swing structure, NOT indicators
   int dir = scoring.DecideDirection(g_stD1, g_stH4, g_stH1);
   //--- counter-trend exception: only on a clear H1 Change of Character
   if(dir == 0 && InpAllowCounterTrend && g_stH1.recentCHoCH && g_stH1.bias != 0)
      dir = g_stH1.bias;
   if(dir == 0)
     {
      g_tradingStatus = "SMC step 1: structure unclear or HTF conflict - waiting";
      return;
     }
   bool isBuy = (dir > 0);

   //--- STEP 2: Liquidity map ----------------------------------------
   double atr = indEntry.Atr(1);
   SSmcAnalysis smc;
   if(!smartMoney.Scan(g_stEntry, atr == EMPTY_VALUE ? 0.0 : atr, smc))
     {
      FailStep(2, "liquidity scan failed (insufficient data)");
      return;
     }
   if(InpReqLiquidityTarget && !(isBuy ? smc.bslAbovePrice : smc.sslBelowPrice))
     {
      FailStep(2, "no liquidity pool in profit direction");
      return;
     }

   //--- STEP 3+4: BOS / CHoCH (MSS) ----------------------------------
   //--- a structure break in our direction is the primary signal
   if(InpReqBosChoch)
     {
      bool broke = (g_stEntry.bias == dir && (g_stEntry.recentBOS || g_stEntry.recentCHoCH));
      if(!broke)
        {
         FailStep(3, "no BOS/CHoCH in trade direction");
         return;
        }
     }

   //--- STEP 5: Order Block ------------------------------------------
   SOrderBlock ob;
   if(isBuy) ob = smc.obBull; else ob = smc.obBear;
   if(InpReqOrderBlock)
     {
      if(!ob.valid)
        {
         FailStep(5, "no valid order block");
         return;
        }
      if(ob.quality < InpMinOBQuality)
        {
         FailStep(5, StringFormat("order block quality %.0f < %.0f", ob.quality, InpMinOBQuality));
         return;
        }
     }

   //--- STEP 6: Fair Value Gap ---------------------------------------
   if(InpReqFVG && !(isBuy ? smc.fvgBull : smc.fvgBear))
     {
      FailStep(6, "no fair value gap in trade direction");
      return;
     }

   //--- STEP 7: Liquidity Sweep --------------------------------------
   //--- smart money grabs liquidity BEFORE the move; we wait for it
   if(InpReqSweep && !(isBuy ? smc.sweepBull : smc.sweepBear))
     {
      FailStep(7, "no liquidity sweep yet - waiting");
      return;
     }

   //--- STEP 8: Premium / Discount zone ------------------------------
   //--- buy only at discount, sell only at premium (equilibrium 0.5)
   if(InpReqPremiumDiscount)
     {
      if(isBuy && smc.rangePos > InpDiscountMax)
        {
         FailStep(8, StringFormat("price in premium (%.2f) - no buys", smc.rangePos));
         return;
        }
      if(!isBuy && smc.rangePos < 1.0 - InpDiscountMax)
        {
         FailStep(8, StringFormat("price in discount (%.2f) - no sells", smc.rangePos));
         return;
        }
     }

   //--- STEP 9: Mitigation -------------------------------------------
   //--- entry only when price is actually returning into the zone
   if(InpReqMitigation)
     {
      bool mitigating = (ob.valid && ob.mitigating) ||
                        (isBuy ? smc.fvgBullMitigated : smc.fvgBearMitigated);
      if(!mitigating)
        {
         FailStep(9, "price not mitigating OB/FVG yet - waiting");
         return;
        }
     }

   //--- STEP 10: Entry Confirmation ----------------------------------
   //--- 10a) lower timeframes must not actively oppose the direction
   if(InpReqTrendConfirm)
     {
      if(indM30.TrendDirection() == -dir || indM15.TrendDirection() == -dir)
        {
         FailStep(10, "lower timeframe opposing direction");
         return;
        }
     }
   //--- 10b) optional DXY correlation veto
   if(InpUseDxyFilter && InpDxySymbol != "")
     {
      int dxy = ExternalTrendDir(InpDxySymbol);
      if(dxy != 0 && dxy == dir)
        {
         FailStep(10, "DXY trending with gold direction");
         return;
        }
     }
   //--- 10c) SL/TP plan for RR + confidence scoring
   double refPrice = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                           : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double slDist = ComputeSLDistance(dir, g_stEntry, refPrice);
   if(slDist <= 0.0)
     {
      FailStep(10, "cannot compute SL distance");
      return;
     }
   double rr = DynamicRR();

   SScoreContext ctx;
   ctx.smc       = smc;
   ctx.plannedRR = rr;
   ctx.session   = session;
   scoring.Evaluate(dir, g_stD1, g_stH4, g_stH1, g_stEntry, indEntry, indH1, ctx, g_lastSignal);
   if(g_lastSignal.total < threshold)
     {
      g_tradingStatus = StringFormat("SMC step 10: score %.1f < %.1f - waiting", g_lastSignal.total, threshold);
      return;
     }

   //--- STEP 11: Risk Management + execution -------------------------
   if(rr < InpMinRR)
     {
      FailStep(11, StringFormat("RR %.2f below minimum %.2f", rr, InpMinRR));
      return;
     }
   OpenTrade(g_lastSignal, slDist, rr, session);
  }

//+------------------------------------------------------------------+
//| Open a trade with SL/TP, risk-based lot and full logging         |
//+------------------------------------------------------------------+
void OpenTrade(const SSignal &sig, const double slDist, const double rr, const string session)
  {
   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   bool   isBuy  = (sig.direction > 0);
   double price  = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                         : SymbolInfoDouble(_Symbol, SYMBOL_BID);

   double tpDist = slDist * rr;
   double sl = isBuy ? NormalizeDouble(price - slDist, digits)
                     : NormalizeDouble(price + slDist, digits);
   double tp = isBuy ? NormalizeDouble(price + tpDist, digits)
                     : NormalizeDouble(price - tpDist, digits);

   //--- position sizing (may refuse the trade entirely)
   string sizingNote = "";
   double lots = risk.CalcLot(slDist, sizingNote);
   if(lots <= 0.0)
     {
      g_tradingStatus = "sizing refused: " + sizingNote;
      logger.LogSkip("sizing", sizingNote);
      return;
     }

   //--- margin sanity check for the small account
   ENUM_ORDER_TYPE type = isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   if(!risk.MarginOK(type, lots, price))
     {
      g_tradingStatus = "insufficient free margin";
      logger.LogSkip("margin", "free margin below safety threshold");
      return;
     }

   //--- send the order (never without SL/TP)
   bool sent = isBuy ? trade.Buy(lots, _Symbol, price, sl, tp, "CG " + DoubleToString(sig.total, 0))
                     : trade.Sell(lots, _Symbol, price, sl, tp, "CG " + DoubleToString(sig.total, 0));
   if(!sent)
     {
      g_tradingStatus = StringFormat("order failed: %d %s",
                                     trade.ResultRetcode(), trade.ResultRetcodeDescription());
      return;
     }

   //--- find the fresh position and register its R for management
   ulong posTicket = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      posTicket = t;   // newest matching position
      break;
     }
   if(posTicket > 0)
      tradeMgr.RegisterPosition(posTicket, slDist);

   //--- log the decision with its full context
   logger.LogOpen(posTicket, _Symbol, sig.direction, price, sl, tp, lots, sig,
                  CRegimeDetector::RegimeName(g_regime.regime) + "/" + CRegimeDetector::VolName(g_regime.vol),
                  session, sizingNote);

   g_tradingStatus = StringFormat("%s %.2f lots opened (score %.1f, RR 1:%.1f)",
                                  isBuy ? "BUY" : "SELL", lots, sig.total, rr);
   Print("CapitalGuard: ", g_tradingStatus, " | ", sig.reason);
  }

//+------------------------------------------------------------------+
//| Trade transactions: log closes, clean per-ticket state           |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
  {
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD)
      return;
   if(!HistoryDealSelect(trans.deal))
      return;
   if(HistoryDealGetInteger(trans.deal, DEAL_MAGIC) != InpMagic)
      return;
   if((ENUM_DEAL_ENTRY)HistoryDealGetInteger(trans.deal, DEAL_ENTRY) != DEAL_ENTRY_OUT)
      return;

   //--- record the realized outcome
   logger.LogClose(trans.deal);

   //--- drop management state once the position is fully closed
   ulong posId = HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);
   if(!PositionSelectByTicket(posId))
      tradeMgr.ForgetPosition(posId);
  }
//+------------------------------------------------------------------+
