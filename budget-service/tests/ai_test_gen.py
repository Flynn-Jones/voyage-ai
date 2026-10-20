"""Ask the local Ollama model for extra edge cases for POST /expenses and PUT /expenses/<id>.

    python budget-service/tests/ai_test_gen.py [--out docs/release-2/student-4/ai-assisted-tests.md]

Writes the exact prompt, the raw model response and the parsed cases to the output file. The
cases are NOT trusted automatically: a person reviews them, runs them against the live API, and
copies the useful ones into test_endpoints_ai_proposed.py (marked ai_proposed). The review
outcome is recorded in the same Markdown file.
Env: OLLAMA_BASE_URL (default http://localhost:11434), OLLAMA_MODEL (default qwen2.5:7b).
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

OLLAMA = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
ROOT = Path(__file__).resolve().parents[2]

SYSTEM = "You are a senior QA engineer. Reply with a single JSON object only."
PROMPT = """Propose edge-case API tests for a Flask budget service.

Endpoints:
- POST /expenses  body {trip_reference, expense, category, estimated_cost, destination_id, actual_cost?, status?}
  Required: trip_reference, expense, category, estimated_cost, destination_id.
  Valid: 201. Missing or invalid fields: 400.
- PUT /expenses/<id>  partial body with any of the same fields.
  Valid: 200. Unknown id: 404. Invalid data or no updatable field: 400.
Rules: text fields are non-empty strings (trip_reference <=120 chars, expense <=200, category <=40,
destination_id <=80); estimated_cost and actual_cost are numbers >= 0 (actual_cost may be null);
status is one of Planned, Paid, Cancelled.

Already covered (do NOT repeat): each required field missing; estimated_cost "abc", -5, true;
actual_cost "lots"; status "Maybe"; blank category; valid update; unknown id; PUT {}.

Never write a long string literally: write the placeholder "<x*N>" (for example "<a*121>" means
121 letters a); the test harness expands it.

Return 8 NEW cases as JSON:
{"cases": [{"name": "snake_case_name", "method": "POST|PUT", "body": {...},
            "expected_status": 201|200|400|404, "why": "one sentence"}]}
For POST cases give the full body. For PUT cases give only the update body (the id is filled in)."""


PLACEHOLDER = re.compile(r"^<(.)\*(\d+)>$")


def expand(value):
    """Turn the model's "<a*121>" placeholders into real strings (recursively)."""
    if isinstance(value, str):
        match = PLACEHOLDER.match(value)
        return match.group(1) * int(match.group(2)) if match else value
    if isinstance(value, dict):
        return {k: expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v) for v in value]
    return value


def ask() -> str:
    response = requests.post(f"{OLLAMA}/api/chat", timeout=180, json={
        "model": MODEL, "stream": False, "format": "json", "options": {"temperature": 0.2, "num_predict": 2048},
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": PROMPT}],
    })
    response.raise_for_status()
    return response.json()["message"]["content"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "docs" / "release-2" / "student-4" / "ai-assisted-tests.md"))
    args = parser.parse_args(argv)
    raw = ask()
    try:
        cases = expand(json.loads(raw).get("cases", []))
    except ValueError:
        cases = []
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out.write_text("\n".join([
        "# AI-assisted endpoint tests (Budget, Student 4)",
        "",
        f"- Generated: {stamp} by `budget-service/tests/ai_test_gen.py`",
        f"- Model: `{MODEL}` via Ollama (`/api/chat`, JSON mode, temperature 0.2, num_predict 2048)",
        f"- Parsed cases: {len(cases)}",
        "",
        "## Prompt",
        "",
        "System:",
        "```text", SYSTEM, "```",
        "User:",
        "```text", PROMPT, "```",
        "",
        "## Raw model response",
        "",
        "```json", raw.strip(), "```",
        "",
        "## Review (filled in by a person)",
        "",
        "_Each case is run against the live API; accepted ones go to `tests/test_endpoints_ai_proposed.py`._",
        "",
    ]) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(cases)} cases)")
    for case in cases:
        print(f"- {case.get('method')} {case.get('name')}: expect {case.get('expected_status')} - {case.get('why')}")
    return 0 if cases else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    sys.exit(main())
