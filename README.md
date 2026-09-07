# VoiceOps: Production Voice AI Screener & Post-Call Intelligence Pipeline

VoiceOps is an automated, event-driven voice screening pipeline designed to qualify inbound sales and support prospects in real time. It pairs a low-latency conversational voice agent with an asynchronous intelligence engine powered by Google Gemini and n8n orchestration to eliminate manual CRM entry and accelerate lead response times.

---

## 🏗 System Architecture

```text
             [ Inbound Phone Call ]
                       │
                       ▼
                [ Vapi.ai Agent ]
                 │            │
  (Mid-Call Tool Call)     (End-Of-Call Payload)
                 │            │
                 ▼            ▼
         FastAPI Backend Services (Port 8000)
                 │            │
        (Forward Event)   (Gemini Extraction Layer)
                 │            │
                 │    ┌───────┴──────────────────┐
                 │    ▼                          ▼
                 │  [Lead Categorization]   [Sentiment/Pain Points]
                 │    └───────┬──────────────────┘
                 │            │
                 ▼            ▼
     n8n Event Orchestration Engine (Port 5678)
      │                                │
      ├─► Twilio WhatsApp Dispatch     ├─► Google Sheets Lead DB
      └─► Slack Internal Notification  └─► Priority Escalation Alerts
```

---

## 🚀 Key Features

* **Real-Time Mid-Call Tool Execution:** The voice agent detects prospect intent to view technical material and dispatches informational brochures over WhatsApp via Twilio during the call.
* **Structured Post-Call Intelligence:** Extracts granular caller signals (budget clearance, deployment urgency, sentiment, pain points) from raw transcripts using Google Gemini with deterministic JSON schema enforcement.
* **Multi-Stage Orchestration (n8n):**
  * Asynchronously logs verified call records and call recording URLs to Google Sheets CRM.
  * Dynamically parses intent scoring (`Hot`, `Warm`, `Cold`) and pushes rich Slack Block Kit alerts to internal sales channels for instant follow-up.
* **Resilient Infrastructure:** Endpoints implement non-blocking external webhook dispatches and graceful degradation (`Continue on Fail`) to ensure high availability during third-party API outages.

---

## 🛠 Tech Stack

* **Backend Framework:** FastAPI, Uvicorn, Pydantic v2
* **LLM & Extraction:** Google Gemini API (Structured Outputs), Groq fallback
* **Voice Agent Infrastructure:** Vapi.ai
* **Workflow Automation:** n8n (Self-Hosted)
* **Integrations:** Twilio API (WhatsApp Messaging), Google Sheets API / Drive API, Slack Webhooks
* **Testing & Validation:** HTTPX, Pytest, Custom Asynchronous Integration Test Suite

---

## 📂 Repository Structure

```text
├── n8n_workflows/
│   ├── midcall_brochure.json        # Mid-call WhatsApp brochure workflow
│   └── postcall_intelligence.json   # Post-call CRM sync & Slack alert flow
├── static/
│   └── index.html                   # Web dashboard & interactive call trigger UI
├── config.py                        # Pydantic settings & environment configuration
├── intelligence.py                  # Gemini-powered call summarization & classification logic
├── main.py                          # FastAPI routing, Vapi webhooks, and tool call handlers
├── integration_test.py              # End-to-end integration test runner (10/10 automated suite)
├── smoke_test.py                    # Rapid pre-flight smoke test suite
├── requirements.txt                 # Project dependencies
├── .env.example                     # Template for environment variables
└── README.md
```

---

## ⚙️ Installation & Local Setup

### 1. Clone & Install Dependencies

```bash
git clone https://github.com/Devananditha/VoiceOps-Engine.git
cd VoiceOps-Engine

python -m venv venv
source venv/bin/activate  # On Windows: .\venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Create a `.env` file based on `.env.example`:

```env
GEMINI_API_KEY=your_gemini_api_key
GROQ_API_KEY=your_groq_api_key
VAPI_API_KEY=your_vapi_api_key
VAPI_ASSISTANT_ID=your_vapi_assistant_id
VAPI_PHONE_NUMBER_ID=your_vapi_phone_number_id
N8N_MIDCALL_WEBHOOK_URL=http://localhost:5678/webhook/midcall
N8N_POSTCALL_WEBHOOK_URL=http://localhost:5678/webhook/postcall
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
GOOGLE_SHEET_ID=your_google_sheet_id
```

### 3. Start n8n & Import Workflows

```bash
n8n start
```

* Navigate to `http://localhost:5678`.
* Import `midcall_brochure.json` and `postcall_intelligence.json` from the `n8n_workflows/` directory.
* Connect your Google Cloud Service Account and Slack credentials, then toggle both workflows to **Active**.

### 4. Start the Application Server

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 🧪 Automated Verification Suite

The repository includes a comprehensive integration testing harness (`integration_test.py`) simulating Vapi payloads, validating LLM schema compliance, and verifying webhook reachability.

Run the test suite:

```bash
python integration_test.py
```

### Test Coverage Results

```text
============================= Test Summary =============================
0. Pre-flight Check          : PASS (Server reachable, status=ok)
1. Mid-Call Webhook Handlers : PASS (Tool call parsed, forwarded to n8n)
2. Post-Call Extraction      : PASS (Enriched payload parsed in <4s)
   - Lead Classification     : PASS (Hot/Warm/Cold accuracy)
   - Budget & Timeline Check : PASS (Boolean and string extraction)
   - Key Pain Points         : PASS (Normalized array extraction)
3. Orchestration Endpoints   : PASS (HTTP 200 on active n8n webhooks)
============================= 10/10 Passed =============================
```

---

## 🔒 Security & Best Practices

* **Zero Hardcoded Secrets:** Environment-driven configuration for all access keys and webhook targets.
* **Least Privilege Access:** Cloud integrations utilize scoped Service Account roles for Google Drive and Sheets without public file exposure.
* **Payload Validation:** Strict Pydantic schemas enforce type safety across all inbound caller metadata.
