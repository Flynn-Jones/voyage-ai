# Release 2 evidence index

Prepared 2026-10-25 from a local-only verification run on `e223429`. It was updated the same day (from 04:30 UTC) with the GitHub Actions runs for the final code SHA `48bb3cc`, copies of their CI JUnit (added in this update, not yet committed), a regenerated testing review and contribution logs. Nothing here was deployed or run against AWS.

## SHA sequencing (read first)

| Item | SHA | Meaning |
|---|---|---|
| **Final code SHA** | `48bb3cc6a76d2647d1ca00bedf52ee72f08c33e9` (`main`, merge of PR #28) | Same tree as `8b9e357`. All five student workflows succeeded on it (requirement 6), and its CI JUnit has been copied into `student-N/ci-run-<run>-48bb3cc/` (untracked until the evidence commit). |
| **Earlier tested SHA** | `e223429db551d11541c8dba981fc2da5b81ca917` | The commit the local checks (requirements 1–4, 7) and the Student 1 pre-commit scan ran against. `239b9e4` (evidence only) and `8b9e357` (review-collector, Activity AI-gate and smoke-test fixes) came after it. Those local results are historical and were not re-run on `48bb3cc`. |
| **Evidence commit** | _not committed yet_ | The documentation and JUnit changes listed under "Files changed (this update)". They change no code. Evidence deliberately cites the code SHA and is not re-edited to embed its own commit SHA. |

## Requirement status

Status key: VERIFIED (evidence in repo or in a cited run), PARTIAL, BLOCKED (could not be done here), HUMAN (needs a person's authority or access).

| # | Requirement | Status | Evidence / note |
|---|---|---|---|
| 1 | Integrated five-feature root Compose application | VERIFIED locally | `docker compose -f docker-compose.yml config --quiet` exit 0. 18 services, no Ollama/MCP/RAG/Multi-Agent service (`local-verification/compose-services-18.txt`). Isolated project `voyage-r2-verify`: all 18 `running`, none restarting (`compose-ps-18-services.txt`). Six frontends `:3000`-`:3005` return 200, 12 backend and database APIs return 200 on `/health` (`http-health.txt`). |
| 2 | Frontend → backend → database CRUD, five features | VERIFIED locally | 41 checks, 0 failures, tag `R2VERIFY-20261025T025816Z-8c6395`: create, read back, frontend render, update, delete and 404 for each feature, then a leftover sweep (0 rows) (`crud-transcript.txt`, `crud-results.json`). The Student 1 live suite was **not** run locally: it hardcodes backend `:5001` while the root stack maps it to `:5011`. Its authoritative result is CI run 40160985545 on `48bb3cc` (earlier: 40154654944 on `e223429`). |
| 3 | Ollama/AI, MCP, RAG and Multi-Agent outside Compose | VERIFIED locally | Host servers on `:7001`, `:7002`, `:7004`, Ollama on `:11434`; Multi-Agent `/health` reported Ollama, MCP and RAG reachable. `agentic_loop --mode all` PASS (MCP and RAG) and `--mode activity_rag` PASS, with Ollama commentary (`agentic-loop-*.txt`). RAG ran with `RAG_DATA_DIR` in a scratch directory; the repo's `chroma`, `corpus` and `rag-audit.jsonl` are unchanged (`rag-refresh-isolated.txt`). |
| 4a | Terminal Planner → Worker → Reviewer → Human Review, all decisions, history/audit, decision effect | VERIFIED locally | Workflow `destination_recommend` (read-only). **partial** `run-4350126a00db` (2 approved, 3 rejected, state `partially_accepted`, then `applied`); **correct** `run-70a75ccefc5c` (2 approved, correction stored on item 5, state `corrected`, then `applied`); **reject** `run-820601d6a716` (state `rejected`, `/applied` refused). History steps `created, plan, work, review, human_review, human_review, applied`. Second review and second `/applied` on a decided run, and `/applied` on a rejected run, all return HTTP 409 (`mas-http-refusal-codes.txt`). No feature records were created (`created_ids: []`). |
| 4b | The same through the UI | PARTIAL / BLOCKED | No browser automation was available in this session (neither the Chrome extension nor the in-app browser). The Student 1 frontend's own form routes were driven over HTTP: `POST :3001/multi-agent/run` then `POST .../review decision=approve` produced `run-9f028d16fe0c`, state `applied`, 5 items approved (`mas-frontend-route-approve.txt`). That exercises frontend → backend → Multi-Agent Server but **is not a browser session and has no screenshots**. A person must repeat one run in a browser and capture screenshots (checklist item 3). |
| 5 | Installed pre-commit with Ruff security rules, detect-secrets, pip-audit | VERIFIED (scan) / HUMAN (commit hook) | `.git/hooks/pre-commit` is installed. `scripts/test/precommit-report.sh student-1` exit 0, all three hooks Passed (`student-1/pre-commit-20261025T025405Z.txt`, recorded commit `e223429`). The scan ran over `e223429` plus the edits that were uncommitted at the time (since committed in `239b9e4`). Hook execution on a real `git commit` needs a commit, so it was not repeated. The chromadb `pip-audit` ignores in `.pre-commit-config.yaml` are due for re-check by 2026-11-20. |
| 6 | Five successful student Actions runs, matching SHA, artifacts | VERIFIED (final SHA) | All `success`, attempt 1, `push` to `main` on `48bb3cc`, 2026-10-25 04:11–04:14 UTC: student-1 [40160985545](https://github.com/Flynn-Jones/voyage-ai/actions/runs/40160985545), student-2 [40160985621](https://github.com/Flynn-Jones/voyage-ai/actions/runs/40160985621), student-3 [40160985570](https://github.com/Flynn-Jones/voyage-ai/actions/runs/40160985570), student-4 [40160985543](https://github.com/Flynn-Jones/voyage-ai/actions/runs/40160985543), student-5 [40160985592](https://github.com/Flynn-Jones/voyage-ai/actions/runs/40160985592). Every job succeeded. `report.json` in the student-1, 3 and 5 report artifacts records `commit_sha` = `48bb3cc…`. The JUnit was downloaded and copied byte-for-byte (SHA-256 checked) into `student-N/ci-run-<run>-48bb3cc/`, which is untracked until the evidence commit, with both required endpoint tests passing for each student. CI JUnit totals (0 failures or errors): student-1 live 35 (3 skipped) and offline 276; student-2 endpoint 6, unit 64; student-3 live 6, offline 191; student-4 endpoint 20, unit 25; student-5 endpoint 27, contract 51 (1 skipped), unit 240. Historical: the same five workflows also succeeded on `e223429` (runs 40154654944, 40154654904, 40154654959, 40154654931, 40154654952). |
| 7 | Offline test suites | VERIFIED locally | `scripts/deploy/aws/tests` 54 passed; `ai-services/agentic_loop` 91 passed; `ai-services/multi-agent-server` 186 passed (run with a scratch DB and audit log) (`offline-tests.txt`). |
| 8 | Manually gated AWS baseline, six frontends, five reversible tagged CRUD lifecycles, AI/MCP/RAG/Multi-Agent disabled | HUMAN | No AWS access (`aws` CLI absent) and no authorisation. See "Cloud status". |
| 9 | Agentic-loop review modes | See below | Each mode was run once. |

### Review modes (agentic loop)

| Mode | Output | Verdict | Exit | Notes |
|---|---|---|---|---|
| `testing-review` (Ollama) | `agentic-loop/testing-review-20261025T043322Z.md` + `testing-review-validation-20261025T043322Z.jsonl` | PASS (15 pass, 0 warn, 0 gap, 0 fail) | 0 | Run on the final evidence. All five students: both required endpoint testcases pass in the `48bb3cc` CI JUnit copied into `student-N/ci-run-<run>-48bb3cc/` (read from the working tree, not yet committed). The collector also reads the older local JUnit for students 2 and 3 and the older CI JUnit for student 4; those pass too. The pre-commit check reads each student's committed scan report (students 2, 3 and 4: 2026-10-20 to 2026-10-22; student 5: 2026-10-22; student 1: on `e223429`). It was not re-run on `48bb3cc`. On `48bb3cc` itself, the CI `lint-security` jobs (students 2 and 4) and `security-scans` job (student 3) ran Ruff S, detect-secrets and pip-audit, and passed. Superseded: `testing-review-20261025T030246Z.md` (INCOMPLETE: student-5 gap, student-1 warn; produced by the earlier count-based collector, before the `8b9e357` collector fix). |
| `multi-agent-review` (Ollama) | `agentic-loop/multi-agent-review-20261025T030320Z.md` | PASS (30 pass, 1 warn) | 0 | Covers the 4 new runs above plus earlier ones. The one WARN is the pre-existing `run-b63d4b354473`, which is still awaiting human review and was left alone. |
| `cloud-deployment-review` (`--no-llm`, no decision) | `agentic-loop/cloud-deployment-review-20261025T030332Z.md` | FAIL (8 pass, 1 fail) | 2 | Truthful result on stale cloud evidence. The decision is recorded as `PENDING (required)`. `--no-llm` was chosen on purpose: the inputs are known to be stale. |

The older reviews of 2026-10-21/22 and the `...T030246Z` testing review are kept as history. The multi-agent and cloud-deployment reviews above were not re-run in this update.

## Cloud status

- The smoke evidence in `docs/release-2/cloud/` is run `20261022T035641Z`. It predates `f934d5f` and there is no `cloud-deployment` or smoke run for `e223429` or `48bb3cc` (checked with `gh run list --workflow cloud-deployment.yml` on 2026-10-25).
- The latest successful `cloud-deployment` runs came from branch `ai-aws-integration` (run 40149950642, commit `04d299d`), which makes AI Mode intentionally live in the cloud. The cluster may therefore not be in the AI-disabled baseline. This is unverified.
- `smoke-test.sh` at this SHA proves frontend reachability, seeded reads and disabled-AI responses. It does **not** prove create, update or delete. A fresh AWS baseline, tagged cloud CRUD with cleanup, screenshots or logs, and the release decision are mandatory human-authorised evidence.
- Conflict resolved by wording only: the report said `PENDING` while the 2026-10-22 review recorded `approve-with-conditions`. The reconciliation note in `cloud/cloud-deployment-report.md` treats that conditional approval as superseded and unverified. The Release Decision stays PENDING.

## Findings

- *Resolved:* the deletion described below was committed in `239b9e4`. There is no `integration-ci.yml` failure on `48bb3cc`.
- `.github/workflows/integration-ci.yml` was a 0-byte file (since `ba7b9e9`), so every push showed a workflow-file failure. A real version exists only on `origin/shared/aws-deployment` and depends on scripts that are not on `main`. Resolution (committed in `239b9e4`): the file is deleted, and `student-4/architecture.md` section 4 now shows the real chain: manual dispatch → `preflight` and `security-scans` → five student workflows via `workflow_call` → `gate` → `deploy` → smoke. The same diagram's smoke node now says "seeded reads", not "CRUD". `student-1/docs/evidence/session-5-pre-workflows.txt` is a historical snapshot and is unchanged.
- *Resolved 2026-10-25:* all five `post-commit-cicd-report.md` files now start with a "Final-SHA GitHub Actions run" section for `48bb3cc`. In the teammates' reports (students 2–5), the earlier content is kept and labelled historical rather than rewritten.

## Files changed (this update, uncommitted)

- New: `student-{1..5}/ci-run-<run>-48bb3cc/` (CI JUnit XML and `endpoint-tests.md`, unmodified), `agentic-loop/testing-review-20261025T043322Z.md` and its `-validation-` `.jsonl`, `student-{1,3,5}/contribution-log.md`
- Modified: `student-{1..5}/post-commit-cicd-report.md`, `student-2/contribution-log.md` and `student-4/contribution-log.md` (dated update notes only), this index
- Not modified: application code, workflows, AWS configuration, `docs/release-2/cloud/`

## Files changed (earlier update, now committed in `239b9e4`)

- Deleted: `.github/workflows/integration-ci.yml`
- Modified: `docs/release-2/student-4/architecture.md` (section 4 diagram), `docs/release-2/student-1/post-commit-cicd-report.md`, `docs/release-2/student-1/pre-commit-security-report.md`, `docs/release-2/cloud/cloud-deployment-report.md` (note inserted above the unchanged `## Release Decision` section)
- New: `docs/release-2/release-evidence-index.md` (this file), `docs/release-2/student-1/pre-commit-20261025T025405Z.txt`, six files in `docs/release-2/agentic-loop/` (the three reviews above and their validation logs), and `docs/release-2/local-verification/` (13 transcripts)

## Local resources left behind (stopped, not removed)

- Compose project `voyage-r2-verify`: 18 stopped containers `voyage-r2-verify-<service>-1`, network `voyage-r2-verify_voyage-net`, six volumes `voyage-r2-verify_{shared,destination,accommodation,activity,budget,itinerary}-db-data` and 18 images `voyage-r2-verify-<service>:latest`. Optional cleanup: `docker compose -p voyage-r2-verify down` (add `-v` only if you want the volumes gone).
- The previously existing `voyage-ai` project (18 exited containers, six `voyage-ai_*` volumes, `student-1_destination-db-data`) and the `microservices-net` network were not touched.
- Multi-Agent Server `runs.db` and `logs/audit.jsonl` (gitignored) gained 4 runs (5 → 9) and 19 audit lines (23 → 42). The earlier entries are an unchanged prefix. New run IDs: `run-4350126a00db`, `run-70a75ccefc5c`, `run-820601d6a716`, `run-9f028d16fe0c`.
- The tracked `*.sqlite` files are unchanged (checksums match the pre-run snapshot). No `R2VERIFY` rows remain. MCP, RAG and Multi-Agent processes started here were stopped by PID; Ollama was not touched. Docker Desktop was started for this run and is still running.

## Human-only checklist

1. ~~Commit the earlier evidence and confirm five green student runs~~: done (`239b9e4`, then runs on `48bb3cc`). Review and commit this update (needs your approval).
2. Optionally remove the stopped `voyage-r2-verify` resources above.
3. Browser UI evidence: with the stack and host servers up, run one Student 1 `/multi-agent` workflow in a browser (start, Planner/Worker/Reviewer, Human Review, decision) and save screenshots.
4. Student 1 and 5 JUnit (and students 2–4) were downloaded from the `48bb3cc` runs and saved under `student-N/ci-run-<run>-48bb3cc/` (requirement 6). `testing-review` was regenerated on them, verdict PASS. **Committing these files is still pending** (they are untracked until the evidence commit).
5. AWS (paid, human-authorised): install and authenticate the `aws` CLI; run `bash scripts/deploy/aws/gh-oidc-setup.sh` from `main` so `main` is the trusted branch; then `gh workflow run cloud-deployment.yml --ref main -f sha=<new full 40-character SHA>`. This replaces the AI-live `ai-aws-integration` images with the AI-disabled baseline. Commit the `cloud-deployment-evidence` artifact files into `docs/release-2/cloud/`.
6. Cloud CRUD: run a tagged create, update, delete and 404 lifecycle with cleanup for all five services, and capture screenshots or logs of the six frontends and the disabled-AI responses. `smoke-test.sh` does not do this today.
7. Run `cloud-deployment-review --decision <approve|approve-with-conditions|reject> --decided-by "<name>"` on the fresh evidence and record the release decision in `cloud/cloud-deployment-report.md`.
8. CI/CD reports refreshed to the final SHA and contribution logs written for all five students, in the working tree; **committing is still pending**. The logs for students 1, 3 and 5 were compiled by Student 1 from Git history, so **students 2–5 should confirm their logs and the flagged account mappings**. Still missing: `architecture.md` for students 1, 3 and 5, and `local-deployment-report.md` for students 1 and 3.
9. Contribution log, architecture and local deployment documents for every student, the final PDF, the demo video and the final release decision.
