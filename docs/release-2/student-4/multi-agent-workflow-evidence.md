# Multi-Agent workflow evidence (Budget, `budget_reconcile`)

Live runs on 2026-10-20 against the shared Multi-Agent Server (:7004) with qwen2.5:7b, the shared MCP (:7001)
and RAG (:7002) servers, and the Budget containers. The history and audit data below were pulled from the
server's API and `logs/audit.jsonl`, not written by hand. The full audit lines for these four runs are in
[audit-excerpts.jsonl](audit-excerpts.jsonl).

Lifecycle: `running` → Planner (Plan) → Worker (Act) → Reviewer (Observe) → `awaiting_human_review` →
human decision → budget-be writes approved lines (Adapt) → `applied`, or `rejected` with nothing written.

## Scenario 1 - partial approval (terminal: cli.py + curl)

- **run_id:** `run-d49503fcb1b8` · requested_by `cli:Oat` · final state **`applied`**
- **Input:** `{"trip_reference": "TRIP-1001", "budget_cap": 1300.0, "note": "", "declined_requests": []}`
- **Totals (computed in Python):** recorded $1160.00 + draft $199.50 = **$1359.50** vs cap $1300.00 → **$59.50 over**
- **Plan:** `list_expenses` (Review recorded budget lines to understand current costs.), `get_itinerary` (Check scheduled items and their costs for accuracy.)
- **Draft lines:** 1. Guided historic centre walk (Activities, $24.00); 2. Visit the local art museum (Activities, $18.50); 3. Riverside food tour (Food, $32.00); 4. Breakfast market visit (Food, $12.00); 5. Architecture and gardens tour (Activities, $45.00); 6. Evening cooking class (Food, $68.00)
- **Reviewer:** risk **high**
  - finding: Projected $1359.50 is $59.50 over the $1300.00 cap.
  - finding: Transport: 'Flight to Paris' actual $480.00 is $30.00 above its $450.00 estimate.
  - finding: Activities: draft line 2 'Visit the local art museum' may duplicate recorded 'Museum passes' (similarity 1.0).
  - recommendation: Reduce Activities by ~$60
- **Human decision:** `partial` by `student-4`: approved ['1', '3', '4', '5', '6'], rejected ['2'], edits `{"5": {"estimated_cost": 40}}`, note "Museum already covered by passes; tour trimmed to $40"
- **Applied:** `{"created_ids": [91, 92, 93, 94, 95], "target": "budget-db"}`

### History (`GET /runs/<id>/history`)

| # | ts (UTC) | step | agent | state | detail |
|---|---|---|---|---|---|
| 1 | 02:45:59.132 | created |  | running | `{"requested_by": "cli:Oat"}` |
| 2 | 02:46:04.808 | plan | planner | running | `{"source": "llm", "attempts": [{"attempt": 1, "ok": true}], "duration_ms": 5671}` |
| 3 | 02:46:07.202 | work | worker | running | `{"source": "workflow", "duration_ms": 2390, "tools_called": ["list_expenses", "get_itinerary"]}` |
| 4 | 02:46:10.076 | review | reviewer | running | `{"source": "llm", "attempts": [{"attempt": 1, "ok": true}], "duration_ms": 2869}` |
| 5 | 02:46:10.083 | human_review | human | awaiting_human_review | `{}` |
| 6 | 02:46:12.574 | human_review | human | partially_accepted | `{"decision": "partial", "approved_ids": ["1", "3", "4", "5", "6"], "rejected_ids": ["2"], "edits": {"5": {"estimated_cost": 40}}, "note": "Museum already cov...` |
| 7 | 02:46:12.614 | applied | backend | applied | `{"created_ids": [91, 92, 93, 94, 95], "target": "budget-db"}` |

### audit.jsonl excerpt

| ts (UTC) | step | agent | model | source | duration_ms | tools_called | error |
|---|---|---|---|---|---|---|---|
| 02:46:04.810 | plan | planner | qwen2.5:7b | llm | 5671 | - | - |
| 02:46:07.206 | work | worker | - | workflow | 2390 | list_expenses, get_itinerary | - |
| 02:46:10.080 | review | reviewer | qwen2.5:7b | llm | 2869 | - | - |
| 02:46:12.577 | human_review | human | - | - | 0 | - | - |
| 02:46:12.616 | applied | backend | - | - | 0 | - | - |

## Scenario 1 - partial approval (UI)

- **run_id:** `run-323d8f2e5343` · requested_by `budget-be` · final state **`applied`**
- **Input:** `{"trip_reference": "TRIP-1001", "budget_cap": 1300.0, "note": "", "declined_requests": []}`
- **Totals (computed in Python):** recorded $1160.00 + draft $199.50 = **$1359.50** vs cap $1300.00 → **$59.50 over**
- **Plan:** `list_expenses` (Review recorded budget lines to understand current costs.), `get_itinerary` (Check scheduled items and their costs for accuracy.), `list_activities` (added automatically: categorises the scheduled itinerary items)
- **Draft lines:** 1. Guided historic centre walk (Activities, $24.00); 2. Visit the local art museum (Activities, $18.50); 3. Riverside food tour (Food, $32.00); 4. Breakfast market visit (Food, $12.00); 5. Architecture and gardens tour (Activities, $45.00); 6. Evening cooking class (Food, $68.00)
- **Reviewer:** risk **high**
  - finding: Projected $1359.50 is $59.50 over the $1300.00 cap.
  - finding: Transport: 'Flight to Paris' actual $480.00 is $30.00 above its $450.00 estimate.
  - finding: Activities: draft line 2 'Visit the local art museum' may duplicate recorded 'Museum passes' (similarity 1.0).
  - recommendation: Reduce Activities by ~$60
- **Human decision:** `partial` by `budget-ui`: approved ['1', '3', '4', '5', '6'], rejected ['2'], edits `{"5": {"estimated_cost": 40}}`, note "Museum already covered by passes; tour trimmed to $40"
- **Applied:** `{"created_ids": [109, 110, 111, 112, 113], "target": "budget-db"}`

### History (`GET /runs/<id>/history`)

| # | ts (UTC) | step | agent | state | detail |
|---|---|---|---|---|---|
| 49 | 02:51:17.247 | created |  | running | `{"requested_by": "budget-be"}` |
| 50 | 02:51:20.227 | plan | planner | running | `{"source": "llm", "attempts": [{"attempt": 1, "ok": true}], "duration_ms": 2976}` |
| 51 | 02:51:22.672 | work | worker | running | `{"source": "workflow", "duration_ms": 2441, "tools_called": ["list_expenses", "list_activities", "get_itinerary"]}` |
| 52 | 02:51:25.709 | review | reviewer | running | `{"source": "llm", "attempts": [{"attempt": 1, "ok": true}], "duration_ms": 3030}` |
| 53 | 02:51:25.717 | human_review | human | awaiting_human_review | `{}` |
| 54 | 02:51:25.999 | human_review | human | partially_accepted | `{"decision": "partial", "approved_ids": ["1", "3", "4", "5", "6"], "rejected_ids": ["2"], "edits": {"5": {"estimated_cost": 40}}, "note": "Museum already cov...` |
| 55 | 02:51:26.038 | applied | backend | applied | `{"created_ids": [109, 110, 111, 112, 113], "target": "budget-db"}` |

### audit.jsonl excerpt

| ts (UTC) | step | agent | model | source | duration_ms | tools_called | error |
|---|---|---|---|---|---|---|---|
| 02:51:20.231 | plan | planner | qwen2.5:7b | llm | 2976 | - | - |
| 02:51:22.676 | work | worker | - | workflow | 2441 | list_expenses, list_activities, get_itinerary | - |
| 02:51:25.713 | review | reviewer | qwen2.5:7b | llm | 3030 | - | - |
| 02:51:26.002 | human_review | human | - | - | 0 | - | - |
| 02:51:26.041 | applied | backend | - | - | 0 | - | - |

## Scenario 7 - reject all (terminal: curl)

- **run_id:** `run-4b96b38bc03a` · requested_by `budget-be` · final state **`rejected`**
- **Input:** `{"trip_reference": "TRIP-1003", "budget_cap": 1500.0, "note": "", "declined_requests": []}`
- **Totals (computed in Python):** recorded $1600.00 + draft $110.00 = **$1710.00** vs cap $1500.00 → **$210.00 over**
- **Plan:** `list_expenses` (Review recorded budget lines to understand current costs.), `get_itinerary` (Check scheduled items and their costs for accuracy.), `list_activities` (added automatically: categorises the scheduled itinerary items)
- **Draft lines:** 1. Temple visit before peak hours (Activities, $15.00); 2. Technology district tour (Activities, $55.00); 3. Sunset observation deck (Activities, $40.00)
- **Reviewer:** risk **high**
  - finding: Projected $1710.00 is $210.00 over the $1500.00 cap.
  - finding: Transport: 'Flight to Tokyo' actual $940.00 is $40.00 above its $900.00 estimate.
  - finding: No Food budget recorded or drafted.
  - recommendation: Draft lines alone cannot close the $210.00 gap: reduce Activities by up to $110.00 and recorded spending by ~$100, or raise the cap by $210.00.
  - recommendation: Increase the Transport estimate by $40.00 to match the actual cost.
  - recommendation: Add a Food budget amount.
- **Human decision:** `reject` by `student-4`: approved [], rejected ['1', '2', '3'], edits `{}`, note "Over cap; will rebudget the trip first"
- **Applied:** nothing (rejected)

### History (`GET /runs/<id>/history`)

| # | ts (UTC) | step | agent | state | detail |
|---|---|---|---|---|---|
| 43 | 02:50:33.362 | created |  | running | `{"requested_by": "budget-be"}` |
| 44 | 02:50:36.338 | plan | planner | running | `{"source": "llm", "attempts": [{"attempt": 1, "ok": true}], "duration_ms": 2971}` |
| 45 | 02:50:38.814 | work | worker | running | `{"source": "workflow", "duration_ms": 2471, "tools_called": ["list_expenses", "list_activities", "get_itinerary"]}` |
| 46 | 02:50:42.010 | review | reviewer | running | `{"source": "llm", "attempts": [{"attempt": 1, "ok": true}], "duration_ms": 3191}` |
| 47 | 02:50:42.018 | human_review | human | awaiting_human_review | `{}` |
| 48 | 02:50:42.080 | human_review | human | rejected | `{"decision": "reject", "rejected_ids": ["1", "2", "3"], "note": "Over cap; will rebudget the trip first", "reviewer": "student-4"}` |

### audit.jsonl excerpt

| ts (UTC) | step | agent | model | source | duration_ms | tools_called | error |
|---|---|---|---|---|---|---|---|
| 02:50:36.341 | plan | planner | qwen2.5:7b | llm | 2971 | - | - |
| 02:50:38.817 | work | worker | - | workflow | 2471 | list_expenses, list_activities, get_itinerary | - |
| 02:50:42.014 | review | reviewer | qwen2.5:7b | llm | 3191 | - | - |
| 02:50:42.082 | human_review | human | - | - | 0 | - | - |

## Scenario 7 - reject all (UI)

- **run_id:** `run-9a3e1dcd7baf` · requested_by `budget-be` · final state **`rejected`**
- **Input:** `{"trip_reference": "TRIP-1002", "budget_cap": 2000.0, "note": "", "declined_requests": []}`
- **Totals (computed in Python):** recorded $575.00 + draft $82.50 = **$657.50** vs cap $2000.00 → $1342.50 remaining
- **Plan:** `list_expenses` (Review recorded budget lines to understand current costs.), `get_itinerary` (Check scheduled items and their costs for accuracy.), `list_activities` (added automatically: categorises the scheduled itinerary items)
- **Draft lines:** 1. Ancient landmarks walking tour (Activities, $20.00); 2. Gallery and cultural district (Activities, $27.50); 3. Regional food tasting (Food, $35.00)
- **Reviewer:** risk **low**
- **Human decision:** `reject` by `budget-ui`: approved [], rejected ['1', '2', '3'], edits `{}`, note "Not adding these yet"
- **Applied:** nothing (rejected)

### History (`GET /runs/<id>/history`)

| # | ts (UTC) | step | agent | state | detail |
|---|---|---|---|---|---|
| 56 | 02:51:28.348 | created |  | running | `{"requested_by": "budget-be"}` |
| 57 | 02:51:31.326 | plan | planner | running | `{"source": "llm", "attempts": [{"attempt": 1, "ok": true}], "duration_ms": 2974}` |
| 58 | 02:51:33.829 | work | worker | running | `{"source": "workflow", "duration_ms": 2499, "tools_called": ["list_expenses", "list_activities", "get_itinerary"]}` |
| 59 | 02:51:36.534 | review | reviewer | running | `{"source": "llm", "attempts": [{"attempt": 1, "ok": true}], "duration_ms": 2700}` |
| 60 | 02:51:36.540 | human_review | human | awaiting_human_review | `{}` |
| 61 | 02:51:36.916 | human_review | human | rejected | `{"decision": "reject", "rejected_ids": ["1", "2", "3"], "note": "Not adding these yet", "reviewer": "budget-ui"}` |

### audit.jsonl excerpt

| ts (UTC) | step | agent | model | source | duration_ms | tools_called | error |
|---|---|---|---|---|---|---|---|
| 02:51:31.329 | plan | planner | qwen2.5:7b | llm | 2974 | - | - |
| 02:51:33.833 | work | worker | - | workflow | 2499 | list_expenses, list_activities, get_itinerary | - |
| 02:51:36.537 | review | reviewer | qwen2.5:7b | llm | 2700 | - | - |
| 02:51:36.919 | human_review | human | - | - | 0 | - | - |

