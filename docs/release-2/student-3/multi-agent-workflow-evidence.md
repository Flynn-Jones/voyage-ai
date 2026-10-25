# Multi-Agent workflow evidence: Activity (student-3)

`activity_schedule_check` was run live on 2026-10-22 between 12:40:39 and 12:40:43 UTC. Nothing was
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

**One job, like the other features' workflows: check one day's schedule.** The input is a date
(required), a free-slot length (default 2 hours) and an optional request for the Planner. One run
goes **Planner → Worker → Reviewer → Human**:

- **Tools:** the only ones allowed are the read-only MCP tools `list_activities` and
  `list_assignments`.
- **Worker:** Python computes three things from the same reads:
  - the activities scheduled that day (`scheduled`, shown for context)
  - every **overlapping pair** (items of kind `overlap`)
  - **free slots** between 08:00 and 20:00 that clash with nothing (items of kind `free_slot`,
    labelled "Proposed: ... (not saved)")

  The LLM only narrates that result.
- **Reviewer:** the LLM review is grounded in computed checks (`clash`, `no_free_slot`, `no_records`,
  `unknown_duration`, `unsupported_claim`, `format`, …) and can never be more lenient than them.
- **Human:** a person makes the one decision per run: Accept, Partially Accept or Reject. Nothing is
  ever written to the activity database; "applied" only closes the run's lifecycle.

## Summary of the three runs

| Scenario | run_id | Day | Worker result (computed in Python) | Reviewer | Human | Final state |
|---|---|---|---|---|---|---|
| 1 | `run-a4744312ae92` | 2026-10-06 | 2 activities; **1 overlap** (#7 Old Town Cycling Tour 09:00-13:00 and #13 Bike Repair Workshop 11:00-13:00, 120 min); **1 free slot** 13:00-15:00 | medium, Partially Accept | **Partially Accept** + correction | `applied` |
| 2 | `run-4588ac06b483` | 2026-10-07 | 2 activities; no overlaps; **2 free slots** 08:00-10:00 and 18:00-20:00 | medium, Partially Accept | **Accept** | `applied` |
| 3 | `run-5c2c8742cfa0` | 2026-12-25 | nothing scheduled; 1 free slot 08:00-10:00 | low, Accept | **Reject** | `rejected` |

Two more checks were run against the same live stack:

| Check | Request | Result |
|---|---|---|
| Invalid input | no date | **400** `invalid input: date (YYYY-MM-DD) is required`; no run created |
| One decision per run | a second decision on `run-a4744312ae92` | **409** `run run-a4744312ae92 is 'applied', not awaiting_human_review; only one human decision is allowed per run` |

## Scenario 1: a clash and a free slot in one run, human partially accepts

The real schedule has no clashing activities. So a temporary activity, "Evidence: Bike Repair
Workshop" (2 h, 2026-10-06 11:00), was added through `POST /api/add_activity` before the run and
deleted afterwards through `DELETE /api/delete_activity/13`. Both calls are in the transcript; the
database was back to its 10 activities afterwards.

- **Input:** `{"date": "2026-10-06", "slot_hours": 2, "request": "Check my 2026-10-06 schedule for clashes and free time."}`
- **Planner** (1426 ms, `source: fallback`): the 0.5b model's plan failed validation twice ("plan
  needs 3 to 5 steps", then "plan uses 'human_approval', which is not an allowed read-only source").
  The workflow's fallback plan was used, with objective: "Check the schedule on 2026-10-06: list the
  activities scheduled that day, find any that overlap, and propose free 2-hour slots between 08:00
  and 20:00 that clash with none of them."
- **Worker** (575 ms): `list_activities` and `list_assignments` over the MCP SDK, both OK. Python
  found:
  - **Scheduled:** 09:00 #7 Old Town Cycling Tour ("half day", read as 4 h) and 11:00 #13 Bike Repair
    Workshop (2 h)
  - **`7-13` (overlap):** the two overlap by **120 min**
  - **`slot-1` (free slot):** 13:00-15:00. 08:00-09:00 was too short for 2 h, and only the first
    slot in each gap is proposed.
- **Computed checks:** `clash` and `format`. The LLM narration described the clash correctly but
  ignored the Output / Evidence Used / Limitations format.
- **Reviewer** (519 ms, `source: llm`): risk **medium**, recommendation **Partially Accept**,
  correction "rewrite the worker output". **Known limitation:** the LLM also added the advice "No
  2-hour slot can be proposed …", which is wrong because `slot-1` exists. It got through because the
  grounding check only rejects advice that cites IDs missing from the evidence. The findings, risk
  and recommendation all still come from the computed checks, and the human decision below is based
  on the computed items.
- **Human:** **Partially Accept**, with the correction "Move the Bike Repair Workshop to the
  13:00-15:00 free slot so it no longer overlaps the Old Town Cycling Tour." Approved `7-13` and
  `slot-1`. The decision note records the workflow ID, decision, correction, reason, evidence used,
  reviewer and timestamp.
- **Applied:** `state: applied`. The correction is advice for a person to act on; nothing was moved.

## Scenario 2: no clashes, free slots proposed, human accepts

- **Input:** `{"date": "2026-10-07", "slot_hours": 2}`
- **Planner** (682 ms, `source: fallback`): the LLM plan failed validation ("plan needs 3 to 5
  steps", then "evidence_required must be a list of strings").
- **Worker** (188 ms):
  - **Scheduled:** 10:00 #8 Day Spa & Hot Springs ("full day", 8 h, so until 18:00) and 20:00 #9 Live
    Jazz Club Evening
  - **Overlaps:** none
  - **`slot-1`:** 08:00-10:00. **`slot-2`:** 18:00-20:00.

  The self-check confirmed neither slot clashes with a scheduled activity (no `proposal_conflict`).
- **Safeguard caught the LLM.** The Worker LLM's narration said "No activities found", which
  contradicts the evidence. The computed checks flagged it as `unsupported_claim` ("Worker output
  says no activities were found, but the evidence has 10."), along with `format`.
- **Reviewer** (226 ms, `source: llm`): risk **medium**, recommendation **Partially Accept**,
  correction "Remove the ids that are not in the evidence … Rewrite the worker output as Output /
  Evidence Used / Limitations".
- **Human:** **Accept**, reason "No clashes; the morning and evening slots are usable." The person
  checked the computed slots, which are correct; the problem was only in the LLM's wording.
- **Applied:** `state: applied`, both slots approved.

## Scenario 3: an empty day, human rejects

- **Input:** `{"date": "2026-12-25", "slot_hours": 2}`
- **Planner** (428 ms, **`source: llm`**): this time the model's plan passed validation and was used
  (objective "Check activities scheduled on 2026-12-25 and find overlaps."). This proves the LLM
  planning path works end to end, not only the fallback.
- **Worker** (33 ms): nothing is scheduled that day, so there are no overlaps. The whole day is one
  free gap, so one slot is proposed: 08:00-10:00. The narration was deterministic (`fallback`)
  because there was no activity evidence for the LLM to describe.
- **Computed check:** `no_records` ("No activities are scheduled on 2026-12-25.").
- **Reviewer** (184 ms, `source: llm`): risk **low**, recommendation **Accept**, with the advice "Add
  or schedule activities (Register activity, or Edit on the Activities page), then re-run."
- **Human:** **Reject**, reason "Wrong day: nothing is planned on 2026-12-25, so there is nothing to
  check." `slot-1` is in `rejected_ids`.
- **Applied:** a rejected run is never reported to `/applied`, so `applied_recorded: null` and the
  final state is **`rejected`**.

## History and audit log

`GET /api/activity/multi-agent/runs/<run_id>/history` for each run, from the transcript:

| run | created | plan | work | review | awaiting human | decision | applied |
|---|---|---|---|---|---|---|---|
| `run-a4744312ae92` | seq 82 | 83 | 84 | 85 | 86 | 87 `partially_accepted` | 88 `applied` |
| `run-4588ac06b483` | seq 89 | 90 | 91 | 92 | 93 | 94 `approved` | 95 `applied` |
| `run-5c2c8742cfa0` | seq 96 | 97 | 98 | 99 | 100 | 101 `rejected` | — |

[`audit-excerpts.jsonl`](audit-excerpts.jsonl) has the server's 14 audit entries for these runs:
every plan, work, review, human_review and applied step. Each entry records its timing, source
(`llm` or `fallback`), the model, the failed attempts, and every tool call (`list_activities` and
`list_assignments` only).

## What this shows

- **One job per run.** A single day check returns the day's activities, clashes and free slots
  together. No task to choose.
- **The order is enforced:** Planner → Worker → Reviewer → Human, every time. A run waits in
  `awaiting_human_review` until a person decides, and only one decision is allowed (the 409 above).
- **Every Accept, Partially Accept and Reject path was exercised,** with the full decision record:
  workflow ID, decision, correction, reason, evidence used, reviewer and timestamp.
- **Read-only:** only the two allowlisted read tools were called, and proposals are labelled
  "not saved".
- **The guardrails held against a real, weak model:**
  - invalid LLM plans were rejected and replaced by the fallback plan, and a valid one was used
  - the Worker LLM's false "No activities found" claim was flagged as `unsupported_claim`
  - every reviewer verdict was at least as strict as the computed checks

  The computed facts (overlaps, free slots, totals) were right in every run.
- **One gap found:** reviewer advice that cites no IDs is not fact-checked, so a wrong sentence
  ("No 2-hour slot can be proposed …") reached the run card in scenario 1. It did not change the
  findings, risk or recommendation, and the human acted on the computed items.
- **Disabled by default in CI and the cloud:** with `ACTIVITY_MULTI_AGENT_ENABLED=false`, the run
  route returns **403** "Multi-Agent mode is disabled." See `student-3.yml`'s `build-and-smoke` job
  and [`scenario-transcripts/endpoint-tests.txt`](scenario-transcripts/endpoint-tests.txt).

## Unit test coverage

Edge cases are covered deterministically, with MCP and the LLM mocked, in
`ai-services/multi-agent-server/tests/test_activity_schedule_check.py`: **35 tests, all passing**
([`junit-mas-activity-schedule-check.xml`](junit-mas-activity-schedule-check.xml)). They cover:

- invalid input (no date, a bad date, a bad slot length, an overlong request)
- the read-only allowlist
- duration parsing
- one run returning a clash and a free slot together
- free slots never clashing
- unreadable durations blocking the rest of the day
- only that day's activities being listed
- an empty day
- the database down (high risk, Reject, and no slots guessed)
- unsafe or incomplete LLM plans falling back
- a Worker citing unknown IDs
- reviews that try to downgrade the risk

## Model note

These runs used `qwen2.5:0.5b`. The Multi-Agent Server defaults to `qwen2.5:7b`, which isn't
installed on this machine, so it was started with `OLLAMA_MODEL=qwen2.5:0.5b`. With a larger model
the LLM output would usually pass validation directly instead of falling back. The computed results
and the human decision flow don't depend on the model.
