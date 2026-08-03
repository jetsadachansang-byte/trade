//+------------------------------------------------------------------+
//|                                             TelegramNotify.mqh   |
//|  CapitalGuard - Telegram Bot API push client                     |
//|                                                                  |
//|  Sends signal notifications through the Telegram Bot API:        |
//|    POST https://api.telegram.org/bot<TOKEN>/sendMessage          |
//|                                                                  |
//|  Requirements (see docs/MANUAL_TH.md):                           |
//|   1. A bot created via @BotFather -> bot token                   |
//|   2. A chat id: your own user id, a group id (negative), or a    |
//|      channel username in the form @mychannel                     |
//|   3. MT5: Tools > Options > Expert Advisors >                    |
//|      "Allow WebRequest for listed URL" + add                     |
//|      https://api.telegram.org                                    |
//|                                                                  |
//|  Unlike LINE, Telegram imposes no monthly message quota; the     |
//|  practical limits (~30 msg/sec, 20 msg/min per group) are far    |
//|  above what a high-quality signal system produces.               |
//|                                                                  |
//|  Messages use parse_mode=HTML so headers can be bold. Dynamic    |
//|  content is limited to symbol names and numbers, so no HTML      |
//|  escaping of the payload is required; JSON escaping is applied.  |
//|                                                                  |
//|  In the Strategy Tester WebRequest is unavailable - messages     |
//|  are printed to the journal instead so logic stays testable.     |
//+------------------------------------------------------------------+
#ifndef CG_TELEGRAM_NOTIFY_MQH
#define CG_TELEGRAM_NOTIFY_MQH

//+------------------------------------------------------------------+
//| Telegram bot push client                                         |
//+------------------------------------------------------------------+
class CTelegramNotify
  {
private:
   bool              m_enabled;
   string            m_token;       // bot token from @BotFather
   string            m_chatId;      // user id / group id / @channel
   int               m_failCount;   // consecutive failures (health flag)

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

   //--- build the API endpoint for one method
   string            Endpoint(const string method) const
     {
      return("https://api.telegram.org/bot" + m_token + "/" + method);
     }

   //--- shared WebRequest wrapper; returns the HTTP status code
   int               Call(const string verb, const string url, const string body, string &response)
     {
      char data[], result[];
      string resultHeaders;
      string headers = "";

      if(body != "")
        {
         StringToCharArray(body, data, 0, WHOLE_ARRAY, CP_UTF8);
         //--- drop the trailing null terminator added by StringToCharArray
         int n = ArraySize(data);
         if(n > 0 && data[n - 1] == 0) ArrayResize(data, n - 1);
         headers = "Content-Type: application/json\r\n";
        }
      else
         ArrayResize(data, 0);

      ResetLastError();
      int status = WebRequest(verb, url, headers, 5000, data, result, resultHeaders);
      response = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
      return(status);
     }

public:
                     CTelegramNotify() : m_enabled(false), m_token(""), m_chatId(""), m_failCount(0) {}

   //--- configure the client
   void              Init(const bool enabled, const string token, const string chatId)
     {
      m_enabled = enabled;
      m_token   = token;
      m_chatId  = chatId;
      m_failCount = 0;
      if(m_enabled && m_token == "")
         Print("TelegramNotify: enabled but bot token is empty - messages will not be sent");
      if(m_enabled && m_chatId == "")
         Print("TelegramNotify: enabled but chat id is empty - run with InpTgShowChatId=true "
               "to discover it, or see the manual");
     }

   //--- send one message; returns true when Telegram accepted it
   bool              Push(const string text)
     {
      if(!m_enabled) return(false);

      //--- tester / missing credentials: journal only, so flow stays visible
      if(MQLInfoInteger(MQL_TESTER) || m_token == "" || m_chatId == "")
        {
         Print("TELEGRAM >> ", text);
         return(true);
        }

      string body = StringFormat(
         "{\"chat_id\":\"%s\",\"text\":\"%s\",\"parse_mode\":\"HTML\","
         "\"disable_web_page_preview\":true}",
         JsonEscape(m_chatId), JsonEscape(text));

      string response = "";
      int status = Call("POST", Endpoint("sendMessage"), body, response);

      if(status == 200)
        {
         m_failCount = 0;
         return(true);
        }

      m_failCount++;
      if(status == -1)
         PrintFormat("TelegramNotify: WebRequest blocked (err %d). Add https://api.telegram.org to "
                     "Tools > Options > Expert Advisors > Allow WebRequest", GetLastError());
      else if(status == 401)
         Print("TelegramNotify: HTTP 401 - bot token is wrong or was revoked");
      else if(status == 400)
         PrintFormat("TelegramNotify: HTTP 400 - chat id '%s' is invalid, or the bot has never "
                     "been started by that chat. Response: %s", m_chatId, response);
      else
         PrintFormat("TelegramNotify: HTTP %d (%s)", status, response);
      return(false);
     }

   //--- setup helper: print recent chat ids to the journal so the user
   //--- can find the value for InpTgChatId without leaving MT5.
   //--- Send any message to the bot first, then run this.
   void              PrintChatIds()
     {
      if(m_token == "")
        {
         Print("TelegramNotify: cannot fetch updates - bot token is empty");
         return;
        }
      if(MQLInfoInteger(MQL_TESTER))
        {
         Print("TelegramNotify: getUpdates is unavailable in the Strategy Tester");
         return;
        }

      string response = "";
      int status = Call("GET", Endpoint("getUpdates"), "", response);
      if(status != 200)
        {
         PrintFormat("TelegramNotify: getUpdates failed with HTTP %d (%s)", status, response);
         return;
        }
      if(StringFind(response, "\"result\":[]") >= 0)
        {
         Print("TelegramNotify: no updates yet. Open Telegram, send any message to your bot "
               "(e.g. /start), then reload the EA.");
         return;
        }

      //--- the raw payload contains the chat objects; print it so the
      //--- user can read "id": <number> straight from the journal
      Print("TelegramNotify: getUpdates response below - look for \"chat\":{\"id\":<number>");
      Print(response);
     }

   //--- consecutive failure count (dashboard health indicator)
   int               FailCount() const { return(m_failCount); }
  };

#endif // CG_TELEGRAM_NOTIFY_MQH
