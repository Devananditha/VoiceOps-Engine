"""
VoiceOps — Full Integration Test
=================================
Tests both webhook pipelines end-to-end:
  1. POST /vapi-tool-call  → triggers n8n mid-call → Twilio WhatsApp + Slack
  2. POST /vapi-end-call   → LLM analysis → n8n post-call → Slack + Google Sheets
"""

from __future__ import annotations
import json
import sys
import httpx
import time

# Force UTF-8 on Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = "http://localhost:8000"

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

passed = 0
failed = 0
warned = 0


def ok(label, detail=""):
    global passed; passed += 1
    print(f"  {GREEN}[PASS]{RESET}  {label}  {DIM}{detail}{RESET}")

def fail(label, detail=""):
    global failed; failed += 1
    print(f"  {RED}[FAIL]{RESET}  {label}  {detail}")

def warn(label, detail=""):
    global warned; warned += 1
    print(f"  {YELLOW}[WARN]{RESET}  {label}  {detail}")

def section(title):
    print(f"\n{BOLD}{CYAN}== {title} =={RESET}")

def pretty_json(data, indent=2):
    return json.dumps(data, indent=indent, ensure_ascii=False)


# =====================================================================
# 0. Pre-flight: is the server running?
# =====================================================================
section("0. Pre-flight Check")

try:
    r = httpx.get(f"{BASE}/health", timeout=5)
    if r.status_code == 200:
        ok("Server reachable", f"status={r.json().get('status')}")
    else:
        fail("Server health", f"status_code={r.status_code}")
except httpx.ConnectError:
    fail("Server unreachable", "Is uvicorn running on port 8000?")
    sys.exit(1)


# =====================================================================
# 1. Mid-Call Webhook Test (POST /vapi-tool-call)
# =====================================================================
section("1. Mid-Call Webhook: POST /vapi-tool-call")

midcall_payload = {
    "message": {
        "type": "tool-calls",
        "toolCallList": [
            {
                "id": "call_test_001",
                "type": "function",
                "function": {
                    "name": "send_midcall_brochure",
                    "arguments": "{}"
                }
            }
        ],
        "call": {
            "id": "test-call-id-12345",
            "customer": {
                "number": "+17372508034"
            }
        }
    }
}

print(f"\n  {DIM}Payload:{RESET}")
print(f"  {DIM}{pretty_json(midcall_payload)}{RESET}\n")

try:
    r = httpx.post(
        f"{BASE}/vapi-tool-call",
        json=midcall_payload,
        timeout=15,
    )
    print(f"  {BOLD}Response:{RESET} HTTP {r.status_code}")
    data = r.json()
    print(f"  {DIM}{pretty_json(data)}{RESET}\n")

    if r.status_code == 200:
        result_msg = data.get("result", "")
        if "Brochure dispatched" in result_msg:
            ok("Tool call accepted", f"result='{result_msg}'")
        else:
            warn("Tool call accepted but unexpected result", f"result='{result_msg}'")
    else:
        fail("Tool call failed", f"HTTP {r.status_code}: {r.text[:200]}")

except Exception as exc:
    fail("Tool call request error", f"{type(exc).__name__}: {exc}")


# =====================================================================
# 2. Post-Call Webhook Test (POST /vapi-end-call)
# =====================================================================
section("2. Post-Call Sync: POST /vapi-end-call")

postcall_payload = {
    "phone_number": "+17372508034",
    "duration_minutes": 3.5,
    "recording_url": "https://api.vapi.ai/mock-recording.mp3",
    "classification": "Hot",
    "budget_mentioned": True,
    "timeline": "Immediate (2 weeks)",
    "intent_reasoning": "Client urgently needs an AI voice agent deployed for incoming lead screening.",
    "key_pain_points": ["High call drop-off", "Manual data entry lag"]
}

# Wrap in VAPI legacy flat shape (this is what our endpoint also accepts)
print(f"\n  {DIM}Payload:{RESET}")

# We need to send a payload that goes through the LLM pipeline.
# Use the proper end-call shape with a transcript.
endcall_payload = {
    "transcript": (
        "Agent: Hi, this is the AI screener from VoiceOps. How are you today?\n"
        "Prospect: Good, thanks. We're looking to deploy an AI voice agent urgently.\n"
        "Agent: Great. What's your timeline for this?\n"
        "Prospect: Immediate, ideally within 2 weeks. Budget isn't a constraint "
        "-- we just need it done right. Our main pain points are high call drop-off "
        "rates and manual data entry lag.\n"
        "Agent: I understand. Let me send you some information about our platform.\n"
        "Prospect: That would be helpful, thank you."
    ),
    "customer": {"number": "+17372508034"},
    "durationMinutes": 3.5,
    "recordingUrl": "https://api.vapi.ai/mock-recording.mp3"
}

print(f"  {DIM}{pretty_json(endcall_payload)}{RESET}\n")

try:
    start = time.time()
    r = httpx.post(
        f"{BASE}/vapi-end-call",
        json=endcall_payload,
        timeout=120,  # LLM calls can be slow
    )
    elapsed = time.time() - start

    print(f"  {BOLD}Response:{RESET} HTTP {r.status_code}  ({elapsed:.1f}s)")
    data = r.json()
    print(f"  {DIM}{pretty_json(data)}{RESET}\n")

    if r.status_code == 200:
        # Validate all expected fields
        expected_keys = {
            "classification", "budget_mentioned", "timeline",
            "intent_reasoning", "key_pain_points",
            "phone_number", "duration_minutes", "recording_url"
        }
        present = expected_keys & data.keys()
        missing = expected_keys - data.keys()

        if not missing:
            ok("End-call pipeline complete", f"classification='{data['classification']}'")
        else:
            warn("End-call response missing keys", f"missing={missing}")

        # Check classification value
        clf = data.get("classification", "")
        if clf in ("Hot", "Warm", "Cold"):
            ok("Classification valid", f"'{clf}'")
        else:
            fail("Classification invalid", f"got '{clf}'")

        # Check budget
        budget = data.get("budget_mentioned")
        if isinstance(budget, bool):
            ok("Budget field valid", f"budget_mentioned={budget}")
        else:
            warn("Budget field type", f"expected bool, got {type(budget).__name__}")

        # Check pain points
        pp = data.get("key_pain_points", [])
        if isinstance(pp, list) and len(pp) > 0:
            ok("Pain points extracted", f"{len(pp)} items: {pp}")
        else:
            warn("Pain points empty or wrong type", f"got {pp!r}")

        # Check metadata passthrough
        if data.get("phone_number") == "+17372508034":
            ok("Phone number passthrough", "'+17372508034'")
        else:
            warn("Phone number mismatch", f"got '{data.get('phone_number')}'")

        if data.get("recording_url") == "https://api.vapi.ai/mock-recording.mp3":
            ok("Recording URL passthrough", "correct")
        else:
            warn("Recording URL mismatch", f"got '{data.get('recording_url')}'")

    elif r.status_code == 502:
        detail = data.get("detail", "")
        fail("LLM pipeline failed (502)", f"detail: {detail[:200]}")

    else:
        fail("Unexpected status", f"HTTP {r.status_code}: {r.text[:300]}")

except httpx.ReadTimeout:
    fail("Request timed out", "LLM call exceeded 120s")
except Exception as exc:
    fail("Request error", f"{type(exc).__name__}: {exc}")


# =====================================================================
# 3. Direct n8n Webhook Probe (optional — check if n8n is listening)
# =====================================================================
section("3. n8n Webhook Reachability")

for label, url in [
    ("Mid-call", "http://localhost:5678/webhook/midcall"),
    ("Post-call", "http://localhost:5678/webhook/postcall"),
]:
    try:
        r = httpx.post(url, json={"probe": True}, timeout=15)
        if r.status_code < 500:
            ok(f"n8n {label} webhook reachable", f"HTTP {r.status_code}")
        else:
            warn(f"n8n {label} webhook returned error", f"HTTP {r.status_code}")
    except httpx.ConnectError:
        warn(f"n8n {label} webhook unreachable", "Is n8n running on port 5678?")
    except Exception as exc:
        warn(f"n8n {label} webhook", f"{type(exc).__name__}: {exc}")


# =====================================================================
# Summary
# =====================================================================
section("Summary")
total = passed + failed + warned
print(f"  {GREEN}{passed} passed{RESET}  "
      f"{YELLOW}{warned} warnings{RESET}  "
      f"{RED}{failed} failed{RESET}  "
      f"({total} total)\n")

if failed:
    print(f"{RED}Some tests failed -- review output above.{RESET}")
    sys.exit(1)
elif warned:
    print(f"{YELLOW}Warnings present -- some services may not be fully connected.{RESET}")
    sys.exit(0)
else:
    print(f"{GREEN}All tests passed -- full pipeline is operational!{RESET}")
    sys.exit(0)
