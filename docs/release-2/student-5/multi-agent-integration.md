# Student 5 Itinerary Multi-Agent integration

Implemented on `student-5/itinerary-feature`, based on foundation commit
`313b2538a9edaab19ea7585f7a96305b376f6ce8`. This report records the integration validation before the checkpoint commit.

## Shared pattern reused

The existing shared server discovers `workflows/itinerary_schedule_review.py` through its `WORKFLOW`
export. Generic Planner, Worker and Reviewer wrappers, JSON validation/retry/fallback, allowlisted tool
access, `runs.db`, ordered history, audit JSONL and all human decision transitions remain unchanged.
There is no new server, shared infrastructure refactor or feature database access from the plugin.

## Workflow

- Input: required trimmed `trip_reference` (at most 120 characters), optional positive integer `day`.
- Planner: scoped read-only task, required `get_itinerary` evidence, Worker/Reviewer/Human steps. The
  contract is fixed and validated so model output cannot substitute another scope/task/tool.
- Worker: calls the existing read-only MCP `get_itinerary` tool with the validated scope. Every record
  is checked for scope, identifiers, finite cost and valid times. Missing/malformed/unavailable evidence
  fails the run. At most 200 evidence records are accepted.
- Proposals: deterministic same-day shifts to remove overlaps in start-time/id order, preserving duration,
  activity, destination and cost. Each includes its original snapshot, item ID, full proposed record and
  reason. No records are created or deleted; no Worker persistence exists.
- Reviewer: re-derives the expected proposals from the evidence and checks Planner/Worker consistency.
  Unsupported changes, no evidence or shifts that cannot fit before midnight require manual review.
  Models cannot replace the computed assessment with unsupported findings. Shared fallback sources and
  attempts remain visible in history/audit when a model fails validation or is unavailable.
- Human: shared `approve`, `partial`, `correct`, `reject` decisions. The backend validates selections
  and corrections before forwarding; only times and notes can be corrected, preserving duration/scope.

RAG is deliberately unused for this structured task. Travel times, opening hours, reservations and
real-world feasibility are not in the evidence and are not claimed as verified.

## Backend routes

All routes use `/api/itinerary/multi-agent`:

| Method | Suffix | Behavior |
| --- | --- | --- |
| POST | `/run` | Start only `itinerary_schedule_review` |
| GET | `/runs/<run_id>` | Inspect an own-workflow run, including running/failed state |
| GET | `/runs/<run_id>/history` | Inspect shared ordered history after checking workflow/run identity |
| POST | `/runs/<run_id>/review` | Validate and record one human decision; does not write itinerary records |
| POST | `/runs/<run_id>/apply` | Apply the recorded approved set, then acknowledge through shared `/applied` |

`MULTI_AGENT_ENABLED=false` returns 403 before processing on every route. Invalid inputs return 400;
unknown/foreign workflows or run IDs 404; invalid lifecycle transitions, stale snapshots or unsafe
application selections 409; unavailable/malformed upstream responses 502; shared request timeout 504.
The existing service URL/timeout configuration is reused. Default remains disabled; root Compose uses
`ITINERARY_MULTI_AGENT_ENABLED=true` to opt in. No automatic startup/model downloads are introduced.

## Atomic application

The owning backend revalidates all approved items against the stored decision, Worker evidence and scope.
It calls only the internal database HTTP API. `POST /multi-agent/applications/<run_id>` independently
checks the approving decision, fixed workflow, full records, immutable fields and duration. It then:

1. Starts a SQLite `BEGIN IMMEDIATE` transaction in the database tier.
2. Checks the durable per-run receipt; identical replay returns it without writing, changed replay fails.
3. Compares approved original snapshots with current records; deleted/changed records fail.
4. Checks the final selected schedule against the current trip: no new or worsened pairwise overlap.
5. Updates all selected time/notes fields and stores the receipt in the same transaction, or rolls back all.

The additive `itinerary_multi_agent_applications` table is created idempotently on database startup.
Existing records and the normal CRUD schema are preserved. It protects retries across backend/database
restarts and concurrent application requests. If the database committed but its reply was lost, retry
returns the receipt; if the shared `/applied` acknowledgement was lost, retry cannot apply twice.
The backend reports saved-but-acknowledgement-pending explicitly. Shared `created_ids` contains the IDs of
updated existing records here; the backend receipt calls them `updated_item_ids`.

The database API retains the project's existing trusted internal-service boundary; this task does not add
authentication, cryptographic approval proofs or user-level run ownership. Backend/database endpoints must
remain private outside trusted local development. A human-approved partial selection can leave existing
overlaps, but application cannot introduce or worsen one. A stale/conflicting selection requires a fresh run.

## Frontend

The existing Itinerary page gains one section, using the existing form/card/button styles. It accepts trip
and optional day, displays Planner/Worker/Reviewer/status/history, provides checkboxes and time/notes
corrections for all four decisions, and has a separate Apply action. Controls lock while busy/after a
decision. Run ID loading supports reload/recovery. Model/evidence text uses DOM text/value APIs, not HTML
injection. Successful application refreshes the ordinary itinerary list.

Nginx gets a dedicated Multi-Agent proxy location with a 650-second read timeout; the backend's configurable
default is 240 seconds. The new JS file is explicitly copied into the frontend image.

## Validation (2026-10-22)

| Check | Result |
| --- | --- |
| Full Student 5 backend/database/shared-contract/collector/integration/UI suite | **291 passed** |
| Full shared Multi-Agent suite, including new Itinerary plugin tests | **152 passed** |
| Configured Ruff/S, detect-secrets, pip-audit | **PASS** on 70 scoped files |
| Root and standalone Compose configuration | **PASS** |
| `git diff --check` | **PASS** |

Raw scan: [pre-commit-20261022T094508Z.txt](pre-commit-20261022T094508Z.txt). No dependency pins, secrets-baseline
entries or shared scan exemptions changed in this task. Two rule-specific S603 annotations explain fixed
test-only subprocess invocations (this repository's HTTP test and installed Edge with temporary profile).

Student 5 command (MCP/RAG disabled in the process as in CI; tests opt into their mocked paths):

```text
.venv/Scripts/python.exe -B -m pytest itinerary-service/itinerary-be/tests itinerary-service/itinerary-db/tests itinerary-service/tests -q -p no:cacheprovider
291 passed in 8.96s
```

The subprocess HTTP test runs the real shared runner, backend, database API and temporary SQLite across
loopback HTTP. Ollama is replaced with failure to exercise logged fallbacks; an MCP transport adapter calls
the real `get_itinerary` tool, which reads that temporary database over HTTP. It verifies all four decisions,
no writes before Apply, foreign trip isolation, repeated decision/application rejection, real shared history/
audit, lost database responses, and lost shared acknowledgements. Database tests also exercise concurrent
application, restart persistence, stale second-record rollback and unsafe partial acceptance.

The headless Edge test uses the real HTML/CSS/JS with a local controlled API and temporary browser profile.
It verifies disabled handling, all four decision controls, partial selection, correction payloads, final
states and text-safe rendering. The sandbox reset Edge's DevTools connection; the same test and final suite
passed outside the sandbox. Edge/websocket-client are optional: environments without them report an explicit
skip. No development database/browser profile is used by these integration/UI tests.

## Subsequent live validation and remaining work

The complete local container/host-server validation was subsequently completed. See
[local-deployment-report.md](local-deployment-report.md) for real-model/MCP/browser runs, all four human
decisions, database receipts, restored baseline data and documented limitations, including Reviewer fallback.
The automated integration tests above remain distinct from that live evidence.

Post-commit GitHub Actions endpoint tests, published JUnit/CI evidence and cloud deployment remain future
release work. Keep all four AI switches false for cloud baseline deployment.
