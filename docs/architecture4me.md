

---
TrendAgent Architecture

1. Core
   - rss_fetcher
   - processor
   - reporter

2. Delivery
   - email_sender
   - (future) telegram_sender

3. Config
   - config.json
   - rss_list.json

   TrendAgent target architecture (clear version)

USER (Telegram)
   |
   v
[1] Input Layer (local)
   - receive Telegram message
   - identify chat_id / user_id
   - read raw text / voice transcript
   - ONLY these 3 exact slash commands stay local:
       /status
       /help
       /report
   - every other message goes to cloud first
   - no local keyword-based natural-language routing

   If exact slash command:
       -> local exact command path
   Else:
       -> cloud controller

[1A] Exact Slash Command Path (local only)
   - /status
   - /help
   - /report
   - execute locally and reply
   - this is a tiny fast path only
   - nothing natural-language belongs here

[2] Cloud Controller LLM  = THE BRAIN / PLANNER
   - sees every non-slash Telegram message first
   - decides what the user means
   - decides whether this turn is:
       CHAT
       RUN_PIPELINE
       GET_LATEST_REPORT
       SUMMARIZE_LATEST_REPORT
       ASK_CLARIFICATION
       CONFIRM_PENDING_ACTION
       CANCEL_PENDING_ACTION
       other future actions
   - returns a strict structured plan (JSON only)
   - no generic filler should override the plan

[3] Local Plan / State Manager
   - validate and normalize returned plan
   - store pending confirmation per chat_id
   - manage yes / yes please / ok / go ahead / please do / start
   - manage no / no thanks / cancel / stop / not now
   - this layer does NOT interpret natural language semantically
   - it only manages state and confirmation flow

[4] Local Executor  = THE HANDS
   - executes the structured plan chosen by the cloud controller
   - local executor does NOT decide what the sentence means
   - local executor only performs operations such as:
       run_pipeline
       get_latest_report_text
       get_latest_report_file
       send_report
       read report content
       store/retrieve pending plan
       future local tools

[5] Cloud Content Generation Layer (when needed)
   - if the task requires language generation over report content,
     the cloud LLM does it
   - examples:
       summarize latest report
       explain report
       answer questions about report
   - local code may fetch the report text,
     but cloud LLM must read and summarize/explain it

[6] Output Layer
   - send final reply to Telegram
   - split messages if needed
   - log result


Responsibility boundary

Cloud LLM:
- understands language
- decides intent
- decides action vs chat vs clarification
- decides whether confirmation is needed
- summarizes report content
- explains report content

Local Python:
- exact slash commands only
- validates plan
- stores pending confirmation
- executes local tools
- fetches report text/files
- manages state/logging/safety rails
- does NOT do keyword-based natural-language intent classification
- does NOT act as the main semantic interpreter


Very important routing rule

If message is exactly:
   /status
   /help
   /report
then handle locally.

Else:
   send to cloud controller first.


Not allowed as local routing

These must NOT be interpreted locally by keyword:
- status please
- help me
- show me the report
- please send the report
- summarize the latest report
- yes please
- not now

All of those must go to cloud first.


Correct summarize flow

User: summarize the latest report
   ↓
Cloud controller decides plan
   ↓
Local executor fetches latest report text
   ↓
Cloud LLM reads that report text
   ↓
Cloud LLM produces summary
   ↓
Telegram sends reply

Important:
- local code must not do the actual semantic summarization
- local only fetches the report text/content
- cloud does the summarization


Correct confirmation flow

User: please send the report
   ↓
Cloud controller returns:
   intent = RUN_PIPELINE
   needs_confirmation = true
   confirmation_prompt = ...
   ↓
Local state manager stores pending plan
   ↓
Telegram sends confirmation prompt

User: yes please
   ↓
Local state manager matches affirmative reply
   ↓
Local executor executes stored pending plan
   ↓
Telegram sends result

User: not now
   ↓
Local state manager cancels stored pending plan
   ↓
Telegram sends cancel reply


Short instruction for Codex

1. Only exact slash commands stay local: /status, /help, /report.
2. Every other Telegram message must go to the cloud controller first.
3. The cloud controller is the semantic brain and must decide the turn.
4. Local code must not do keyword-based natural-language intent classification anymore.
5. Local code should only manage state, confirmations, exact slash commands, safety rails, and execute structured plans.
6. For summarization, local code may fetch/read the report text, but the actual summarization must be done by the cloud LLM.
7. “Summarize the latest report” should therefore mean:
   - local fetch latest report text
   - cloud LLM summarizes that text
8. Confirmation replies like yes / yes please / go ahead should execute the stored pending plan.
9. Negative replies like no / cancel / not now should cancel the stored pending plan.
10. No generic filler text should override the structured plan returned by the controller.