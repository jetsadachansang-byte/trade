//+------------------------------------------------------------------+
//|                                                 LineNotify.mqh   |
//|  CapitalGuard - LINE Official Account push client                |
//|                                                                  |
//|  Sends text messages through the LINE Messaging API:             |
//|   - push      : to one userId (the OA admin / a subscriber)      |
//|   - broadcast : to every follower of the OA                      |
//|                                                                  |
//|  Requirements (see docs/MANUAL_TH.md):                           |
//|   1. LINE Official Account + Messaging API channel               |
//|   2. Channel access token (long-lived)                           |
//|   3. MT5: Tools > Options > Expert Advisors >                    |
//|      "Allow WebRequest for listed URL" + add https://api.line.me |
//|                                                                  |
//|  In the Strategy Tester WebRequest is unavailable - messages     |
//|  are printed to the journal instead so logic stays testable.     |
//+------------------------------------------------------------------+
#ifndef CG_LINE_NOTIFY_MQH
#define CG_LINE_NOTIFY_MQH

//+------------------------------------------------------------------+
//| LINE OA push client                                              |
//+------------------------------------------------------------------+
class CLineNotify
  {
private:
   bool              m_enabled;
   string            m_token;       // channel access token
   string            m_to;          // userId; empty = broadcast to followers
   int               m_failCount;   // consecutive failures (for backoff logging)

   //--- escape a string for a JSON value; keeps newlines as \n
   string            JsonEscape(const string s) const
     {
      string out = s;
      StringReplace(out, "\\", "\\\\");
      StringReplace(out, "\"", "\\\"");
      StringReplace(out, "\r", "");
      StringReplace(out, "\n", "\\n");
      return(out);
     }

public:
                     CLineNotify() : m_enabled(false), m_token(""), m_to(""), m_failCount(0) {}

   //--- configure; `to` empty means broadcast to all OA followers
   void              Init(const bool enabled, const string token, const string to)
     {
      m_enabled = enabled;
      m_token   = token;
      m_to      = to;
      if(m_enabled && m_token == "")
         Print("LineNotify: enabled but channel access token is empty - messages will not be sent");
     }

   //--- send one text message; returns true when accepted by LINE
   bool              Push(const string text)
     {
      if(!m_enabled) return(false);

      //--- tester / missing token: journal only, so the flow is visible
      if(MQLInfoInteger(MQL_TESTER) || m_token == "")
        {
         Print("LINE >> ", text);
         return(true);
        }

      string url  = (m_to == "") ? "https://api.line.me/v2/bot/message/broadcast"
                                 : "https://api.line.me/v2/bot/message/push";
      string body;
      if(m_to == "")
         body = StringFormat("{\"messages\":[{\"type\":\"text\",\"text\":\"%s\"}]}",
                             JsonEscape(text));
      else
         body = StringFormat("{\"to\":\"%s\",\"messages\":[{\"type\":\"text\",\"text\":\"%s\"}]}",
                             m_to, JsonEscape(text));

      char data[], result[];
      string resultHeaders;
      StringToCharArray(body, data, 0, WHOLE_ARRAY, CP_UTF8);
      //--- drop the trailing null terminator added by StringToCharArray
      int n = ArraySize(data);
      if(n > 0 && data[n - 1] == 0) ArrayResize(data, n - 1);

      string headers = "Content-Type: application/json\r\n"
                       "Authorization: Bearer " + m_token + "\r\n";
      ResetLastError();
      int status = WebRequest("POST", url, headers, 5000, data, result, resultHeaders);

      if(status == 200)
        {
         m_failCount = 0;
         return(true);
        }
      m_failCount++;
      if(status == -1)
         PrintFormat("LineNotify: WebRequest blocked (err %d). Add https://api.line.me to "
                     "Tools > Options > Expert Advisors > Allow WebRequest", GetLastError());
      else
         PrintFormat("LineNotify: HTTP %d (%s)", status, CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8));
      return(false);
     }

   //--- consecutive failure count (dashboard health indicator)
   int               FailCount() const { return(m_failCount); }
  };

#endif // CG_LINE_NOTIFY_MQH
