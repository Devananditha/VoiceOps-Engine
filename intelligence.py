"""
VoiceOps_Engine — Intelligence Module
======================================
Provides LLM-powered call analysis with a Gemini → Groq fallback chain.

Primary:  Google Gemini (google-genai)
Fallback: Groq  llama-3.3-70b-versatile  (json_object mode)

The returned dict always matches the schema::

    {
        "classification":   "Hot" | "Warm" | "Cold",
        "budget_mentioned": bool,
        "timeline":         str,
        "intent_reasoning": str,   # exactly 2 sentences
        "key_pain_points":  list[str],
    }
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Literal

from google import genai
from google.genai import types as genai_types
from groq import Groq

from config import get_settings

logger = logging.getLogger("voiceops_engine.intelligence")


def _settings():
    """Lazy accessor — avoids calling get_settings() at import time."""
    return get_settings()


# ── Shared prompt ──────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """\
You are an expert sales-call analyst. Given a call transcript, extract the \
following fields and return ONLY a valid JSON object — no markdown fences, \
no commentary.

Schema:
{
  "classification":   "Hot" | "Warm" | "Cold",
  "budget_mentioned": true | false,
  "timeline":         "<string — when the prospect intends to act>",
  "intent_reasoning": "<exactly 2 sentences explaining the classification>",
  "key_pain_points":  ["<pain point>", ...]
}
"""

_REQUIRED_KEYS: set[str] = {
    "classification",
    "budget_mentioned",
    "timeline",
    "intent_reasoning",
    "key_pain_points",
}

_VALID_CLASSIFICATIONS: set[str] = {"Hot", "Warm", "Cold"}


# ── Validation helper ──────────────────────────────────────────────────────────

def _validate(data: dict) -> dict:
    """
    Ensures the parsed dict satisfies the required schema.
    Raises ValueError with a descriptive message on the first violation found.
    """
    missing = _REQUIRED_KEYS - data.keys()
    if missing:
        raise ValueError(f"LLM response missing required keys: {missing}")

    clf = data["classification"]
    if clf not in _VALID_CLASSIFICATIONS:
        raise ValueError(
            f"classification must be one of {_VALID_CLASSIFICATIONS}, got {clf!r}"
        )

    if not isinstance(data["budget_mentioned"], bool):
        raise ValueError("budget_mentioned must be a boolean")

    if not isinstance(data["key_pain_points"], list):
        raise ValueError("key_pain_points must be a list")

    return data


# ── Gemini path ────────────────────────────────────────────────────────────────

async def _analyze_with_gemini(transcript: str) -> dict:
    """
    Calls Google Gemini (gemini-2.0-flash) and returns the validated dict.
    Uses a JSON response schema to constrain output format.
    Raises on any API or parsing error so the caller can fall back to Groq.
    """
    cfg = _settings()
    if not cfg.gemini_api_key or cfg.gemini_api_key.startswith("your_"):
        raise RuntimeError("GEMINI_API_KEY is not configured — skipping Gemini.")

    client = genai.Client(api_key=cfg.gemini_api_key)

    response = await client.aio.models.generate_content(
        model="gemini-2.0-flash",
        contents=f"{_SYSTEM_PROMPT}\n\nTranscript:\n{transcript}",
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json",
        ),
    )

    raw = response.text.strip()
    data = json.loads(raw)
    return _validate(data)


# ── Groq fallback path ─────────────────────────────────────────────────────────

def _analyze_with_groq(transcript: str) -> dict:
    """
    Calls Groq (llama-3.3-70b-versatile) in json_object mode.
    This is a synchronous Groq SDK call — kept sync because the Groq client
    does not expose an async interface; it is fast enough for our latency budget.
    Raises on any API or parsing error.
    """
    cfg = _settings()
    if not cfg.groq_api_key or cfg.groq_api_key.startswith("your_"):
        raise RuntimeError("GROQ_API_KEY is not configured — cannot run Groq fallback.")

    client = Groq(api_key=cfg.groq_api_key)

    completion = client.chat.completions.create(
        model="openai/gpt-oss-20b",  # confirmed available on this account; qwen/qwen3.6-27b rate-limited
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Transcript:\n{transcript}",
            },
        ],
    )

    raw = completion.choices[0].message.content.strip()
    data = json.loads(raw)
    return _validate(data)


# ── Public API ─────────────────────────────────────────────────────────────────

async def analyze_call(transcript: str) -> dict:
    """
    Analyse a call transcript and return a structured intelligence dict.

    Strategy:
        1. Attempt extraction via Google Gemini.
        2. On *any* exception, log a warning and retry with Groq.
        3. If Groq also fails, the exception propagates to the caller
           so the endpoint can return a clean 502 rather than silently
           storing corrupt data.

    Args:
        transcript: Raw text transcript of the voice call.

    Returns:
        A dict matching the schema described in the module docstring.

    Raises:
        Exception: If both Gemini and Groq fail — the caller must handle this.
    """
    # ── Primary: Gemini ────────────────────────────────────────────────────────
    try:
        result = await _analyze_with_gemini(transcript)
        logger.info("Call analysed via Gemini — classification=%s", result["classification"])
        return result

    except Exception as gemini_exc:
        logger.warning(
            "Gemini analysis failed (%s: %s) — falling back to Groq.",
            type(gemini_exc).__name__,
            gemini_exc,
        )

    # ── Fallback: Groq (run sync SDK in a thread — avoids blocking the event loop) ───
    result = await asyncio.to_thread(_analyze_with_groq, transcript)
    logger.info(
        "Call analysed via Groq (fallback) — classification=%s",
        result["classification"],
    )
    return result
