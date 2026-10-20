"""AI-proposed edge cases (qwen2.5:7b via tests/ai_test_gen.py), reviewed by a person and run live.

Prompt, raw response and the accept/reject review: docs/release-2/student-4/ai-assisted-tests.md.
Only trip_reference was changed (to this module's test tag) so the records can be cleaned up;
bodies and expected statuses are as the model proposed them.
"""
import os
import uuid

import pytest
import requests

pytestmark = [pytest.mark.live, pytest.mark.ai_proposed]

BE = os.environ.get("BUDGET_BE_URL", "http://localhost:5004").rstrip("/")
DB = os.environ.get("BUDGET_DB_URL", "http://localhost:6004").rstrip("/")
TIMEOUT = 10
TAG = f"TEST-AI-{uuid.uuid4().hex[:8].upper()}"

AI_PROPOSED = [
    # name (as proposed), body, expected status
    ("too_long_expense",
     {"trip_reference": TAG, "expense": "x" * 201, "category": "food", "estimated_cost": 10, "destination_id": "NYC"},
     400),
    ("null_status",
     {"trip_reference": TAG, "expense": "lunch", "category": "food", "estimated_cost": 10, "destination_id": "NYC",
      "status": None},
     400),
    ("invalid_category",
     {"trip_reference": TAG, "expense": "lunch", "category": "x" * 41, "estimated_cost": 10, "destination_id": "NYC"},
     400),
]


@pytest.fixture(scope="module", autouse=True)
def live_stack():
    try:
        requests.get(f"{BE}/", timeout=TIMEOUT).raise_for_status()
    except requests.RequestException as exc:
        pytest.skip(f"Budget containers not reachable ({exc})")
    yield
    for row in requests.get(f"{DB}/expenses", params={"trip_reference": TAG}, timeout=TIMEOUT).json():
        requests.delete(f"{DB}/expenses/{row['id']}", timeout=TIMEOUT)


@pytest.mark.parametrize("name, body, expected", AI_PROPOSED, ids=[case[0] for case in AI_PROPOSED])
def test_ai_proposed_post_edge_case(name, body, expected):
    """AI-proposed: see the module docstring."""
    response = requests.post(f"{BE}/expenses", json=body, timeout=TIMEOUT)
    assert response.status_code == expected, f"{name}: {response.status_code} {response.text[:200]}"
    stored = requests.get(f"{DB}/expenses", params={"trip_reference": TAG}, timeout=TIMEOUT).json()
    assert stored == [], f"{name}: a rejected request must not persist anything"
