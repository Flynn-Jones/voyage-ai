# Multi-Agent workflow evidence: Activity (student-3)

`activity_schedule_check` was run live on 2026-10-22 between 12:23:36 and 12:23:40 UTC. Nothing was
mocked:

- the real `activity-frontend`, `activity-backend` and `activity-database` containers (root
  `docker-compose.yml`, Multi-Agent enabled, not the CI settings)
- the real shared Multi-Agent Server (`:7004`) and MCP server (`:7001`) on the host
- real Ollama with `qwen2.5:0.5b`, the model Activity's compose file uses

Every request went through `activity-backend`'s own `/api/activity/multi-agent/*` routes
(`backend/routes/multi_agent_mode.py`), not straight to the Multi-Agent Server. That also proves the
backend wiring (`services/multi_agent_api.py`) works end to end.

- Full request and response bodies:
  [`scenario-transcripts/multi-agent-scenarios.txt`](scenario-transcripts/multi-agent-scenarios.txt)
- The server's `logs/audit.jsonl` entries for the three runs:
  [`audit-excerpts.jsonl`](audit-excerpts.jsonl)
- The workflow itself: `ai-services/multi-agent-server/workflows/activity_schedule_check.py`. Agent
  prompts are in `activity-service/prompts/multi-agent/`.

## How the workflow works

**Planner → Worker → Reviewer → Human.** It is read-only throughout:

- **Tools:** the only ones allowed are the MCP tools `list_activities` and `list_assignments`.
- **Worker:** Python computes the schedule facts (summary, overlapping pairs, free slots). The LLM only
  narrates that result.
- **Reviewer:** the LLM review is grounded in computed checks and can never be more lenient than them.
- **Human:** a person makes the one decision per run: Accept, Partially Accept or Reject. Nothing is
  ever written to the activity database; "applied" only closes the run's lifecycle.

## Summary of the three runs

| Scenario | run_id | Task | Computed result | Reviewer | Human | Final state |
|---|---|---|---|---|---|---|
| 1 | `run-45966fb7d69e` | `summary` (all days) | 10 activities, 10 scheduled; combined cost 528.00 | medium, Partially Accept | **Accept** | `applied` |
| 2 | `run-596224f719a7` | `overlaps` on 2026-10-06 | 1 overlapping pair: #7 Old Town Cycling Tour 09:00-13:00 and #11 Bike Repair Workshop 11:00-13:00, 120 min | medium, Partially Accept | **Partially Accept** + correction | `applied` |
| 3 | `run-bb30addd9e9e` | `free_slots` on 2026-10-07, 2 h | 2 proposed slots: 08:00-10:00 and 18:00-20:00 (not saved) | medium, Partially Accept | **Reject** | `rejected` |

Two more checks were run against the same live stack:

| Check | Request | Result |
|---|---|---|
| Invalid input | `overlaps` with no date | **400** `invalid input: date (YYYY-MM-DD) is required for overlaps`; no run created |
| One decision per run | a second decision on `run-45966fb7d69e` | **409** `run run-45966fb7d69e is 'applied', not awaiting_human_review; only one human decision is allowed per run` |

## Scenario 1: summary, human accepts

- **Input:** `{"task": "summary", "request": "Summarise every activity and when it is scheduled."}`
- **Planner** (1227 ms, `source: fallback`): the 0.5b model's plan failed validation twice ("plan
  needs 3 to 5 steps"). The workflow's fallback plan was used: read `list_activities`, read
  `list_assignments`, Reviewer check, Human decision.
- **Worker** (413 ms): `list_activities` (225 ms) and `list_assignments` (16 ms) over the MCP SDK,
  both OK. Python computed the 10 activities with their scheduled times and a combined cost of
  528.00.
- **Safeguard caught the LLM.** The Worker LLM's narration said "No activities found", which
  contradicts the evidence. The computed checks flagged it: `unsupported_claim` ("Worker output says
  no activities were found, but the evidence has 10.") and `format` (missing the Output / Evidence
  Used / Limitations sections).
- **Reviewer** (230 ms, `source: llm`): risk **medium**, recommendation **Partially Accept**. The
  findings are exactly the computed checks; the correction is "rewrite the worker output".
- **Human:** **Accept**, reason "Summary matches the activity list and schedule." The person
  checked the computed items, which are correct; the problem was only in the LLM's wording.
- **Applied:** `state: applied`, `applied_recorded: true`. No database write.

## Scenario 2: overlap found, human partially accepts with a correction

The real schedule has no clashing activities. So a temporary activity, "Evidence: Bike Repair
Workshop" (2 h, 2026-10-06 11:00), was added through `POST /api/add_activity` before the run and
deleted afterwards through `DELETE /api/delete_activity/11`. Both calls are in the transcript; the
database was back to its 10 activities afterwards.

- **Input:** `{"task": "overlaps", "date": "2026-10-06", "request": "Check 2026-10-06 for overlapping activities."}`
- **Planner** (495 ms, `source: fallback`): the LLM plan failed validation twice, so the fallback
  plan was used.
- **Worker** (251 ms): both MCP tools OK. Python found **1 overlapping pair among 2 activities**:
  `#7 Old Town Cycling Tour 09:00-13:00` overlaps `#11 Evidence: Bike Repair Workshop 11:00-13:00`
  by **120 min**. "half day" was read as 4 hours.
- **Computed checks:** `clash` (the overlap above) and `format`.
- **Reviewer** (574 ms, `source: llm`): the first attempt was rejected ("recommendation must be one of
  ('Accept', 'Partially Accept', 'Reject')") and the retry passed. Risk **medium**, recommendation
  **Partially Accept**. Advice: "Move one activity of each clashing pair to another time (Edit on the
  Activities page)."
- **Human:** **Partially Accept**, with the correction "Move the Bike Repair Workshop to 14:00 so it
  no longer overlaps the Old Town Cycling Tour." The reason: move the shorter activity, not the tour.
  The decision note records the workflow ID, decision, correction, reason, evidence used, reviewer
  and timestamp.
- **Applied:** `state: applied`, approved item `7-11`. The correction is advice for a person to act
  on; the workflow did not move anything.

## Scenario 3: free slots proposed, human rejects

- **Input:** `{"task": "free_slots", "date": "2026-10-07", "slot_hours": 2}`
- **Planner** (730 ms, `source: fallback`): the LLM plan failed validation ("plan must have Worker
  steps reading ['list_activities']", then "plan needs 3 to 5 steps").
- **Worker** (177 ms): Python searched 08:00-20:00 around the 2 scheduled activities: "Day Spa & Hot
  Springs", full day from 10:00, read as 8 hours; and "Live Jazz Club Evening" at 20:00. It proposed
  `slot-1` **08:00-10:00** and `slot-2` **18:00-20:00**, each marked "Proposed: ... (not saved)".
  The self-check confirmed neither slot clashes with a scheduled activity (no `proposal_conflict`).
- **Computed checks:** `unsupported_claim` (the LLM again claimed "No activities found") and `format`.
- **Reviewer** (211 ms, `source: llm`): risk **medium**, recommendation **Partially Accept**.
- **Human:** **Reject**, reason "The free slots are too early or too late in the day for this trip."
  Both slots appear in `rejected_ids`.
- **Applied:** a rejected run is never reported to `/applied`, so `applied_recorded: null` and the
  final state is **`rejected`**.

## History and audit log

`GET /api/activity/multi-agent/runs/<run_id>/history` for each run, from the transcript:

| run | created | plan | work | review | awaiting human | decision | applied |
|---|---|---|---|---|---|---|---|
| `run-45966fb7d69e` | seq 53 | 54 | 55 | 56 | 57 | 58 `approved` | 59 `applied` |
| `run-596224f719a7` | seq 60 | 61 | 62 | 63 | 64 | 65 `partially_accepted` | 66 `applied` |
| `run-bb30addd9e9e` | seq 67 | 68 | 69 | 70 | 71 | 72 `rejected` | — |

[`audit-excerpts.jsonl`](audit-excerpts.jsonl) has the server's 14 audit entries for these runs:
every plan, work, review, human_review and applied step. Each entry records its timing, source
(`fallback` or `llm`), the model, and every tool call (`list_activities` and `list_assignments`
only, both `ok: true`).

## What this shows

- **The order is enforced:** Planner → Worker → Reviewer → Human, every time. A run waits in
  `awaiting_human_review` until a person decides, and only one decision is allowed (the 409 above).
- **Every Accept, Partially Accept and Reject path was exercised,** with the full decision record:
  workflow ID, decision, correction, reason, evidence used, reviewer and timestamp.
- **Read-only:** only the two allowlisted read tools were called, and proposals are labelled
  "not saved".
- **The guardrails held against a real, weak model:**
  - invalid LLM plans were rejected and replaced by the fallback plan
  - an invalid LLM review was retried
  - the Worker LLM's false "No activities found" claims were flagged as `unsupported_claim`
  - every reviewer verdict was at least as strict as the computed checks

  The computed facts (overlaps, free slots, totals) were right in every run; only the LLM's
  wording was wrong, and it was caught each time.
- **Disabled by default in CI and the cloud:** with `ACTIVITY_MULTI_AGENT_ENABLED=false`, the run
  route returns **403** "Multi-Agent mode is disabled." See `student-3.yml`'s `build-and-smoke` job
  and [`scenario-transcripts/endpoint-tests.txt`](scenario-transcripts/endpoint-tests.txt).

## Unit test coverage

The live runs cover the main paths against a real model and real data. Edge cases are covered
deterministically, with MCP and the LLM mocked, in
`ai-services/multi-agent-server/tests/test_activity_schedule_check.py`: **36 tests, all passing**
([`junit-mas-activity-schedule-check.xml`](junit-mas-activity-schedule-check.xml)). They cover:

- invalid input
- the read-only allowlist
- duration parsing
- free slots never clashing
- unreadable durations
- an empty day
- the database down (high risk, Reject)
- unsafe or incomplete LLM plans falling back
- a Worker citing unknown IDs
- reviews that try to downgrade the risk

## Model note

These runs used `qwen2.5:0.5b`. The Multi-Agent Server defaults to `qwen2.5:7b`, which isn't
installed on this machine, so it was started with `OLLAMA_MODEL=qwen2.5:0.5b`. With a larger model
the Planner and Worker LLM output would usually pass validation directly instead of falling back.
The computed results and the human decision flow don't depend on the model.
