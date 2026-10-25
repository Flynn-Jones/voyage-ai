# Contribution log: Accommodation (student-2) — Release 2 integration

**Status note (updated 2026-10-25 by Student 1 from `git log origin/main` and GitHub PR data; Student 2
should confirm):** the "uncommitted" note written on 2026-10-21 is out of date. This work is on `main`:

| Commit / PR | Author (as recorded) | What |
|---|---|---|
| `a92b36c`: PR #22 (`student-2/release-2`), squash-merged 2026-10-21, merged by `DebuggingMyLife` | GiangPiano | Everything in "What I built" below |
| `646b657`, `65a04ff` | GiangPiano | The original branch commits behind PR #22 (same change) plus `accommodation-service/scripts/integration-evidence.sh`. They reached `main` through PR #24 (`aws-integration`, merge `1cd26db`) |
| `cfcb7fc` (2026-10-21, after PR #22) | GiangPiano | Multi-Agent tab in the Accommodation frontend (`frontend/app.py`, `templates/multi_agent.html`, `frontend/tests/test_multi_agent.py`), also via PR #24 |

Commits by others in `accommodation-service/` since then: `9845ba4` (GiangPiano, nav links from
the request host) and `e223429` (Flynn Jones, `workflow_call` and `push: main` triggers and the endpoint-function recording step in `student-2.yml`).
Identity: GitHub resolves Git author `GiangPiano` to the account `GiangPiano`. PR #22's title names
"Student 2".

## What I built

| Area | What | Files |
|---|---|---|
| Pre-commit onboarding | Added `accommodation-service/` to the shared scan scope; fixed every Ruff finding (E402, F401) and pip-audit advisory it surfaced; accepted 4 findings with justification | `.pre-commit-config.yaml`, `backend/src/app.py`, `backend/app.py`, `database/src/app.py`, `frontend/app.py`, both requirements.txt |
| AI-Mode kill switch | `AI_ENABLED` env var + 403 response, same pattern as the existing `MCP_ENABLED`/`RAG_ENABLED` | `backend/src/services/llm_client.py`, `backend/src/routes/ai.py`, `backend/tests/test_ai_mode.py` |
| Multi-Agent client + routes | New HTTP client to the shared Multi-Agent Server + 4 Flask routes (run/get/history/review), registered in `create_app()` | `backend/src/services/multi_agent_api.py`, `backend/src/routes/multi_agent_mode.py`, `backend/src/app.py`, `backend/tests/test_multi_agent_mode.py` |
| `accommodation_shortlist` workflow | New Planner→Worker→Reviewer plugin for the shared Multi-Agent Server (read-only, no write tool) | `ai-services/multi-agent-server/workflows/accommodation_shortlist.py`, `ai-services/multi-agent-server/tests/test_accommodation_shortlist.py` |
| Cloud-flag wiring | `AI_ENABLED`/`MULTI_AGENT_*` added alongside the existing `MCP_ENABLED`/`RAG_ENABLED` in both of Accommodation's own compose files and the root integrated compose file | `accommodation-service/docker-compose.yml`, `docker-compose.ci.yml`, root `docker-compose.yml` |
| CI overhaul | Replaced the 3-job smoke test with a 5-job pipeline (lint-security, unit-tests, build, endpoint-tests, verify-ai-disabled), matching Budget's shape; new live endpoint test suite for the two required endpoint functions | `.github/workflows/student-2.yml`, `accommodation-service/pytest.ini`, `accommodation-service/tests/` |
| Cloud-deployment proposal | Draft-only proposal for the still-missing Release 2 cloud pipeline (AWS, bonus-marks AI-enabled path + security controls), using Accommodation as the worked example | `docs/release-2/student-2/cloud-deployment-proposal.md` |
| Agentic-loop review | Ran all three Release 2 review modes (`multi-agent-review`, `testing-review`, `cloud-deployment-review`) for real against this work | `docs/release-2/agentic-loop/*` (shared output location, not student-2-specific) |
| Documentation | This folder | `docs/release-2/student-2/*` |

## Files outside `accommodation-service/`

- `.pre-commit-config.yaml` — added the `accommodation-service/|` scope line.
- `.secrets.baseline` — regenerated timestamp/filter entry from running `detect-secrets scan`;
  no secrets were found or added.
- `docker-compose.yml` (repo root) — `accommodation-backend`'s block gained `AI_ENABLED` and
  `MULTI_AGENT_*`, matching Budget's existing prefixed-override pattern.
- `ai-services/multi-agent-server/workflows/accommodation_shortlist.py` and
  `ai-services/multi-agent-server/tests/test_accommodation_shortlist.py` — the new shared-server
  workflow plugin (per the plugin contract in `ai-services/multi-agent-server/README.md`, this
  lives in the shared server's own `workflows/`, not inside `accommodation-service/`).
- `.github/workflows/student-2.yml` — the CI rewrite.

## Pre-commit

Onboarded in this change (previously, `accommodation-service/` was not scanned at all). Full
report: [`pre-commit-security-report.md`](pre-commit-security-report.md). `pre-commit install`
was also run for this clone — `.git/hooks/pre-commit` now actually fires on `git commit`, pinned
to a durable `pipx`-installed Python 3.14 `pre-commit` (see the report's Follow-up for why 3.14,
not whatever `python3` resolves to by default). A planted-secret demonstration confirming a real
`git commit` gets blocked, not just a manual `pre-commit run`:
[`pre-commit-blocked-commit-demo.txt`](pre-commit-blocked-commit-demo.txt).

## CI

`.github/workflows/student-2.yml`'s 5 jobs were first reproduced locally against real Docker
containers. *Updated 2026-10-25:* the workflow has since run on GitHub Actions. The final-SHA run is
[40160985621](https://github.com/Flynn-Jones/voyage-ai/actions/runs/40160985621) (`48bb3cc`, success),
recorded in [`post-commit-cicd-report.md`](post-commit-cicd-report.md).

## Agentic-loop review (Release 2 requirement)

Ran all three Release 2 review modes for real, not simulated:

- `multi-agent-review` — **PASS**, 16/16 checks, against this change's live `accommodation_shortlist`
  runs (`docs/release-2/agentic-loop/multi-agent-review-20261021T025922Z.md`).
- `testing-review` — student-2 passes every check (pre-commit, CI/CD report, 93 passing JUnit
  tests); overall verdict is INCOMPLETE only because students 1/3/5 have no `docs/release-2/student-N/`
  yet (`docs/release-2/agentic-loop/testing-review-20261021T025747Z.md`). *Superseded:* see
  `docs/release-2/agentic-loop/testing-review-20261025T043322Z.md` (PASS for all five students).
- `cloud-deployment-review` — **INCOMPLETE**, honestly: no actual AWS deployment exists yet (only
  the proposal), so this correctly reports 4 gaps and a pending human decision rather than a false
  pass (`docs/release-2/agentic-loop/cloud-deployment-review-20261021T025950Z.md`).

Each `.md` report has a matching `*-validation-<timestamp>.jsonl` log in the same folder.

## Open items (not done by this change)

*Updated 2026-10-25:* items 1, 3 and 4 below are resolved. The work is committed (table above), a
Multi-Agent frontend tab was added in `cfcb7fc`, and the Student 1, 3 and 5 folders now exist. For
cloud status, see `docs/release-2/cloud/` rather than item 2.

- **Not yet committed or pushed.** Decide on a branch name (e.g. `student-2/release-2-shortlist`)
  and PR before any of this reaches `origin`.
- **No real cloud deployment exists** for any feature yet — `cloud-deployment-proposal.md` is a
  starting point for the team, and the `cloud-deployment-review` agentic-loop run above honestly
  documents this gap rather than papering over it.
- `accommodation_shortlist` has no frontend UI tab (Budget's equivalent has one); the live
  evidence here is terminal/curl-based, same as Budget's own evidence doc leans on for most
  scenarios.
- Students 1, 3 and 5 have no `docs/release-2/student-N/` folder at all yet (confirmed by the
  `testing-review` run above) — a group-level gap, not something I can close individually.
