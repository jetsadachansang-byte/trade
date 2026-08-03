//+------------------------------------------------------------------+
//|                                       CapitalGuardSignalEA.mq5   |
//|  CapitalGuard Signal v2 - Multi-symbol SMC/ICT analyst -> LINE   |
//|                                                                  |
//|  This EA NEVER opens orders. It analyses a prioritised universe  |
//|  of liquid symbols and pushes only the highest-quality setups    |
//|  (score >= 90) to a LINE Official Account:                       |
//|                                                                  |
//|   Tier 1 (continuous, most resources): XAUUSD                    |
//|   Tier 2 (every closed bar): EURUSD GBPUSD USDJPY USDCHF         |
//|                              AUDUSD NZDUSD USDCAD                |
//|   Tier 3 (stricter threshold): EURJPY GBPJPY EURGBP AUDJPY       |
//|                                CADJPY CHFJPY                     |
//|                                                                  |
//|  When several setups appear in one cycle, the highest-priority   |
//|  symbol wins (XAUUSD > EURUSD > GBPUSD > USDJPY > others).       |
//|                                                                  |
//|  Gold macro context: DXY / US yields / VIX trends (when the      |
//|  broker offers those symbols). One conflicting factor lowers     |
//|  the score; two or more suspends the signal.                     |
//|                                                                  |
//|  Attach to ONE chart only (ideally XAUUSD M5). No signal all     |
//|  day is normal behaviour: quality over quantity.                 |
//+------------------------------------------------------------------+
#property copyright "CapitalGuard"
#property version   "2.00"

#include <CapitalGuard\ScoringEngine.mqh>
#include <CapitalGuard\SymbolAnalyst.mqh>
#include <CapitalGuard\NewsFilter.mqh>
#include <CapitalGuard\LineNotify.mqh>
#include <CapitalGuard\SignalManager.mqh>

//--- Inputs: General ----------------------------------------------------
input group             "=== General ==="
input long              InpMagic            = 20260804;         // Instance id (log file names)
input ENUM_TIMEFRAMES   InpEntryTF          = PERIOD_M5;        // Entry analysis timeframe (M1/M5)
input int               InpScanSeconds      = 15;               // Scan cycle (seconds)
input int               InpMaxSpreadPoints  = 45;               // Max spread to signal (points)

//--- Inputs: Symbol universe (edit names to match your broker) ----------
input group             "=== Symbols (priority order) ==="
input string            InpTier1Symbols     = "XAUUSD";                                          // Tier 1: continuous
input string            InpTier2Symbols     = "EURUSD,GBPUSD,USDJPY,USDCHF,AUDUSD,NZDUSD,USDCAD"; // Tier 2: majors
input string            InpTier3Symbols     = "EURJPY,GBPJPY,EURGBP,AUDJPY,CADJPY,CHFJPY";        // Tier 3: crosses
input double            InpTier3Extra       = 2.0;              // Tier 3 threshold add-on (stricter)

//--- Inputs: LINE Official Account --------------------------------------
input group             "=== LINE OA ==="
input bool              InpLineEnabled      = true;             // Send messages to LINE OA
input string            InpLineToken        = "";               // Channel access token (Messaging API)
input string            InpLineUserId       = "";               // Target userId (empty = broadcast)

//--- Inputs: Signal issuing ---------------------------------------------
input group             "=== Signals ==="
input double            InpScoreThreshold   = 90.0;             // Min confidence score (0-100)
input int               InpMaxSignalsPerDay = 3;                // Max signals per day (all symbols)
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

//--- Inputs: Gold macro context (broker symbol names; "" = skip) --------
input group             "=== Gold Macro Context ==="
input string            InpDxySymbol        = "";               // Dollar index symbol (e.g. USDX)
input string            InpYieldSymbol      = "";               // US yield/bond proxy symbol
input string            InpVixSymbol        = "";               // VIX symbol (e.g. VIX)
input double            InpMacroPenalty     = 5.0;              // Score penalty per single conflict

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
input string            InpNewsCurrencies   = "USD,EUR,GBP,JPY,CHF,AUD,NZD,CAD"; // Currencies watched
input string            InpNewsManualTimes  = "";               // Manual blocks "yyyy.mm.dd hh:mi;..."

//--- Inputs: Dashboard --------------------------------------------------
input group             "=== Dashboard ==="
input bool              InpShowDashboard    = true;             // Show on-chart dashboard
input int               InpDashboardSecs    = 3;                // Chart dashboard refresh (seconds)
input bool              InpWriteWebDash     = true;             // Write mobile HTML dashboard file
input int               InpWebDashSecs      = 60;               // HTML dashboard refresh (seconds)

//--- Module instances ---------------------------------------------------
CScoringEngine    scoring;
CNewsFilter       news;
CLineNotify       line;
CSignalManager    signalMgr;
CSymbolAnalyst   *g_analysts[];      // priority-ordered analyst pool

//--- Runtime state ------------------------------------------------------
datetime          g_lastSignalAt  = 0;
datetime          g_lastDashAt    = 0;
datetime          g_lastWebDashAt = 0;
string            g_newsStatus    = "";
string            g_status        = "starting";

//+------------------------------------------------------------------+
//| Build the shared analyst configuration from inputs               |
//+------------------------------------------------------------------+
void BuildConfig(SAnalystConfig &cfg)
  {
   cfg.entryTF        = InpEntryTF;
   cfg.emaFast        = InpEmaFast;   cfg.emaMid = InpEmaMid;   cfg.emaSlow = InpEmaSlow;
   cfg.rsiPeriod      = InpRsiPeriod; cfg.atrPeriod = InpAtrPeriod;
   cfg.adxPeriod      = InpAdxPeriod; cfg.bbPeriod = InpBBPeriod; cfg.bbDev = InpBBDev;
   cfg.swingBars      = InpSwingBars; cfg.structLookback = InpStructLookback;
   cfg.smcWindow      = InpSmcWindow;
   cfg.adxTrendMin    = InpAdxTrendMin; cfg.highVolRatio = InpHighVolRatio;
   cfg.lowVolRatio    = InpLowVolRatio; cfg.atrAvgBars = InpAtrAvgBars;
   cfg.atrMultSL      = InpAtrMultSL; cfg.maxSLAtrMult = InpMaxSLAtrMult;
   cfg.minSLAtrMult   = InpMinSLAtrMult;
   cfg.allowCounterTrend  = InpAllowCounterTrend;
   cfg.reqLiquidityTarget = InpReqLiquidityTarget;
   cfg.reqBosChoch    = InpReqBosChoch;
   cfg.reqOrderBlock  = InpReqOrderBlock;   cfg.minOBQuality = InpMinOBQuality;
   cfg.reqFVG         = InpReqFVG;          cfg.reqSweep = InpReqSweep;
   cfg.reqPremiumDiscount = InpReqPremiumDiscount; cfg.discountMax = InpDiscountMax;
   cfg.reqMitigation  = InpReqMitigation;   cfg.reqTrendConfirm = InpReqTrendConfirm;
   cfg.reqWeeklyBias  = InpReqWeeklyBias;   cfg.reqOTE = InpReqOTE;
   cfg.tp1R = InpTP1R; cfg.tp2R = InpTP2R; cfg.tp3R = InpTP3R;
   cfg.maxSpreadPoints = InpMaxSpreadPoints;
  }

//+------------------------------------------------------------------+
//| Create analysts for one comma-separated symbol list              |
//+------------------------------------------------------------------+
void AddTier(const string list, const int tier, const SAnalystConfig &cfg)
  {
   string parts[];
   int n = StringSplit(list, ',', parts);
   for(int i = 0; i < n; i++)
     {
      string sym = parts[i];
      StringTrimLeft(sym); StringTrimRight(sym);
      if(sym == "") continue;
      if(!SymbolSelect(sym, true))
        {
         PrintFormat("CapitalGuard Signal: symbol %s not found at this broker - skipped "
                     "(edit the tier lists to match your broker's names)", sym);
         continue;
        }
      CSymbolAnalyst *a = new CSymbolAnalyst();
      if(!a.Init(sym, tier, cfg))
        {
         PrintFormat("CapitalGuard Signal: failed to init analyst for %s - skipped", sym);
         delete a;
         continue;
        }
      int k = ArraySize(g_analysts);
      ArrayResize(g_analysts, k + 1);
      g_analysts[k] = a;
     }
  }

//+------------------------------------------------------------------+
//| Expert initialization                                            |
//+------------------------------------------------------------------+
int OnInit()
  {
   scoring.Init(InpWeightStructure, InpWeightLiquidity, InpWeightBosChoch,
                InpWeightOB, InpWeightFVG, InpWeightVolume, InpWeightIndicator);
   news.Init(InpNewsEnabled, InpNewsPreMin, InpNewsPostMin, InpNewsCurrencies, InpNewsManualTimes);
   line.Init(InpLineEnabled, InpLineToken, InpLineUserId);
   signalMgr.Init(GetPointer(line), InpMagic, InpSignalExpiryHrs);

   //--- build the prioritised analyst pool (tier order = send order)
   SAnalystConfig cfg;
   BuildConfig(cfg);
   ArrayResize(g_analysts, 0);
   AddTier(InpTier1Symbols, 1, cfg);
   AddTier(InpTier2Symbols, 2, cfg);
   AddTier(InpTier3Symbols, 3, cfg);
   if(ArraySize(g_analysts) == 0)
     {
      Print("CapitalGuard Signal: no valid symbols - check the tier lists");
      return(INIT_FAILED);
     }

   //--- macro context symbols (optional)
   if(InpDxySymbol != "")   SymbolSelect(InpDxySymbol, true);
   if(InpYieldSymbol != "") SymbolSelect(InpYieldSymbol, true);
   if(InpVixSymbol != "")   SymbolSelect(InpVixSymbol, true);

   //--- scan clock (analysis is timer-driven so all symbols are
   //--- covered even when the chart symbol is quiet)
   EventSetTimer(MathMax(5, InpScanSeconds));

   line.Push(StringFormat("🤖 CapitalGuard Signal เริ่มทำงาน\nวิเคราะห์ %d สินทรัพย์ (Tier1: %s)\nส่งเฉพาะสัญญาณคะแนน >= %.0f",
                          ArraySize(g_analysts), InpTier1Symbols, InpScoreThreshold));
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Expert deinitialization                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
   for(int i = 0; i < ArraySize(g_analysts); i++)
     {
      if(CheckPointer(g_analysts[i]) == POINTER_DYNAMIC)
        {
         g_analysts[i].Release();
         delete g_analysts[i];
        }
     }
   ArrayResize(g_analysts, 0);
   Comment("");
  }

//+------------------------------------------------------------------+
//| Chart ticks: track live signals + refresh dashboards             |
//+------------------------------------------------------------------+
void OnTick()
  {
   signalMgr.Monitor();
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
  }

//+------------------------------------------------------------------+
//| Scan clock: analyse the whole universe by priority               |
//+------------------------------------------------------------------+
void OnTimer()
  {
   signalMgr.Monitor();     // also track while the chart symbol is quiet
   ScanUniverse();
  }

//+------------------------------------------------------------------+
//| Session name (global gate)                                       |
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
//| Simple external-symbol trend (SMA20 vs SMA50 on H1)              |
//+------------------------------------------------------------------+
int ExternalTrendDir(const string sym)
  {
   if(sym == "") return(0);
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
//| Gold macro context: count factors conflicting with `dir`.        |
//| DXY or yields trending WITH gold direction = conflict            |
//| (gold is inversely driven by both). VIX falling while buying     |
//| gold = risk-on headwind. Missing symbols are simply skipped.     |
//+------------------------------------------------------------------+
int GoldMacroConflicts(const int dir, string &desc)
  {
   int conflicts = 0;
   desc = "";
   int dxy = ExternalTrendDir(InpDxySymbol);
   if(dxy != 0 && dxy == dir)
     { conflicts++; desc += "DXY trending with gold; "; }
   int yld = ExternalTrendDir(InpYieldSymbol);
   if(yld != 0 && yld == dir)
     { conflicts++; desc += "US yields trending with gold; "; }
   int vix = ExternalTrendDir(InpVixSymbol);
   if(vix != 0 && vix == -dir)
     { conflicts++; desc += "VIX against gold flow; "; }
   return(conflicts);
  }

//+------------------------------------------------------------------+
//| One full priority-ordered scan of the symbol universe           |
//+------------------------------------------------------------------+
void ScanUniverse()
  {
   //--- global gates first (shared by all symbols) -------------------
   string session = CurrentSessionName();
   if(session == "")
     { g_status = "outside analysed sessions"; return; }
   if(InpUseKillZones && !InKillZone())
     { g_status = "waiting for kill zone"; return; }
   if(news.IsBlocked(g_newsStatus))
     { g_status = "news pause: " + g_newsStatus; return; }
   g_newsStatus = "";
   datetime dayStart = iTime(_Symbol, PERIOD_D1, 0);
   bool capReached  = (InpMaxSignalsPerDay > 0 &&
                       signalMgr.SignalsSince(dayStart) >= InpMaxSignalsPerDay);
   bool inCooldown  = (g_lastSignalAt > 0 &&
                       TimeCurrent() - g_lastSignalAt < (long)InpCooldownMinutes * 60);

   //--- analyse in priority order; first passing candidate is sent ---
   bool sent      = false;
   int  candidates = 0;
   for(int i = 0; i < ArraySize(g_analysts); i++)
     {
      CSymbolAnalyst *a = g_analysts[i];
      //--- Tier 1 is re-analysed every cycle; others on new bars only
      bool force = (a.Tier() == 1);
      double threshold = InpScoreThreshold + (a.Tier() == 3 ? InpTier3Extra : 0.0);

      SSignalCandidate c;
      bool got = a.Analyse(force, scoring, threshold, c);

      //--- structure may have flipped: check live signals of this symbol
      SStructureInfo st;
      a.EntryStructure(st);
      signalMgr.CheckInvalidation(a.Symbol(), st);

      if(got) candidates++;
      if(!got || sent || capReached || inCooldown)
         continue;
      if(signalMgr.HasActiveSignal(c.symbol))
         continue;                       // never stack signals per symbol

      //--- gold macro context (Tier 1 only)
      if(c.tier == 1)
        {
         string mdesc = "";
         int conflicts = GoldMacroConflicts(c.dir, mdesc);
         if(conflicts >= 2)
           {
            g_status = "macro conflict - signal suspended (" + mdesc + ")";
            continue;
           }
         if(conflicts == 1)
           {
            c.score -= InpMacroPenalty;
            if(c.score < threshold)
              {
               g_status = "macro penalty dropped score below threshold";
               continue;
              }
            c.notes += "\n• ⚠️ ปัจจัยมหภาคขัดแย้งบางส่วน: " + mdesc;
           }
        }

      //--- send it (highest-priority symbol wins this cycle)
      signalMgr.NewSignal(c);
      g_lastSignalAt = TimeCurrent();
      g_status = StringFormat("SIGNAL SENT: %s %s score %.1f",
                              c.symbol, c.dir > 0 ? "BUY" : "SELL", c.score);
      Print("CapitalGuard Signal: ", g_status);
      sent = true;   // keep looping so remaining symbols still refresh
     }

   //--- summarise this cycle for the dashboards
   if(!sent)
     {
      if(capReached)
         g_status = StringFormat("daily signal cap reached (%d) - waiting for tomorrow",
                                 InpMaxSignalsPerDay);
      else if(inCooldown)
         g_status = StringFormat("cooldown %d min between signals", InpCooldownMinutes);
      else if(candidates > 0)
         g_status = "candidate found but a signal is already active on that symbol";
      else
         g_status = StringFormat("scanning %d symbols - no qualifying setup yet",
                                 ArraySize(g_analysts));
     }
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
   text += "===== CAPITAL GUARD SIGNAL - multi-symbol (no auto-trade) =====\n";
   text += StringFormat("  Session: %s | KillZone: %s | News: %s\n",
                        CurrentSessionName() == "" ? "closed hrs" : CurrentSessionName(),
                        InKillZone() ? "YES" : "no",
                        g_newsStatus == "" ? "clear" : g_newsStatus);
   text += StringFormat("  Signals today: %d | Active: %d | Win rate: %.1f%% (W%d/L%d/C%d)\n",
                        signalMgr.SignalsSince(dayStart), signalMgr.ActiveCount(),
                        winRate, wins, losses, cancelled);
   text += StringFormat("  LINE: %s | Status: %s\n",
                        line.FailCount() == 0 ? "OK" : StringFormat("%d fails", line.FailCount()),
                        g_status);
   text += "---------------------------------------------------------------\n";
   for(int i = 0; i < ArraySize(g_analysts); i++)
     {
      CSymbolAnalyst *a = g_analysts[i];
      text += StringFormat("  T%d %-8s %-28s %s\n",
                           a.Tier(), a.Symbol(), a.BiasSummary(), a.Status());
     }
   text += "===============================================================\n";
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
   string lastSigHtml = "-";
   if(hasSig)
     {
      int d = (int)SymbolInfoInteger(lastSig.symbol, SYMBOL_DIGITS);
      lastSigHtml = StringFormat("%s %s @ %s | SL %s | TP2 %s | score %.0f | %s",
                                 lastSig.symbol, lastSig.dir > 0 ? "BUY" : "SELL",
                                 DoubleToString(lastSig.entry, d),
                                 DoubleToString(lastSig.sl, d),
                                 DoubleToString(lastSig.tp2, d),
                                 lastSig.score, CSignalManager::StatusName(lastSig.status));
     }

   string html;
   html  = "<meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>";
   html += "<meta http-equiv='refresh' content='60'>";
   html += "<title>CapitalGuard Signal</title>";
   html += "<style>body{font-family:sans-serif;background:#111;color:#eee;margin:1em}"
           "h2{color:#ffd700}table{width:100%;border-collapse:collapse;margin-bottom:1em}"
           "td,th{padding:6px;border-bottom:1px solid #333;text-align:left}"
           "td:first-child{color:#999}th{color:#ffd700}</style>";
   html += "<h2>CapitalGuard Signal</h2><table>";
   html += "<tr><td>อัปเดตล่าสุด</td><td>" + TimeToString(TimeCurrent(), TIME_DATE|TIME_MINUTES) + " (server)</td></tr>";
   html += "<tr><td>สถานะตลาด</td><td>" + (CurrentSessionName() == "" ? "นอกช่วงวิเคราะห์" : CurrentSessionName())
         + (InKillZone() ? " (Kill Zone)" : "") + "</td></tr>";
   html += "<tr><td>ข่าว</td><td>" + (g_newsStatus == "" ? "ไม่มีข่าวแรงขณะนี้" : g_newsStatus) + "</td></tr>";
   html += "<tr><td>สัญญาณล่าสุด</td><td>" + lastSigHtml + "</td></tr>";
   html += StringFormat("<tr><td>สัญญาณวันนี้ / active</td><td>%d / %d</td></tr>",
                        signalMgr.SignalsSince(dayStart), signalMgr.ActiveCount());
   html += StringFormat("<tr><td>Win Rate (session)</td><td>%.1f%% (W%d / L%d / ยกเลิก %d)</td></tr>",
                        winRate, wins, losses, cancelled);
   html += "<tr><td>สถานะระบบ</td><td>" + g_status + "</td></tr>";
   html += "</table>";
   html += "<table><tr><th>Tier</th><th>Symbol</th><th>Bias</th><th>สถานะ</th></tr>";
   for(int i = 0; i < ArraySize(g_analysts); i++)
     {
      CSymbolAnalyst *a = g_analysts[i];
      html += StringFormat("<tr><td>T%d</td><td>%s</td><td>%s</td><td>%s</td></tr>",
                           a.Tier(), a.Symbol(), a.BiasSummary(), a.Status());
     }
   html += "</table><p style='color:#666'>รีเฟรชอัตโนมัติทุก 60 วินาที · CapitalGuard</p>";

   int fh = FileOpen("CapitalGuard\\dashboard.html", FILE_WRITE|FILE_TXT|FILE_ANSI, ';', CP_UTF8);
   if(fh != INVALID_HANDLE)
     {
      FileWriteString(fh, html);
      FileClose(fh);
     }
  }
//+------------------------------------------------------------------+
