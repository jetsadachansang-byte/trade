//+------------------------------------------------------------------+
//|                                        TradeTemplate_TP_SL.mq5   |
//|  MT5 Expert Advisor Template with proper TP/SL management        |
//|                                                                  |
//|  Features:                                                       |
//|   - SL/TP by fixed points or ATR-based (adaptive to volatility)  |
//|   - Risk-based lot sizing (% of account balance per trade)       |
//|   - Broker stops-level / freeze-level validation                 |
//|   - Breakeven move + trailing stop management                    |
//|   - Example signal: MA crossover (replace with your own logic)   |
//+------------------------------------------------------------------+
#property copyright "Trade Template"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>

//--- SL/TP calculation mode
enum ENUM_SLTP_MODE
  {
   SLTP_FIXED_POINTS,   // Fixed points
   SLTP_ATR             // ATR-based (adaptive)
  };

//--- Inputs: General ----------------------------------------------------
input group             "=== General ==="
input long              InpMagic          = 20260802;      // Magic number
input int               InpMaxPositions   = 1;             // Max open positions (this EA)
input int               InpSlippagePoints = 20;            // Max slippage (points)

//--- Inputs: Risk & Money Management ------------------------------------
input group             "=== Risk Management ==="
input double            InpRiskPercent    = 1.0;           // Risk per trade (% of balance)
input double            InpFixedLot       = 0.0;           // Fixed lot (0 = use risk %)

//--- Inputs: Stop Loss / Take Profit ------------------------------------
input group             "=== Stop Loss / Take Profit ==="
input ENUM_SLTP_MODE    InpSLTPMode       = SLTP_ATR;      // SL/TP mode
input int               InpSLPoints       = 300;           // Fixed SL (points)
input int               InpTPPoints       = 600;           // Fixed TP (points)
input int               InpATRPeriod      = 14;            // ATR period
input double            InpATRMultSL      = 1.5;           // ATR multiplier for SL
input double            InpRewardRatio    = 2.0;           // Reward:Risk ratio (TP = SL x RR)

//--- Inputs: Trade Management -------------------------------------------
input group             "=== Trade Management ==="
input bool              InpUseBreakeven   = true;          // Use breakeven
input int               InpBEProfitPoints = 300;           // Move to BE after profit (points)
input int               InpBELockPoints   = 20;            // BE lock-in offset (points)
input bool              InpUseTrailing    = true;          // Use trailing stop
input int               InpTrailStart     = 400;           // Trailing start (points profit)
input int               InpTrailDistance  = 250;           // Trailing distance (points)
input int               InpTrailStep      = 50;            // Trailing step (points)

//--- Inputs: Example Signal (MA crossover) ------------------------------
input group             "=== Signal: MA Crossover (example) ==="
input int               InpFastMAPeriod   = 20;            // Fast MA period
input int               InpSlowMAPeriod   = 50;            // Slow MA period
input ENUM_MA_METHOD    InpMAMethod       = MODE_EMA;      // MA method
input ENUM_APPLIED_PRICE InpMAPrice       = PRICE_CLOSE;   // MA applied price

//--- Globals ------------------------------------------------------------
CTrade   trade;
int      hFastMA  = INVALID_HANDLE;
int      hSlowMA  = INVALID_HANDLE;
int      hATR     = INVALID_HANDLE;
datetime lastBarTime = 0;

//+------------------------------------------------------------------+
//| Expert initialization                                            |
//+------------------------------------------------------------------+
int OnInit()
  {
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);

   hFastMA = iMA(_Symbol, _Period, InpFastMAPeriod, 0, InpMAMethod, InpMAPrice);
   hSlowMA = iMA(_Symbol, _Period, InpSlowMAPeriod, 0, InpMAMethod, InpMAPrice);
   hATR    = iATR(_Symbol, _Period, InpATRPeriod);

   if(hFastMA == INVALID_HANDLE || hSlowMA == INVALID_HANDLE || hATR == INVALID_HANDLE)
     {
      Print("Failed to create indicator handles");
      return(INIT_FAILED);
     }

   if(InpFastMAPeriod >= InpSlowMAPeriod)
     {
      Print("Fast MA period must be less than Slow MA period");
      return(INIT_PARAMETERS_INCORRECT);
     }

   if(InpRiskPercent <= 0.0 && InpFixedLot <= 0.0)
     {
      Print("Either risk percent or fixed lot must be positive");
      return(INIT_PARAMETERS_INCORRECT);
     }

   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Expert deinitialization                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   if(hFastMA != INVALID_HANDLE) IndicatorRelease(hFastMA);
   if(hSlowMA != INVALID_HANDLE) IndicatorRelease(hSlowMA);
   if(hATR    != INVALID_HANDLE) IndicatorRelease(hATR);
  }

//+------------------------------------------------------------------+
//| Expert tick                                                      |
//+------------------------------------------------------------------+
void OnTick()
  {
   // Manage open positions on every tick (breakeven / trailing)
   ManagePositions();

   // Open new trades only once per bar
   if(!IsNewBar())
      return;

   int signal = GetSignal();   // +1 buy, -1 sell, 0 none
   if(signal == 0)
      return;

   if(CountOwnPositions() >= InpMaxPositions)
      return;

   if(signal > 0)
      OpenTrade(ORDER_TYPE_BUY);
   else
      OpenTrade(ORDER_TYPE_SELL);
  }

//+------------------------------------------------------------------+
//| New bar detection                                                |
//+------------------------------------------------------------------+
bool IsNewBar()
  {
   datetime t = iTime(_Symbol, _Period, 0);
   if(t == lastBarTime)
      return(false);
   lastBarTime = t;
   return(true);
  }

//+------------------------------------------------------------------+
//| Example signal: MA crossover on the last closed bar              |
//| Replace this function with your own entry logic                  |
//+------------------------------------------------------------------+
int GetSignal()
  {
   double fast[], slow[];
   if(CopyBuffer(hFastMA, 0, 1, 2, fast) < 2) return(0);
   if(CopyBuffer(hSlowMA, 0, 1, 2, slow) < 2) return(0);

   // fast[0] = bar 1 (last closed), fast[1] = bar 2
   bool crossUp   = (fast[1] <= slow[1] && fast[0] > slow[0]);
   bool crossDown = (fast[1] >= slow[1] && fast[0] < slow[0]);

   if(crossUp)   return(1);
   if(crossDown) return(-1);
   return(0);
  }

//+------------------------------------------------------------------+
//| Compute SL distance in price units                               |
//+------------------------------------------------------------------+
double GetSLDistance()
  {
   if(InpSLTPMode == SLTP_FIXED_POINTS)
      return(InpSLPoints * _Point);

   // ATR mode
   double atr[];
   if(CopyBuffer(hATR, 0, 1, 1, atr) < 1)
      return(0.0);
   return(atr[0] * InpATRMultSL);
  }

//+------------------------------------------------------------------+
//| Compute TP distance in price units from SL distance              |
//+------------------------------------------------------------------+
double GetTPDistance(const double slDistance)
  {
   if(InpSLTPMode == SLTP_FIXED_POINTS)
      return(InpTPPoints * _Point);

   return(slDistance * InpRewardRatio);
  }

//+------------------------------------------------------------------+
//| Enforce broker minimum stop distance                             |
//+------------------------------------------------------------------+
double EnforceStopsLevel(double distance)
  {
   long stopsLevel = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double minDist  = (stopsLevel + 1) * _Point;
   double spread   = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD) * _Point;

   // Keep at least stops level + one spread as safety margin
   double floorDist = MathMax(minDist, spread * 2.0);
   return(MathMax(distance, floorDist));
  }

//+------------------------------------------------------------------+
//| Risk-based lot size from SL distance                             |
//+------------------------------------------------------------------+
double CalcLotSize(const double slDistance)
  {
   if(InpFixedLot > 0.0)
      return(NormalizeLot(InpFixedLot));

   double balance   = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskMoney = balance * InpRiskPercent / 100.0;

   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickValue <= 0.0 || tickSize <= 0.0 || slDistance <= 0.0)
      return(0.0);

   // Loss per 1.0 lot if SL is hit
   double lossPerLot = slDistance / tickSize * tickValue;
   if(lossPerLot <= 0.0)
      return(0.0);

   double lots = riskMoney / lossPerLot;
   return(NormalizeLot(lots));
  }

//+------------------------------------------------------------------+
//| Normalize lot to broker constraints                              |
//+------------------------------------------------------------------+
double NormalizeLot(double lots)
  {
   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   if(lotStep > 0.0)
      lots = MathFloor(lots / lotStep) * lotStep;

   lots = MathMax(minLot, MathMin(maxLot, lots));
   return(NormalizeDouble(lots, 2));
  }

//+------------------------------------------------------------------+
//| Open a trade with proper SL/TP                                   |
//+------------------------------------------------------------------+
void OpenTrade(const ENUM_ORDER_TYPE type)
  {
   double slDist = EnforceStopsLevel(GetSLDistance());
   double tpDist = EnforceStopsLevel(GetTPDistance(slDist));
   if(slDist <= 0.0 || tpDist <= 0.0)
     {
      Print("Invalid SL/TP distance, trade skipped");
      return;
     }

   double lots = CalcLotSize(slDist);
   if(lots <= 0.0)
     {
      Print("Invalid lot size, trade skipped");
      return;
     }

   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double price, sl, tp;

   if(type == ORDER_TYPE_BUY)
     {
      price = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      sl    = NormalizeDouble(price - slDist, digits);
      tp    = NormalizeDouble(price + tpDist, digits);
      if(!trade.Buy(lots, _Symbol, price, sl, tp, "TP/SL Template"))
         PrintFormat("Buy failed: %d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
      else
         PrintFormat("BUY %.2f lots @ %s SL=%s TP=%s",
                     lots, DoubleToString(price, digits),
                     DoubleToString(sl, digits), DoubleToString(tp, digits));
     }
   else
     {
      price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      sl    = NormalizeDouble(price + slDist, digits);
      tp    = NormalizeDouble(price - tpDist, digits);
      if(!trade.Sell(lots, _Symbol, price, sl, tp, "TP/SL Template"))
         PrintFormat("Sell failed: %d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
      else
         PrintFormat("SELL %.2f lots @ %s SL=%s TP=%s",
                     lots, DoubleToString(price, digits),
                     DoubleToString(sl, digits), DoubleToString(tp, digits));
     }
  }

//+------------------------------------------------------------------+
//| Count positions opened by this EA on this symbol                 |
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
//| Breakeven + trailing stop management                             |
//+------------------------------------------------------------------+
void ManagePositions()
  {
   if(!InpUseBreakeven && !InpUseTrailing)
      return;

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;

      long   type      = PositionGetInteger(POSITION_TYPE);
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double curSL     = PositionGetDouble(POSITION_SL);
      double curTP     = PositionGetDouble(POSITION_TP);

      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

      double newSL = curSL;

      if(type == POSITION_TYPE_BUY)
        {
         double profitPoints = (bid - openPrice) / _Point;

         // 1) Breakeven
         if(InpUseBreakeven && profitPoints >= InpBEProfitPoints)
           {
            double bePrice = NormalizeDouble(openPrice + InpBELockPoints * _Point, digits);
            if(curSL < bePrice)
               newSL = bePrice;
           }

         // 2) Trailing stop
         if(InpUseTrailing && profitPoints >= InpTrailStart)
           {
            double trailSL = NormalizeDouble(bid - InpTrailDistance * _Point, digits);
            if(trailSL > newSL + InpTrailStep * _Point)
               newSL = trailSL;
           }

         if(newSL > curSL && IsValidSLModify(POSITION_TYPE_BUY, newSL, bid))
            trade.PositionModify(ticket, newSL, curTP);
        }
      else // SELL
        {
         double profitPoints = (openPrice - ask) / _Point;

         // 1) Breakeven
         if(InpUseBreakeven && profitPoints >= InpBEProfitPoints)
           {
            double bePrice = NormalizeDouble(openPrice - InpBELockPoints * _Point, digits);
            if(curSL == 0.0 || curSL > bePrice)
               newSL = bePrice;
           }

         // 2) Trailing stop
         if(InpUseTrailing && profitPoints >= InpTrailStart)
           {
            double trailSL = NormalizeDouble(ask + InpTrailDistance * _Point, digits);
            if(newSL == 0.0 || trailSL < newSL - InpTrailStep * _Point)
               newSL = trailSL;
           }

         bool improved = (curSL == 0.0 && newSL > 0.0) || (newSL > 0.0 && newSL < curSL);
         if(improved && IsValidSLModify(POSITION_TYPE_SELL, newSL, ask))
            trade.PositionModify(ticket, newSL, curTP);
        }
     }
  }

//+------------------------------------------------------------------+
//| Validate new SL respects broker stops level                      |
//+------------------------------------------------------------------+
bool IsValidSLModify(const ENUM_POSITION_TYPE type, const double newSL, const double refPrice)
  {
   long stopsLevel = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double minDist  = (stopsLevel + 1) * _Point;

   if(type == POSITION_TYPE_BUY)
      return(refPrice - newSL >= minDist);
   return(newSL - refPrice >= minDist);
  }
//+------------------------------------------------------------------+
