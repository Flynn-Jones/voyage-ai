# Contribution log: Activity (Student 3), Release 2

Compiled 2026-10-25 by Student 1 (not by Student 3) from `git log origin/main`, merged pull requests and the evidence in this folder. "Author" is the identity Git and GitHub record for a commit. It shows who committed the change, not who designed or owns it. **Student 3 should review this log.**

Identity: Git authors `primalpancake92`, `John Le` and `Primalpanacke92` all resolve to the GitHub account `Primalpancake92`, which opened and merged PR #25. The link between this account and "Student 3" is inferred: the account committed this folder (`db0c673`) and the Activity service. No PR title or document names it as Student 3.

## Release 2 work on `main`

| Commit / PR | Author | What | Evidence |
|---|---|---|---|
| `1b62f33` (PR #25, merge `63a7c83`) | primalpancake92 | `activity_schedule_check` workflow and tests on the shared Multi-Agent Server. Activity backend `/api/activity/multi-agent/*` routes and client. Frontend Multi-Agent, MCP and RAG tabs. Activity RAG server files and prompts | [multi-agent-workflow-evidence.md](multi-agent-workflow-evidence.md) |
| `bebf735`, `6d558c0`, `8620a84`, `0f3ff24`, `28d0d52` | primalpancake92 | UI fixes, RAG server isolation fix, RAG fixes, removed redundant pages | commit messages |
| `db0c673` | primalpancake92 | Activity tests and `pytest.ini`. `student-3.yml` expanded (security-scans, offline-tests, compose-validate, build-and-smoke, evidence-pack). This evidence folder | [post-commit-cicd-report.md](post-commit-cicd-report.md), [pre-commit-security-report.md](pre-commit-security-report.md) |
| `433d219` | primalpancake92 | Consolidation of `activity_schedule_check`, frontend, tests and evidence updates (one-line `student-3.yml` change) | [junit-mas-activity-schedule-check.xml](junit-mas-activity-schedule-check.xml) |

Earlier releases from the same account: PR #5 and PR #11 (Activity service).

## Commits by others in `activity-service/` (Release 2)

- `a28239c` (GiangPiano, PR #23): the backend no longer opens the database SQLite file directly.
- `9845ba4` (GiangPiano): frontend nav links built from the request host.
- `f934d5f`, `8b9e357` (Flynn Jones): Activity database seeding fix, Activity AI gates and related tests.
- `e223429` (Flynn Jones): `workflow_call` and `push: main` triggers in `student-3.yml`.
- `0875f58` (PR #16, DebuggingMyLife): "Fixes for Activity feature" during the Release 1 shared-AI integration.

## Flags / not verified

- The account-to-Student-3 mapping above is inferred.
- Commit messages such as "feature implemented" and "final final commit" do not describe their changes. The "What" column above is taken from the files each commit changed, not from the message.
- This folder has no `architecture.md` or `local-deployment-report.md`.
