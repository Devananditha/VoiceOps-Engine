"""
VoiceOps_Engine — FastAPI Application Entry Point
=================================================
Production-ready FastAPI application with:
  • CORS middleware (configurable origins)
  • Structured startup / shutdown lifespan events
  • Health-check endpoint      GET  /health
  • Outbound call trigger      POST /api/trigger-call
  • Global exception handler for clean JSON error responses
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
from pydantic import BaseModel, field_validator

import uvicorn
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from config import get_settings
from intelligence import analyze_call

# ── Logging Setup ──────────────────────────────────────────────────────────────
settings = get_settings()

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("voiceops_engine")


# ── Application Lifespan ───────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Handles startup and shutdown logic.
    A single shared httpx.AsyncClient is created at startup and reused across
    all requests — avoids the overhead of opening a new TCP connection per call.
    """
    logger.info("🚀 VoiceOps_Engine starting up — env=%s", settings.app_env)
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        app.state.http_client = client
        yield
    # ── Shutdown ───────────────────────────────────────────────────────────────
    logger.info("🛑 VoiceOps_Engine shutting down gracefully.")


# ── FastAPI Instance ───────────────────────────────────────────────────────────
app = FastAPI(
    title="VoiceOps_Engine",
    description=(
        "AI-powered voice operations platform — "
        "orchestrates VAPI calls, Gemini LLM processing, and n8n automation."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)


# ── CORS Middleware ────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # ← lock down to specific origins in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


# ── Request Timing Middleware ──────────────────────────────────────────────────
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """Injects X-Process-Time header into every response (useful for debugging)."""
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1_000
    response.headers["X-Process-Time"] = f"{elapsed_ms:.2f}ms"
    return response


# ── Global Exception Handler ───────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Catches unhandled exceptions and returns a structured JSON error response
    instead of exposing raw stack traces to clients.
    """
    logger.exception("Unhandled exception on %s %s", request.method, request.url)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "status": "error",
            "message": "An unexpected internal error occurred.",
            "detail": str(exc) if settings.app_env != "production" else None,
        },
    )


# ── Health-Check Route ─────────────────────────────────────────────────────────
@app.get(
    "/health",
    summary="Health Check",
    description=(
        "Returns the current operational status of the VoiceOps_Engine service. "
        "Suitable for use with load-balancer probes and uptime monitors."
    ),
    response_description="Service health payload",
    tags=["System"],
    status_code=status.HTTP_200_OK,
)
async def health_check() -> dict[str, Any]:
    """
    **GET /health** — Liveness probe endpoint.

    Returns:
        - `status`: `"ok"` when the service is running normally.
        - `service`: Human-readable service name.
        - `version`: Current API version string.
        - `environment`: Active deployment environment.
    """
    logger.debug("Health check requested.")
    return {
        "status": "ok",
        "service": "VoiceOps_Engine",
        "version": app.version,
        "environment": settings.app_env,
    }


# ── Outbound Call Trigger ──────────────────────────────────────────────────────

VAPI_CALL_URL = "https://api.vapi.ai/call/phone"
_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


class CallRequest(BaseModel):
    """
    Payload accepted by POST /api/trigger-call.

    Fields:
        name         – Full name of the person being called (used to personalise
                       the assistant's opening line).
        phone_number – Destination number in E.164 format, e.g. +91XXXXXXXXXX.
    """

    name: str
    phone_number: str

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("phone_number")
    @classmethod
    def validate_e164(cls, v: str) -> str:
        v = v.strip()
        if not _E164_RE.match(v):
            raise ValueError(
                "phone_number must be in E.164 format (e.g. +91XXXXXXXXXX)"
            )
        return v


class CallResponse(BaseModel):
    """Successful response returned after VAPI accepts the outbound call."""

    call_id: str
    status: str
    message: str


@app.post(
    "/api/trigger-call",
    response_model=CallResponse,
    summary="Trigger Outbound AI Call",
    description=(
        "Initiates an outbound AI voice call via VAPI to the supplied phone number. "
        "The call is answered by the configured AI assistant."
    ),
    tags=["Calls"],
    status_code=status.HTTP_200_OK,
)
async def trigger_call(payload: CallRequest, request: Request) -> CallResponse:
    """
    **POST /api/trigger-call** — Dispatch an AI-driven outbound phone call.

    The handler:
    1. Validates name + E.164 phone number (done by Pydantic before reaching here).
    2. Constructs the VAPI call payload with assistantId, phoneNumberId,
       and a customer block containing the number and name.
    3. Sends an authenticated POST to https://api.vapi.ai/call/phone.
    4. Returns the VAPI call ID and initial status on success.
    5. Propagates any VAPI-level errors as structured HTTP 4xx/5xx responses.
    """
    logger.info(
        "Triggering outbound call → name=%r  number=%s",
        payload.name,
        payload.phone_number,
    )

    vapi_payload = {
        "assistantId": settings.vapi_assistant_id,
        "phoneNumberId": settings.vapi_phone_number_id,
        "customer": {
            "number": payload.phone_number,
            "name": payload.name,
        },
    }

    headers = {
        "Authorization": f"Bearer {settings.vapi_api_key}",
        "Content-Type": "application/json",
    }

    client: httpx.AsyncClient = request.app.state.http_client

    try:
        resp = await client.post(VAPI_CALL_URL, json=vapi_payload, headers=headers)
    except httpx.RequestError as exc:
        logger.error("Network error reaching VAPI: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not reach VAPI: {exc}",
        )

    if resp.status_code not in (200, 201):
        logger.error(
            "VAPI rejected call request — HTTP %s: %s",
            resp.status_code,
            resp.text,
        )
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"VAPI error: {resp.text}",
        )

    data = resp.json()
    call_id = data.get("id", "unknown")
    call_status = data.get("status", "queued")

    logger.info("Call dispatched — call_id=%s  status=%s", call_id, call_status)

    return CallResponse(
        call_id=call_id,
        status=call_status,
        message="Calling your device now... pick up to test live conversation.",
    )


# ── VAPI Webhook — Mid-Call Tool ──────────────────────────────────────────────
# VAPI server-url payload schema (current):
#   { "message": { "type": "tool-calls",
#                  "toolCallList": [{ "function": { "name": "...", "arguments": {...} } }] } }
# Legacy / flat shape also supported as fallback.

def _extract_tool_name(payload: dict) -> str:
    """
    Extracts the tool name from either VAPI payload shape:
      • Current:  payload["message"]["toolCallList"][0]["function"]["name"]
      • Legacy:   payload["toolCall"]["function"]["name"]  or  payload["name"]
    Returns an empty string when the name cannot be found.
    """
    # Current server-url shape
    msg = payload.get("message", {})
    tool_call_list = msg.get("toolCallList", [])
    if tool_call_list:
        name = tool_call_list[0].get("function", {}).get("name", "")
        if name:
            return name

    # Legacy / flat shape
    return (
        payload.get("toolCall", {}).get("function", {}).get("name", "")
        or payload.get("name", "")
    )


@app.post(
    "/vapi-tool-call",
    summary="VAPI Mid-Call Tool Handler",
    description=(
        "Receives tool-call events from VAPI during a live call. "
        "When the tool name is 'send_midcall_brochure', the payload is "
        "forwarded to the n8n mid-call webhook, which dispatches a brochure "
        "over WhatsApp."
    ),
    tags=["Webhooks"],
    status_code=status.HTTP_200_OK,
)
async def vapi_tool_call(request: Request) -> dict:
    """
    **POST /vapi-tool-call** — VAPI in-call tool dispatcher.

    Handles VAPI's current ``message.toolCallList`` payload shape as well as
    the legacy flat ``toolCall`` shape.  Currently supported tools:

    * ``send_midcall_brochure`` — forwards the raw VAPI payload to the n8n
      mid-call workflow, which sends a WhatsApp brochure to the prospect.

    Unknown tool names are logged and ignored with a neutral 200 so that
    the live call is never interrupted.
    """
    payload: dict = await request.json()
    tool_name = _extract_tool_name(payload)

    logger.info(
        "VAPI tool-call received — tool=%r  type=%s",
        tool_name,
        payload.get("message", {}).get("type", "unknown"),
    )

    if tool_name == "send_midcall_brochure":
        n8n_url = settings.n8n_midcall_webhook_url
        if not n8n_url or "your-n8n-instance" in n8n_url:
            logger.warning(
                "N8N_MIDCALL_WEBHOOK_URL is not configured — brochure NOT forwarded."
            )
            return {"result": "Brochure dispatched via WhatsApp"}

        client: httpx.AsyncClient = request.app.state.http_client
        try:
            resp = await client.post(n8n_url, json=payload, timeout=10.0)
            logger.info("Brochure webhook forwarded — n8n status=%s", resp.status_code)
        except httpx.RequestError as exc:
            # Non-fatal — never abort a live call over a webhook failure.
            logger.error("Could not reach n8n mid-call webhook: %s", exc)

        return {"result": "Brochure dispatched via WhatsApp"}

    logger.warning("Unhandled VAPI tool name: %r — ignoring.", tool_name)
    return {"result": f"Tool '{tool_name}' is not implemented."}


# ── VAPI Webhook — End-of-Call Summary ────────────────────────────────────────
# VAPI server-url end-of-call payload (current schema):
#   {
#     "message": {
#       "type": "end-of-call-report",
#       "artifact": { "transcript": "...", "recordingUrl": "..." },
#       "call":     { "customer": { "number": "+1..." } },
#       "durationSeconds": 240
#     }
#   }
# We also accept the flat top-level shape for backward compatibility.


def _extract_end_call_fields(raw: dict) -> tuple[str, str, float, str]:
    """
    Extracts (transcript, phone_number, duration_minutes, recording_url)
    from either VAPI payload shape.

    Returns safe empty-string / zero defaults when a field is absent.
    """
    msg = raw.get("message", {})

    if msg:  # Current server-url shape
        artifact        = msg.get("artifact", {})
        call            = msg.get("call", {})
        transcript      = artifact.get("transcript", "")
        recording_url   = artifact.get("recordingUrl", "")
        phone_number    = call.get("customer", {}).get("number", "")
        duration_sec    = float(msg.get("durationSeconds", 0))
        duration_min    = duration_sec / 60.0
    else:  # Legacy flat shape
        transcript      = raw.get("transcript", "")
        recording_url   = raw.get("recordingUrl", "")
        phone_number    = raw.get("customer", {}).get("number", "")
        duration_min    = float(raw.get("durationMinutes", 0))

    return transcript, phone_number, duration_min, recording_url


@app.post(
    "/vapi-end-call",
    summary="VAPI End-of-Call Handler",
    description=(
        "Receives the post-call report from VAPI, runs LLM-powered transcript "
        "analysis (Gemini with Groq fallback), merges the intelligence with "
        "caller metadata, and forwards the enriched payload to the n8n "
        "post-call webhook."
    ),
    tags=["Webhooks"],
    status_code=status.HTTP_200_OK,
)
async def vapi_end_call(request: Request) -> dict:
    """
    **POST /vapi-end-call** — Post-call intelligence pipeline.

    Accepts both VAPI payload shapes:

    * **Current** — fields nested under ``message.artifact`` / ``message.call``
    * **Legacy**  — flat top-level fields (``transcript``, ``customer``, etc.)

    Steps:

    1. Parse raw JSON and extract transcript, phone number, duration, recording URL.
    2. Pass transcript to :func:`intelligence.analyze_call` (Gemini → Groq).
    3. Merge LLM output with call metadata.
    4. Forward enriched dict to ``N8N_POSTCALL_WEBHOOK_URL``.
    5. Return the enriched dict to VAPI as acknowledgement.
    """
    raw: dict = await request.json()

    transcript, phone_number, duration_min, recording_url = _extract_end_call_fields(raw)

    logger.info(
        "End-of-call received — number=%s  duration=%.2f min  transcript_len=%d",
        phone_number or "unknown",
        duration_min,
        len(transcript),
    )

    # ── LLM analysis (Gemini → Groq fallback) ─────────────────────────────────
    try:
        intelligence = await analyze_call(transcript)
    except Exception as exc:
        logger.error("Both LLM providers failed for end-of-call analysis: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM analysis failed: {exc}",
        )

    # ── Merge call metadata with LLM output ────────────────────────────────────
    enriched = {
        "phone_number":     phone_number,
        "duration_minutes": round(duration_min, 2),
        "recording_url":    recording_url,
        **intelligence,
    }

    logger.info(
        "Call enriched — classification=%s  budget=%s  pain_points=%d",
        enriched.get("classification"),
        enriched.get("budget_mentioned"),
        len(enriched.get("key_pain_points", [])),
    )

    # ── Forward to n8n post-call workflow ──────────────────────────────────────
    n8n_url = settings.n8n_postcall_webhook_url
    if not n8n_url or "your-n8n-instance" in n8n_url:
        logger.warning(
            "N8N_POSTCALL_WEBHOOK_URL is not configured — enriched payload NOT forwarded."
        )
    else:
        client: httpx.AsyncClient = request.app.state.http_client
        try:
            resp = await client.post(n8n_url, json=enriched, timeout=15.0)
            logger.info("Post-call data forwarded to n8n — status=%s", resp.status_code)
        except httpx.RequestError as exc:
            # Non-fatal — enriched data is still returned to VAPI.
            logger.error("Could not reach n8n post-call webhook: %s", exc)

    return enriched


# ── Static Frontend ───────────────────────────────────────────────────────────
# Mount AFTER all API routes so FastAPI resolves /api/* and /health first.
app.mount("/", StaticFiles(directory="static", html=True), name="static")


# ── Dev Server Entry-Point ─────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.app_env == "development",
        log_level=settings.log_level.lower(),
    )
