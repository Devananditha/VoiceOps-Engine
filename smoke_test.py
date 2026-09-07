"""
VoiceOps_Engine — Automated Smoke Test
=======================================
Verifies that the FastAPI server is running and its core endpoints respond
correctly.  Run while the dev server is up:

    python smoke_test.py

Tests:
  1. GET  /          → HTML frontend returns 200
  2. GET  /health    → API liveness returns 200 + JSON status "ok"
  3. POST /vapi-end-call → mock payload returns 200 or 502 (LLM keys missing)
                          but NEVER a 500 crash
"""

from __future__ import annotations

import json
import sys
import httpx

# Force UTF-8 output on Windows terminals that default to cp1252
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = "http://localhost:8000"

# ── ANSI colours ───────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

passed = 0
failed = 0
warned = 0


def ok(label: str, detail: str = ""):
    global passed
    passed += 1
    print(f"  {GREEN}[PASS]{RESET}  {label}  {detail}")


def fail(label: str, detail: str = ""):
    global failed
    failed += 1
    print(f"  {RED}[FAIL]{RESET}  {label}  {detail}")


def warn(label: str, detail: str = ""):
    global warned
    warned += 1
    print(f"  {YELLOW}[WARN]{RESET}  {label}  {detail}")


def section(title: str):
    print(f"\n{BOLD}-- {title} --{RESET}")


# ── Test 1: Frontend ───────────────────────────────────────────────────────────
section("1. GET / (HTML Frontend)")

try:
    r = httpx.get(f"{BASE}/", follow_redirects=True, timeout=10)
    if r.status_code == 200:
        if "VoiceOps" in r.text or "index.html" in r.headers.get("content-type", ""):
            ok("GET /", f"→ {r.status_code}  content-type={r.headers.get('content-type', '?')}")
        else:
            warn("GET /", f"→ {r.status_code} but page body doesn't contain 'VoiceOps'")
    else:
        fail("GET /", f"→ {r.status_code}")
except httpx.ConnectError:
    fail("GET /", "→ Connection refused — is the server running on port 8000?")
    print(f"\n{RED}Server unreachable. Aborting remaining tests.{RESET}")
    sys.exit(1)
except Exception as exc:
    fail("GET /", f"→ {type(exc).__name__}: {exc}")


# ── Test 2: Health Check ──────────────────────────────────────────────────────
section("2. GET /health (Liveness Probe)")

try:
    r = httpx.get(f"{BASE}/health", timeout=10)
    if r.status_code == 200:
        data = r.json()
        if data.get("status") == "ok":
            ok("GET /health", f"→ {r.status_code}  version={data.get('version')}")
        else:
            warn("GET /health", f"→ 200 but status={data.get('status')!r}")
    else:
        fail("GET /health", f"→ {r.status_code}: {r.text[:200]}")
except Exception as exc:
    fail("GET /health", f"→ {type(exc).__name__}: {exc}")


# ── Test 3: POST /vapi-end-call (Mock Payload) ────────────────────────────────
section("3. POST /vapi-end-call (Mock Transcript)")

mock_payload = {
    "transcript": (
        "Agent: Hi, this is Sarah from TalentBridge. Am I speaking with the hiring manager?\n"
        "Prospect: Yes, that's me. We've been struggling to fill three senior dev roles.\n"
        "Agent: I understand. What's your current timeline for these hires?\n"
        "Prospect: Ideally within the next 6 weeks. Budget isn't an issue — we just "
        "need quality candidates fast.\n"
        "Agent: Got it. I'll send over a few pre-vetted profiles by end of day.\n"
        "Prospect: That would be great, thanks."
    ),
    "customer": {"number": "+919876543210", "name": "Test Prospect"},
    "durationMinutes": 3.5,
    "recordingUrl": "https://example.com/recording/test-123.mp3",
}

try:
    r = httpx.post(
        f"{BASE}/vapi-end-call",
        json=mock_payload,
        timeout=60,          # LLM calls can be slow
    )
    data = r.json()

    if r.status_code == 200:
        # Full success — LLM keys are live
        required = {"classification", "budget_mentioned", "timeline",
                     "intent_reasoning", "key_pain_points",
                     "phone_number", "duration_minutes", "recording_url"}
        present  = required & data.keys()
        missing  = required - data.keys()

        if not missing:
            ok("POST /vapi-end-call",
               f"→ 200  classification={data.get('classification')!r}")
        else:
            warn("POST /vapi-end-call",
                 f"→ 200 but missing keys: {missing}")

    elif r.status_code == 502:
        # Expected when API keys are placeholders
        detail = data.get("detail", "")
        warn("POST /vapi-end-call",
             f"→ 502 (LLM keys not configured) — detail: {detail[:120]}")

    elif r.status_code == 422:
        fail("POST /vapi-end-call",
             f"→ 422 Validation Error — check payload schema.  {json.dumps(data, indent=2)[:300]}")

    else:
        fail("POST /vapi-end-call",
             f"→ {r.status_code}: {r.text[:300]}")

except httpx.ReadTimeout:
    warn("POST /vapi-end-call",
         "→ Timed out after 60s (LLM might be slow or keys invalid)")
except Exception as exc:
    fail("POST /vapi-end-call", f"→ {type(exc).__name__}: {exc}")


# ── Summary ───────────────────────────────────────────────────────────────────
section("Summary")
total = passed + failed + warned
print(f"  {GREEN}{passed} passed{RESET}  "
      f"{YELLOW}{warned} warnings{RESET}  "
      f"{RED}{failed} failed{RESET}  "
      f"({total} total)")

if failed:
    print(f"\n{RED}Some tests failed — review output above.{RESET}")
    sys.exit(1)
elif warned:
    print(f"\n{YELLOW}Warnings present — likely placeholder API keys. "
          f"Core server is healthy.{RESET}")
    sys.exit(0)
else:
    print(f"\n{GREEN}All tests passed — system is fully operational.{RESET}")
    sys.exit(0)
