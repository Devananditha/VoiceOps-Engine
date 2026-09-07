# VoiceOps Engine: Real-Time Conversational Voice AI and Automated Post-Call Intelligence Pipeline

VoiceOps Engine is an event-driven, production-grade voice qualification and CRM automation platform. It integrates low-latency conversational speech synthesis with real-time tool execution and asynchronous post-call intelligence extraction. By orchestrating Vapi.ai, Google Gemini, Groq LLM inference, and self-hosted n8n workflow engines, VoiceOps eliminates manual data entry, guarantees instant lead triage, and enforces sub-second mid-call asset dispatch.

---

## Table of Contents

- [System Architecture](#system-architecture)
- [Design Decisions and Trade-Offs](#design-decisions-and-trade-offs)
  - [Framework Selection: FastAPI vs. Traditional WSGI](#framework-selection-fastapi-vs-traditional-wsgi)
  - [LLM Cascade Strategy: Gemini Primary with Groq Fallback](#llm-cascade-strategy-gemini-primary-with-groq-fallback)
  - [Orchestration Decoupling: FastAPI vs. Workflow Engine (n8n)](#orchestration-decoupling-fastapi-vs-workflow-engine-n8n)
  - [Concurrency and Non-Blocking IO](#concurrency-and-non-blocking-io)
- [Data Flow and Lifecycle](#data-flow-and-lifecycle)
  - [1. Live Call and In-Call Tool Execution](#1-live-call-and-in-call-tool-execution)
  - [2. Post-Call Extraction and Enrichment](#2-post-call-extraction-and-enrichment)
  - [3. Downstream CRM and Notification Dispatch](#3-downstream-crm-and-notification-dispatch)
- [API Specification and Data Contracts](#api-specification-and-data-contracts)
  - [Inbound Call Trigger (`POST /api/trigger-call`)](#inbound-call-trigger-post-apitrigger-call)
  - [Vapi Mid-Call Tool Hook (`POST /vapi-tool-call`)](#vapi-mid-call-tool-hook-post-vapi-tool-call)
  - [Vapi End-of-Call Webhook (`POST /vapi-end-call`)](#vapi-end-of-call-webhook-post-vapi-end-call)
  - [Structured Extraction Schema](#structured-extraction-schema)
- [Resilience, Fault Tolerance, and Edge Cases](#resilience-fault-tolerance-and-edge-cases)
- [Repository Structure](#repository-structure)
- [Environment Configuration](#environment-configuration)
- [Local Setup and Deployment](#local-setup-and-deployment)
- [Automated Verification and Testing](#automated-verification-and-testing)
- [Security and Compliance Posture](#security-and-compliance-posture)

---

## System Architecture

The VoiceOps architecture separates real-time conversational streaming from post-call analytical processing. This design ensures that latency-sensitive audio streams are never blocked by analytical or database operations.

```text
+-----------------------------------------------------------------------------------+
|                                 TELEPHONY LAYER                                   |
|                                                                                   |
|   Prospect Phone <==========> Twilio / SIP Trunk <==========> Vapi.ai Voice Agent |
+-----------------------------------------------------------------------------------+
                                   |                 |
                  (Mid-Call Tool)  |                 |  (End-of-Call Report)
                                   v                 v
+-----------------------------------------------------------------------------------+
|                            APPLICATION GATEWAY (FASTAPI)                          |
|                                                                                   |
|   POST /vapi-tool-call                           POST /vapi-end-call              |
|   [Payload Validation & Routing]                 [Field Extraction & Normalization]|
+-----------------------------------------------------------------------------------+
          |                                                  |
          |                                                  v
          |                                  +------------------------------+
          |                                  |   INTELLIGENCE PIPELINE      |
          |                                  |                              |
          |                                  |   Primary: Gemini 1.5/2.0    |
          |                                  |   Fallback: Groq LLaMA/GPT   |
          |                                  |   (Deterministic JSON Schema)|
          |                                  +------------------------------+
          |                                                  |
          | (Raw Tool Dispatch)                              | (Enriched Analytical Event)
          v                                                  v
+-----------------------------------------------------------------------------------+
|                        EVENT ORCHESTRATION PIPELINE (N8N)                         |
|                                                                                   |
|   Mid-Call Brochure Workflow                     Post-Call CRM Sync Workflow      |
|   --------------------------                     ---------------------------      |
|   - Extract Caller Metadata                      - Parse Enriched Payload         |
|   - Send Twilio WhatsApp Document                - Multi-Branch Triage (Hot/Warm) |
|   - Log Real-Time Dispatch to Slack              - Google Sheets Append/Upsert    |
|                                                  - High-Priority Slack Escalation |
+-----------------------------------------------------------------------------------+
```

---

## Design Decisions and Trade-Offs

### Framework Selection: FastAPI vs. Traditional WSGI

- **Decision**: Implemented backend services using FastAPI and Uvicorn running on Python 3.11+.
- **Rationale**: Telephony webhooks require rapid acknowledgement to avoid connection timeouts from voice providers (Vapi enforces a strict timeout on tool calls). FastAPI provides native asynchronous request processing via `asyncio`, non-blocking HTTP clients via `httpx.AsyncClient`, and high-throughput serialization through Pydantic v2 core compiled in Rust.
- **Alternative Considered**: Flask or Django with Celery. Rejected due to unnecessary operational complexity (Redis/RabbitMQ message broker overhead) for webhook forwarding and higher baseline request latency.

### LLM Cascade Strategy: Gemini Primary with Groq Fallback

- **Decision**: Two-tiered analytical pipeline using Google Gemini (`gemini-1.5-flash` / `gemini-2.0-flash`) as the primary inference engine, with automatic, transparent fallback to Groq (`openai/gpt-oss-20b` or `llama-3.1-8b-instant`).
- **Rationale**: 
  - Gemini provides native structured JSON schema compliance and cost-effective context window utilization for extended call transcripts.
  - LLM APIs are subject to transient rate limits (HTTP 429), regional outages, or capacity exhaustion. The system executes an automatic fallback in `intelligence.py`: if Gemini fails or times out, the transcript is routed to Groq's LPUs, which achieve sub-second inference speeds.
- **Trade-Off**: The Groq fallback requires maintaining parallel system prompts and ensuring both providers strictly adhere to the unified Pydantic JSON contract.

### Orchestration Decoupling: FastAPI vs. Workflow Engine (n8n)

- **Decision**: Decouple business workflow logic (Slack notifications, spreadsheet updates, CRM mutations) from the FastAPI core into an external orchestrator (n8n).
- **Rationale**:
  - **Separation of Concerns**: The FastAPI application functions purely as an edge gateway and extraction worker. It remains stateless, horizontally scalable, and resilient.
  - **Maintainability**: Business workflows (notification formats, channel routing, CRM schema changes) can be modified, replayed, and inspected visually without requiring codebase refactoring, regression testing, or service redeployments.
  - **Auditability**: n8n persists historical execution graphs, allowing post-mortem inspection of all third-party API payloads.

### Concurrency and Non-Blocking IO

- **Decision**: Long-running synchronous SDK operations are offloaded from the main event loop.
- **Implementation**: The Groq client SDK currently relies on synchronous sockets. In `intelligence.py`, calls to `_analyze_with_groq` are wrapped in `asyncio.to_thread` to prevent thread pool starvation and maintain event loop responsiveness for concurrent voice calls.
- **Connection Pooling**: A single `httpx.AsyncClient` lifecycle is managed via FastAPI lifespan context (`request.app.state.http_client`), enforcing persistent TCP keep-alive connections and eliminating TLS handshake overhead across webhook deliveries.

---

## Data Flow and Lifecycle

### 1. Live Call and In-Call Tool Execution

1. An inbound prospect initiates a voice session managed by the Vapi.ai platform.
2. When the caller requests documentation or pricing information, the agent's LLM determines that a function execution is required and emits a `tool-calls` event targeting `send_midcall_brochure`.
3. Vapi dispatches an HTTP POST request to `POST /vapi-tool-call` containing caller metadata and the tool call parameters.
4. FastAPI extracts the caller's E.164 phone number, verifies payload integrity, and issues an asynchronous POST request to the n8n mid-call webhook (`/webhook/midcall`).
5. FastAPI responds immediately with `HTTP 200 OK` (`{"result": "Brochure dispatched via WhatsApp"}`) to ensure conversational continuity without latency artifacts.
6. Concurrently, n8n invokes the Twilio REST API to transmit the media brochure over WhatsApp and posts an audit notification to Slack.

### 2. Post-Call Extraction and Enrichment

1. When the call terminates, Vapi compiles call telemetry (transcript, recording audio URL, duration, caller number) and posts an `end-of-call-report` to `POST /vapi-end-call`.
2. The endpoint extracts the full dialog transcript and delegates processing to `intelligence.analyze_call(transcript)`.
3. The intelligence pipeline extracts:
   - Lead Qualification: `Hot` (immediate intent + budget confirmed), `Warm` (interest demonstrated but timeline/budget vague), or `Cold` (disqualified, non-responsive, or hostile).
   - Commercial Intent: Binary flag indicating if budget was explicitly discussed.
   - Timeline Constraints: Extracted deployment horizon.
   - Core Pain Points: Normalized array of customer operational challenges.
   - Analytical Rationale: Two-sentence synthesized justification for the assigned grade.
4. Extracted attributes are merged with caller metadata (`phone_number`, `duration_minutes`, `recording_url`).

### 3. Downstream CRM and Notification Dispatch

1. The enriched payload is transmitted to the n8n post-call webhook (`/webhook/postcall`).
2. n8n executes parallel execution branches:
   - **Branch A (CRM Ingestion)**: Appends or updates the call record in Google Sheets under the `Calls` tab. Configured with `continueOnFail: true` to prevent CRM outages from halting alerts.
   - **Branch B (Alert Routing)**: Evaluates classification status:
     - `Hot`: Dispatches high-urgency notifications to sales leadership via Slack Incoming Webhook with direct recording playback links and extracted timeline data.
     - `Warm` / `Cold`: Routes records to nurture streams or general logging channels.

---

## API Specification and Data Contracts

### Inbound Call Trigger (`POST /api/trigger-call`)

Dispatches an outbound voice screening call to a specified destination.

#### Request Schema

```json
{
  "name": "Jane Doe",
  "phone_number": "+17372508034"
}
```

#### Response Schema (`HTTP 200 OK`)

```json
{
  "call_id": "c1f7b9e0-82a1-4321-9988-abcdef123456",
  "status": "queued",
  "message": "Calling your device now... pick up to test live conversation."
}
```

---

### Vapi Mid-Call Tool Hook (`POST /vapi-tool-call`)

Accepts Vapi runtime tool execution requests. Supports both modern `message.toolCallList` and legacy flat structures.

#### Request Payload Sample

```json
{
  "message": {
    "type": "tool-calls",
    "toolCallList": [
      {
        "id": "call_fn_001",
        "type": "function",
        "function": {
          "name": "send_midcall_brochure",
          "arguments": "{}"
        }
      }
    ],
    "call": {
      "id": "vapi_session_8819",
      "customer": {
        "number": "+17372508034"
      }
    }
  }
}
```

#### Response Schema (`HTTP 200 OK`)

```json
{
  "result": "Brochure dispatched via WhatsApp"
}
```

---

### Vapi End-of-Call Webhook (`POST /vapi-end-call`)

Accepts end-of-call telemetry, coordinates LLM processing, and triggers downstream sync.

#### Enriched Payload Output Contract

```json
{
  "phone_number": "+17372508034",
  "duration_minutes": 3.5,
  "recording_url": "https://api.vapi.ai/recordings/vapi_session_8819.mp3",
  "classification": "Hot",
  "budget_mentioned": true,
  "timeline": "Immediate (within 2 weeks)",
  "intent_reasoning": "The prospect confirmed available budget and requested immediate onboarding for inbound voice automation.",
  "key_pain_points": [
    "High call drop-off rates",
    "Manual CRM entry lag"
  ]
}
```

---

### Structured Extraction Schema

The LLM extraction layer adheres to the following JSON schema:

```json
{
  "type": "object",
  "properties": {
    "classification": {
      "type": "string",
      "enum": ["Hot", "Warm", "Cold"]
    },
    "budget_mentioned": {
      "type": "boolean"
    },
    "timeline": {
      "type": "string"
    },
    "intent_reasoning": {
      "type": "string"
    },
    "key_pain_points": {
      "type": "array",
      "items": {
        "type": "string"
      }
    }
  },
  "required": [
    "classification",
    "budget_mentioned",
    "timeline",
    "intent_reasoning",
    "key_pain_points"
  ]
}
```

---

## Resilience, Fault Tolerance, and Edge Cases

| Failure Mode | Mitigation Strategy | Architectural Guarantee |
| :--- | :--- | :--- |
| **Primary LLM Outage / Rate Limit** | Transparent catch block in `intelligence.py` transfers transcript directly to Groq fallback model. | Zero downtime for end-call analysis pipeline. |
| **Malformed Transcript Data** | Schema validation checks presence and types of all fields; applies sane defaults (`classification: "Cold"`, `budget_mentioned: False`) if parsing fails. | Predictable downstream consumer payloads. |
| **n8n Webhook Unavailability** | FastAPI wraps webhook dispatch in `try/except httpx.RequestError` with non-blocking logging. | Live voice calls and Vapi webhook acknowledgements are never blocked or dropped. |
| **Google Sheets 404 / Permission Error** | n8n workflow executes Google Sheets node with `continueOnFail: true` and runs Slack alerts on a parallel branch. | High-priority lead alerts reach human operators even during CRM credential or quota errors. |
| **Twilio API Delivery Failure** | Mid-call dispatch node specifies `neverError: true` and logs failure to Slack audit channel. | Speech agent call session is not interrupted by messaging gateway issues. |

---

## Repository Structure

```text
VoiceOps-Engine/
├── n8n_workflows/
│   ├── midcall_brochure.json         # Workflow: Twilio WhatsApp dispatch and Slack audit
│   └── postcall_intelligence.json    # Workflow: Priority routing, Slack alert, and CRM sync
├── static/
│   └── index.html                    # Operational dashboard for manual call testing
├── config.py                         # Pydantic BaseSettings management and env validation
├── intelligence.py                   # Multi-provider LLM extraction and structured fallback
├── main.py                           # FastAPI gateway, webhook controllers, lifecycle state
├── integration_test.py               # End-to-end integration test runner (10/10 automated suite)
├── smoke_test.py                     # Rapid API and model connectivity validation script
├── requirements.txt                  # Locked Python dependency manifest
├── .env.example                      # Sanitized environment configuration template
├── .gitignore                        # Strict secret, database, and cache exclusion patterns
└── README.md                         # Technical system specification
```

---

## Environment Configuration

Copy `.env.example` to `.env` and provide environment-specific credentials:

```bash
cp .env.example .env
```

| Parameter | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `VAPI_API_KEY` | String | Yes | Vapi.ai organizational API token |
| `VAPI_ASSISTANT_ID` | UUID | Yes | Target Vapi assistant profile identifier |
| `VAPI_PHONE_NUMBER_ID` | UUID | Yes | Telephony phone number resource ID |
| `GEMINI_API_KEY` | String | Yes | Google AI Studio API key for structured extraction |
| `GROQ_API_KEY` | String | Yes | Groq Cloud API key for high-speed fallback inference |
| `N8N_MIDCALL_WEBHOOK_URL` | URI | Yes | Target webhook endpoint for in-call brochure tool |
| `N8N_POSTCALL_WEBHOOK_URL`| URI | Yes | Target webhook endpoint for post-call data sync |
| `SLACK_WEBHOOK_URL` | URI | Optional | Incoming Webhook URL for sales notification channels |
| `GOOGLE_SHEET_ID` | String | Optional | Unique spreadsheet identifier for call record storage |
| `APP_ENV` | String | No | Application environment (`development`, `production`) |
| `HOST` | String | No | Server binding interface (default: `0.0.0.0`) |
| `PORT` | Integer | No | Server listening port (default: `8000`) |

---

## Local Setup and Deployment

### Prerequisites

- Python 3.11 or higher
- Node.js 18+ (for local n8n execution)
- Active accounts for Vapi.ai, Google Cloud, Groq, and Twilio

### 1. Application Runtime Installation

```bash
# Clone repository
git clone https://github.com/Devananditha/VoiceOps-Engine.git
cd VoiceOps-Engine

# Initialize virtual environment
python -m venv venv
source venv/bin/activate  # Windows: .\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Workflow Orchestration Setup

```bash
# Start local n8n instance
npx n8n
```

1. Open `http://localhost:5678` in a web browser.
2. Navigate to **Workflows > Import from File**:
   - Upload `n8n_workflows/midcall_brochure.json`
   - Upload `n8n_workflows/postcall_intelligence.json`
3. Configure the **HTTP Basic Auth** credential for Twilio:
   - Account SID: Your Twilio Account SID
   - Password: Your Twilio Auth Token
4. Configure the Google Sheets Service Account credential:
   - In Google Cloud Console, create a Service Account and download the JSON key.
   - Attach the key to the Google Sheets credential in n8n.
   - Share your Google Sheet with the Service Account email as an **Editor**.
   - Ensure the sheet contains a worksheet named `Calls` with required headers.
5. Set both workflows to **Active**.

### 3. Server Startup

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Verify service status:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{
  "status": "ok",
  "service": "VoiceOps_Engine",
  "version": "1.0.0",
  "environment": "development"
}
```

---

## Automated Verification and Testing

The repository includes an end-to-end integration test runner (`integration_test.py`) that exercises all application components without requiring manual phone calls.

### Running the Test Suite

```bash
python integration_test.py
```

### Test Suite Coverage

```text
== 0. Pre-flight Check ==
  [PASS]  Server reachable  status=ok

== 1. Mid-Call Webhook: POST /vapi-tool-call ==
  [PASS]  Tool call accepted  result='Brochure dispatched via WhatsApp'

== 2. Post-Call Sync: POST /vapi-end-call ==
  [PASS]  End-call pipeline complete  classification='Hot'
  [PASS]  Classification valid  'Hot'
  [PASS]  Budget field valid  budget_mentioned=True
  [PASS]  Pain points extracted  2 items
  [PASS]  Phone number passthrough  '+17372508034'
  [PASS]  Recording URL passthrough  correct

== 3. n8n Webhook Reachability ==
  [PASS]  n8n Mid-call webhook reachable  HTTP 200
  [PASS]  n8n Post-call webhook reachable  HTTP 200

== Summary ==
  10 passed  0 warnings  0 failed  (10 total)
```

---

## Security and Compliance Posture

- **Zero Secret Ingestion**: Private keys, authentication tokens, and environment configs are excluded via `.gitignore` rules covering `.env`, `service_account.json`, and wildcard credential patterns.
- **Workflow Sanitization**: All exported n8n workflow definitions use generic placeholders (`YOUR_TWILIO_ACCOUNT_SID`, `YOUR_SLACK_WEBHOOK_URL`). Real access credentials are never stored in version control.
- **Scoped Telephony Boundaries**: Phone numbers accepted by `/api/trigger-call` must satisfy strict E.164 regular expression parsing (`^\+[1-9]\d{1,14}$`) prior to upstream dispatch.
- **Stateless Webhook Handling**: Inbound webhook endpoints do not persist conversational audio or personally identifiable information (PII) on the local host filesystem.
