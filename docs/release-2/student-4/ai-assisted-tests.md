# AI-assisted endpoint tests (Budget, Student 4)

- Generated: 2026-10-20 02:34 UTC by `budget-service/tests/ai_test_gen.py`
- Model: `qwen2.5:7b` via Ollama (`/api/chat`, JSON mode, temperature 0.2, num_predict 2048)
- Parsed cases: 8

## Prompt

System:
```text
You are a senior QA engineer. Reply with a single JSON object only.
```
User:
```text
Propose edge-case API tests for a Flask budget service.

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
For POST cases give the full body. For PUT cases give only the update body (the id is filled in).
```

## Raw model response

```json
{
  "cases": [
    {
      "name": "empty_trip_reference",
      "method": "POST",
      "body": {
        "expense": "lunch",
        "category": "food",
        "estimated_cost": 10,
        "destination_id": "NYC"
      },
      "expected_status": 400,
      "why": "trip_reference is required and cannot be empty"
    },
    {
      "name": "too_long_expense",
      "method": "POST",
      "body": {
        "trip_reference": "ref",
        "expense": "<x*201>",
        "category": "food",
        "estimated_cost": 10,
        "destination_id": "NYC"
      },
      "expected_status": 400,
      "why": "expense cannot exceed 200 characters"
    },
    {
      "name": "negative_estimated_cost",
      "method": "POST",
      "body": {
        "trip_reference": "ref",
        "expense": "lunch",
        "category": "food",
        "estimated_cost": -10,
        "destination_id": "NYC"
      },
      "expected_status": 400,
      "why": "estimated_cost must be a non-negative number"
    },
    {
      "name": "null_status",
      "method": "POST",
      "body": {
        "trip_reference": "ref",
        "expense": "lunch",
        "category": "food",
        "estimated_cost": 10,
        "destination_id": "NYC",
        "status": null
      },
      "expected_status": 400,
      "why": "status must be one of Planned, Paid, Cancelled"
    },
    {
      "name": "invalid_category",
      "method": "POST",
      "body": {
        "trip_reference": "ref",
        "expense": "lunch",
        "category": "<x*41>",
        "estimated_cost": 10,
        "destination_id": "NYC"
      },
      "expected_status": 400,
      "why": "category cannot exceed 40 characters"
    },
    {
      "name": "empty_category",
      "method": "POST",
      "body": {
        "trip_reference": "ref",
        "expense": "lunch",
        "category": "",
        "estimated_cost": 10,
        "destination_id": "NYC"
      },
      "expected_status": 400,
      "why": "category cannot be an empty string"
    },
    {
      "name": "update_nonexistent_id",
      "method": "PUT",
      "body": {
        "trip_reference": "nonexistent",
        "expense": "lunch",
        "category": "food",
        "estimated_cost": 10,
        "destination_id": "NYC"
      },
      "expected_status": 404,
      "why": "unknown id should return 404"
    },
    {
      "name": "update_only_status",
      "method": "PUT",
      "body": {
        "status": "Paid"
      },
      "expected_status": 200,
      "why": "valid update of status field"
    }
  ]
}
```

## Review (filled in by a person)



Reviewed 2026-10-20 against the live containers (`docker compose`, budget-be :5004 / budget-db :6004).

| # | Proposed case | Expected | Verdict | Reason |
|---|---|---|---|---|
| 1 | `empty_trip_reference` | 400 | Rejected | Body omits the field instead of sending `""`; duplicates the existing missing-field test |
| 2 | `too_long_expense` (201 chars) | 400 | **Accepted** | New length boundary; passes live |
| 3 | `negative_estimated_cost` (-10) | 400 | Rejected | Duplicates the existing `-5` case |
| 4 | `null_status` | 400 | **Accepted** | New: explicit `null` for an optional field; passes live |
| 5 | `invalid_category` (41 chars) | 400 | **Accepted** | New length boundary; passes live |
| 6 | `empty_category` (`""`) | 400 | Rejected | Already caught by the required-field check, and the blank-category case is covered |
| 7 | `update_nonexistent_id` | 404 | Rejected | Duplicates the unknown-id test |
| 8 | `update_only_status` | 200 | Rejected | Covered by the existing valid-update test |

Accepted cases are in `budget-service/tests/test_endpoints_ai_proposed.py` (markers `live` and
`ai_proposed`). Each one also checks that nothing was persisted. Only `trip_reference` was changed (to the
module's test tag) so the records can be cleaned up.

**Earlier attempt (same day, kept for transparency).** The first prompt asked for 10 cases and let the
model write long strings literally. qwen2.5:7b returned 2 cases: one repeated an existing test, and the JSON
broke while it was writing a long `trip_reference`. The prompt was then changed to ask for 8 cases, to use
`"<x*N>"` placeholders that the script expands, and to allow `num_predict` 2048. The run above is the
second attempt.

**What the AI added:** 3 of 8 proposals were new (boundary lengths and an explicit `null`). The other 5
repeated cases that were already covered, so every proposal still needs a person to review it.
