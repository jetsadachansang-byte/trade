//+------------------------------------------------------------------+
//|                                                 NewsFilter.mqh   |
//|  CapitalGuard - Economic news filter                             |
//|                                                                  |
//|  Blocks trading around high-impact events (FOMC, CPI, NFP, ...)  |
//|  using the built-in MQL5 Economic Calendar. Also supports a      |
//|  manual list of blocked datetimes for events the calendar        |
//|  does not carry (war headlines, political shocks, speeches).     |
//|                                                                  |
//|  NOTE: the Economic Calendar is NOT available in the Strategy    |
//|  Tester; there the filter falls back to the manual list only.    |
//+------------------------------------------------------------------+
#ifndef CG_NEWS_FILTER_MQH
#define CG_NEWS_FILTER_MQH

//+------------------------------------------------------------------+
//| News filter                                                      |
//+------------------------------------------------------------------+
class CNewsFilter
  {
private:
   bool              m_enabled;
   int               m_preMinutes;      // block window before the event
   int               m_postMinutes;     // block window after the event
   string            m_currencies;      // comma list, e.g. "USD,EUR,JPY"
   datetime          m_manualTimes[];   // manually supplied event times
   bool              m_calendarWarned;  // log calendar unavailability once

   //--- true when `cur` is one of the currencies we care about
   bool              CurrencyRelevant(const string cur) const
     {
      if(m_currencies == "") return(true);
      return(StringFind("," + m_currencies + ",", "," + cur + ",") >= 0);
     }

public:
                     CNewsFilter() : m_enabled(true), m_preMinutes(45),
                                     m_postMinutes(45), m_currencies("USD"),
                                     m_calendarWarned(false) {}

   //--- configure; manualList = "yyyy.mm.dd hh:mi;yyyy.mm.dd hh:mi;..."
   void              Init(const bool enabled, const int preMinutes, const int postMinutes,
                          const string currencies, const string manualList)
     {
      m_enabled     = enabled;
      m_preMinutes  = preMinutes;
      m_postMinutes = postMinutes;
      m_currencies  = currencies;
      ArrayResize(m_manualTimes, 0);

      //--- parse the manual datetime list
      string parts[];
      int n = StringSplit(manualList, ';', parts);
      for(int i = 0; i < n; i++)
        {
         string s = parts[i];
         StringTrimLeft(s);
         StringTrimRight(s);
         if(s == "") continue;
         datetime t = StringToTime(s);
         if(t > 0)
           {
            int m = ArraySize(m_manualTimes);
            ArrayResize(m_manualTimes, m + 1);
            m_manualTimes[m] = t;
           }
        }
     }

   //--- true when trading must pause now; outReason describes the event
   bool              IsBlocked(string &outReason)
     {
      outReason = "";
      if(!m_enabled) return(false);

      datetime now = TimeCurrent();

      //--- 1) manual event list (works everywhere, incl. tester)
      for(int i = 0; i < ArraySize(m_manualTimes); i++)
        {
         if(now >= m_manualTimes[i] - m_preMinutes * 60 &&
            now <= m_manualTimes[i] + m_postMinutes * 60)
           {
            outReason = "Manual news block: " + TimeToString(m_manualTimes[i], TIME_DATE|TIME_MINUTES);
            return(true);
           }
        }

      //--- 2) built-in economic calendar: scan the surrounding window
      MqlCalendarValue values[];
      datetime from = now - m_postMinutes * 60;
      datetime to   = now + m_preMinutes * 60;
      if(!CalendarValueHistory(values, from, to))
        {
         //--- calendar unavailable (e.g. Strategy Tester) - warn once
         if(!m_calendarWarned && MQLInfoInteger(MQL_TESTER))
           {
            Print("NewsFilter: economic calendar unavailable in tester, using manual list only");
            m_calendarWarned = true;
           }
         return(false);
        }
      int total = ArraySize(values);

      for(int i = 0; i < total; i++)
        {
         MqlCalendarEvent event;
         if(!CalendarEventById(values[i].event_id, event)) continue;
         if(event.importance != CALENDAR_IMPORTANCE_HIGH)  continue;

         MqlCalendarCountry country;
         if(!CalendarCountryById(event.country_id, country)) continue;
         if(!CurrencyRelevant(country.currency)) continue;

         outReason = StringFormat("High-impact news: %s (%s) at %s",
                                  event.name, country.currency,
                                  TimeToString(values[i].time, TIME_DATE|TIME_MINUTES));
         return(true);
        }
      return(false);
     }
  };

#endif // CG_NEWS_FILTER_MQH
