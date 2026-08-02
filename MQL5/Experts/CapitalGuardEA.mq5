//+------------------------------------------------------------------+
//|                                             CapitalGuardEA.mq5   |
//|  CapitalGuard - Capital Preservation First trading system        |
//|                                                                  |
//|  Priorities (in order):                                          |
//|   1. Lose as little as possible                                  |
//|   2. Consistent small profits                                    |
//|   3. Never overtrade                                             |
//|   4. Multi-factor, multi-timeframe decisions                     |
//|   5. Every order must have a logged reason                       |
//|                                                                  |
//|  Hard rules: no Martingale, no Grid, no averaging down,          |
//|  every order carries SL and TP, daily loss stops trading.        |
//|                                                                  |
//|  Modules (MQL5/Include/CapitalGuard/):                           |
//|   RiskManager | IndicatorSet | MarketStructure | Regime |        |
//|   NewsFilter | ScoringEngine | TradeManager | Logger | Dashboard |
//+------------------------------------------------------------------+
#property copyright "CapitalGuard"
#property version   "1.00"

#include <Trade\Trade.mqh>
#include <CapitalGuard\RiskManager.mqh>
#include <CapitalGuard\IndicatorSet.mqh>
#include <CapitalGuard\MarketStructure.mqh>
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
   AFTER_TARGET_QUALITY    // Continue: high-quality setups only
  };

//--- Inputs: General ----------------------------------------------------
input group             "=== General ==="
input long              InpMagic            = 20260803;         // Magic number
input ENUM_TIMEFRAMES   InpEntryTF          = PERIOD_M15;       // Entry timeframe
input int               InpMaxPositions     = 1;                // Max simultaneous positions
input int               InpSlippagePoints   = 30;               // Max slippage (points)
input int               InpMaxSpreadPoints  = 45;               // Max spread to trade (points)

//--- Inputs: Risk (capital preservation first) --------------------------
input group             "=== Risk Management ==="
input double            InpRiskPerTrade     = 0.75;             // Risk per trade (%)
input double            InpMaxDailyLoss     = 3.0;              // Max daily loss (%) - stop for the day
input double            InpMaxWeeklyLoss    = 8.0;              // Max weekly loss (%) - stop for the week
input double            InpMaxDrawdown      = 15.0;             // Max drawdown (%) - circuit breaker
input int               InpMaxTradesPerDay  = 3;                // Max trades per day
input double            InpDailyTargetPct   = 2.0;              // Daily profit target (%)
input ENUM_AFTER_TARGET InpAfterTarget      = AFTER_TARGET_QUALITY; // After target reached
input ENUM_MINLOT_POLICY InpMinLotPolicy    = MINLOT_USE_IF_CAPPED; // When lot < broker minimum
input double            InpHardRiskCap      = 3.0;              // Hard cap per trade with min lot (%)

//--- Inputs: Entry decision ---------------------------------------------
input group             "=== Decision Engine ==="
input double            InpScoreThreshold   = 80.0;             // Min confidence score (0-100)
input double            InpQualityThreshold = 90.0;             // Score after daily target reached
input double            InpWeightTrend      = 25.0;             // Weight: Trend
input double            InpWeightMomentum   = 20.0;             // Weight: Momentum
input double            InpWeightVolume     = 20.0;             // Weight: Volume
input double            InpWeightStructure  = 20.0;             // Weight: Market structure
input double            InpWeightVolatility = 15.0;             // Weight: Volatility

//--- Inputs: Indicators -------------------------------------------------
input group             "=== Indicators ==="
input int               InpEmaFast          = 20;               // EMA fast
input int               InpEmaMid           = 50;               // EMA mid
input int               InpEmaSlow          = 200;              // EMA slow
input int               InpRsiPeriod        = 14;               // RSI period
input int               InpAtrPeriod        = 14;               // ATR period
input int               InpAdxPeriod        = 14;               // ADX period
input int               InpBBPeriod         = 20;               // Bollinger period
input double            InpBBDev            = 2.0;              // Bollinger deviation
input int               InpSwingBars        = 3;                // Swing confirmation bars
input int               InpStructLookback   = 80;               // Structure scan lookback (bars)

//--- Inputs: Regime detection -------------------------------------------
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
input double            InpMinRR            = 1.5;              // Minimum Risk:Reward
input double            InpBaseRR           = 2.0;              // Target Risk:Reward
input double            InpStrongTrendRR    = 2.5;              // RR when trend is strong (ADX high)

//--- Inputs: Trade management -------------------------------------------
input group             "=== Trade Management ==="
input bool              InpUseBreakEven     = true;             // Move SL to BE at 1R
input double            InpBETriggerR       = 1.0;              // BE trigger (R multiples)
input int               InpBELockPoints     = 30;               // BE lock-in offset (points)
input bool              InpUsePartial       = true;             // Partial close at 1R
input double            InpPartialR         = 1.0;              // Partial close trigger (R)
input double            InpPartialPct       = 0.5;              // Fraction closed (0-1)
input bool              InpUseAtrTrail      = true;             // ATR trailing after BE
input double            InpAtrTrailMult     = 1.2;              // Trailing distance (x ATR)
input int               InpMaxHoldHours     = 48;               // Time exit after N hours (0=off)
input double            InpTimeExitMinR     = 0.3;              // Time-exit only if profit < this R

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
input int               InpNewsPreMin       = 45;               // Block before news (minutes)
input int               InpNewsPostMin      = 45;               // Block after news (minutes)
input string            InpNewsCurrencies   = "USD";            // Currencies to watch (comma list)
input string            InpNewsManualTimes  = "";               // Manual blocks "yyyy.mm.dd hh:mi;..."

//--- Inputs: Dashboard --------------------------------------------------
input group             "=== Dashboard ==="
input bool              InpShowDashboard    = true;             // Show on-chart dashboard
input int               InpDashboardSecs    = 3;                // Dashboard refresh (seconds)

//--- Module instances ---------------------------------------------------
CTrade            trade;
CRiskManager      risk;
CIndicatorSet     indH4, indH1, indM30, indM15, indM5, indEntry;
CMarketStructure  structure;
CRegimeDetector   regimeDetector;
CNewsFilter       news;
CScoringEngine    scoring;
CTradeManager     tradeMgr;
CTradeLogger      logger;
CDashboard        dashboard;

//--- Runtime state ------------------------------------------------------
datetime          g_lastBarTime   = 0;
SSignal           g_lastSignal;
SRegimeInfo       g_regime;
string            g_newsStatus    = "";
string            g_tradingStatus = "starting";

//+------------------------------------------------------------------+
//| Expert initialization                                            |
//+------------------------------------------------------------------+
int OnInit()
  {
   //--- trade executor
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);

   //--- indicator bundles for every analysed timeframe
   bool ok = true;
   ok &= indH4.Init(_Symbol, PERIOD_H4, InpEmaFast, InpEmaMid, InpEmaSlow, InpRsiPeriod, InpAtrPeriod, InpAdxPeriod, InpBBPeriod, InpBBDev);
   ok &= indH1.Init(_Symbol, PERIOD_H1, InpEmaFast, InpEmaMid, InpEmaSlow, InpRsiPeriod, InpAtrPeriod, InpAdxPeriod, InpBBPeriod, InpBBDev);
   ok &= indM30.Init(_Symbol, PERIOD_M30, InpEmaFast, InpEmaMid, InpEmaSlow, InpRsiPeriod, InpAtrPeriod, InpAdxPeriod, InpBBPeriod, InpBBDev);
   ok &= indM15.Init(_Symbol, PERIOD_M15, InpEmaFast, InpEmaMid, InpEmaSlow, InpRsiPeriod, InpAtrPeriod, InpAdxPeriod, InpBBPeriod, InpBBDev);
   ok &= indM5.Init(_Symbol, PERIOD_M5, InpEmaFast, InpEmaMid, InpEmaSlow, InpRsiPeriod, InpAtrPeriod, InpAdxPeriod, InpBBPeriod, InpBBDev);
   ok &= indEntry.Init(_Symbol, InpEntryTF, InpEmaFast, InpEmaMid, InpEmaSlow, InpRsiPeriod, InpAtrPeriod, InpAdxPeriod, InpBBPeriod, InpBBDev);
   if(!ok)
     {
      Print("CapitalGuard: failed to create indicator handles");
      return(INIT_FAILED);
     }

   //--- modules
   risk.Init(_Symbol, InpMagic, InpRiskPerTrade, InpMaxDailyLoss, InpMaxWeeklyLoss,
             InpMaxDrawdown, InpMaxTradesPerDay, InpDailyTargetPct,
             InpMinLotPolicy, InpHardRiskCap);
   structure.Init(_Symbol, InpEntryTF, InpSwingBars, InpStructLookback);
   regimeDetector.Init(InpAdxTrendMin, InpHighVolRatio, InpLowVolRatio, InpAtrAvgBars);
   news.Init(InpNewsEnabled, InpNewsPreMin, InpNewsPostMin, InpNewsCurrencies, InpNewsManualTimes);
   scoring.Init(InpWeightTrend, InpWeightMomentum, InpWeightVolume, InpWeightStructure, InpWeightVolatility);
   tradeMgr.Init(GetPointer(trade), _Symbol, InpMagic,
                 InpUseBreakEven, InpBETriggerR, InpBELockPoints,
                 InpUsePartial, InpPartialR, InpPartialPct,
                 InpUseAtrTrail, InpAtrTrailMult,
                 InpMaxHoldHours, InpTimeExitMinR);
   logger.Init(InpMagic);
   dashboard.Init(_Symbol, InpMagic, InpDashboardSecs);

   //--- sanity checks on inputs
   if(InpMinRR < 1.0 || InpBaseRR < InpMinRR)
     {
      Print("CapitalGuard: invalid RR configuration");
      return(INIT_PARAMETERS_INCORRECT);
     }

   g_lastSignal.direction = 0;
   g_lastSignal.total     = 0.0;
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Expert deinitialization                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   indH4.Release(); indH1.Release(); indM30.Release();
   indM15.Release(); indM5.Release(); indEntry.Release();
   dashboard.Clear();
  }

//+------------------------------------------------------------------+
//| Expert tick                                                      |
//+------------------------------------------------------------------+
void OnTick()
  {
   //--- keep risk day/week snapshots fresh
   risk.UpdateRollover();

   //--- manage open positions every tick (BE / partial / trail / time)
   tradeMgr.Manage(indEntry);

   //--- refresh regime + dashboard
   regimeDetector.Detect(indH1, g_regime);
   if(InpShowDashboard)
      dashboard.Update(risk, g_regime, g_lastSignal, g_newsStatus, g_tradingStatus, CurrentSessionName());

   //--- evaluate new entries once per closed bar on the entry timeframe
   if(!IsNewBar())
      return;

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
//| Name of the session we are currently inside ("" = none allowed)  |
//+------------------------------------------------------------------+
string CurrentSessionName()
  {
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   int h = dt.hour;
   if(InpTradeAsian   && h >= InpAsianStart  && h < InpAsianEnd)  return("Asian");
   if(InpTradeLondon  && h >= InpLondonStart && h < InpLondonEnd) return("London");
   if(InpTradeNewYork && h >= InpNYStart     && h < InpNYEnd)     return("NewYork");
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
//| Full entry pipeline: filters -> scoring -> SL/TP -> sizing       |
//+------------------------------------------------------------------+
void TryEnter()
  {
   //--- 1) risk gates: daily/weekly loss, drawdown, trade count
   string blockReason = "";
   if(!risk.TradingAllowed(blockReason))
     {
      g_tradingStatus = "BLOCKED: " + blockReason;
      return;
     }

   //--- 2) session filter
   string session = CurrentSessionName();
   if(session == "")
     {
      g_tradingStatus = "outside trading sessions";
      return;
     }

   //--- 3) news filter
   if(news.IsBlocked(g_newsStatus))
     {
      g_tradingStatus = "news pause";
      logger.LogSkip("news", g_newsStatus);
      return;
     }
   g_newsStatus = "";

   //--- 4) spread filter (protect a small account from bad fills)
   long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(spread > InpMaxSpreadPoints)
     {
      g_tradingStatus = StringFormat("spread too high (%d pts)", (int)spread);
      return;
     }

   //--- 5) exposure: never stack positions without reason
   if(CountOwnPositions() >= InpMaxPositions)
     {
      g_tradingStatus = "position already open";
      return;
     }

   //--- 6) daily target logic
   double threshold = InpScoreThreshold;
   if(risk.DailyTargetReached())
     {
      if(InpAfterTarget == AFTER_TARGET_STOP)
        {
         g_tradingStatus = "daily target reached - done for today";
         return;
        }
      threshold = InpQualityThreshold;   // continue only on A+ setups
     }

   //--- 7) market structure snapshot on the entry timeframe
   SStructureInfo st;
   if(!structure.Scan(st))
     {
      g_tradingStatus = "structure scan failed (insufficient data)";
      return;
     }

   //--- 8) weighted confluence scoring
   scoring.Evaluate(indH4, indH1, indM30, indM15, indEntry, st, g_regime, g_lastSignal);
   if(g_lastSignal.direction == 0)
     {
      g_tradingStatus = "no signal: " + g_lastSignal.reason;
      return;
     }
   if(g_lastSignal.total < threshold)
     {
      g_tradingStatus = StringFormat("score %.1f < %.1f", g_lastSignal.total, threshold);
      return;
     }

   //--- 9) build SL from structure + ATR, TP from dynamic RR
   OpenTrade(g_lastSignal, st, session);
  }

//+------------------------------------------------------------------+
//| Dynamic RR target based on regime strength                       |
//+------------------------------------------------------------------+
double DynamicRR()
  {
   bool trending = (g_regime.regime == REGIME_TREND_UP || g_regime.regime == REGIME_TREND_DOWN);
   if(trending && g_regime.adx != EMPTY_VALUE && g_regime.adx >= 30.0)
      return(InpStrongTrendRR);       // strong trend: let winners run
   if(trending)
      return(InpBaseRR);              // normal trend: target RR
   return(InpMinRR);                  // range: conservative RR
  }

//+------------------------------------------------------------------+
//| Compute SL distance combining structure swings and ATR bounds    |
//+------------------------------------------------------------------+
double ComputeSLDistance(const int direction, const SStructureInfo &st, const double refPrice)
  {
   double atr = indEntry.Atr(1);
   if(atr == EMPTY_VALUE || atr <= 0.0)
      return(0.0);

   double buffer   = atr * 0.3;    // liquidity buffer beyond the swing
   double slLevel  = (direction > 0) ? structure.BuyStopLevel(st, buffer)
                                     : structure.SellStopLevel(st, buffer);
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
//| Open a trade with SL/TP, risk-based lot and full logging         |
//+------------------------------------------------------------------+
void OpenTrade(const SSignal &sig, const SStructureInfo &st, const string session)
  {
   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   bool   isBuy  = (sig.direction > 0);
   double price  = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                         : SymbolInfoDouble(_Symbol, SYMBOL_BID);

   double slDist = ComputeSLDistance(sig.direction, st, price);
   if(slDist <= 0.0)
     {
      g_tradingStatus = "cannot compute SL distance";
      return;
     }
   double rr     = DynamicRR();
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
