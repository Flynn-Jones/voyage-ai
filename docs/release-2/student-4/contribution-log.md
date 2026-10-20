# Contribution log: Budget (Student 4), Release 2

Branch `student-4/release-2-budget-reconciler`, built on main after the shared Multi-Agent Server (#17) merged.

## What I built

| Area | What | Commits |
|---|---|---|
| Multi-Agent workflow | `budget_reconcile` on the shared Multi-Agent Server:<br>- Planner picks read-only evidence (each source with a reason)<br>- Worker drafts lines only for costs already chosen in Itinerary and computes the totals in Python<br>- Reviewer flags overspend, actual above estimate, fuzzy duplicates, missing categories and missing evidence<br>- the LLM review is grounded in those computed checks<br>- out-of-scope requests are declined | `db420dc`, `98f9b08`, `31a3e1a` |
| Human review → Adapt | budget-be `/budget/multi-agent/*` routes:<br>- the decision is forwarded once (a second one is 409)<br>- only approved/edited lines are written as Planned<br>- a failed write rolls back<br>- created ids are reported to the server<br>- 403 when `MULTI_AGENT_ENABLED=false` | `853c02f`, `65b739e`, `ec5da7e` |
| UI | Multi-Agent tab: Planner/Worker/Reviewer panels, totals, an approve/edit table, decision note, created ids, a history timeline, and the disabled state | `0d37826` |
| Data quality | budget-db validates every write (types, required fields, status) and returns 400 on bad data | `7f99199` |
| Post-commit testing | Live endpoint tests for POST/PUT `/expenses`; Ollama-generated edge cases reviewed and committed as AI-proposed; `student-4.yml` with 5 jobs, running on every push to main | `d94836b`, `92aaab2`, `706eebb` |
| Cloud readiness | `AI_ENABLED` switch and `/health`; gunicorn; `FLASK_DEBUG`; seed only when empty; same-origin `/api` nginx proxy; host-based navbar links | `1cfc338`, `d2d61dd`, `50a7433`, `194154b` |
| Security (pre-commit findings) | Fixed ruff E402 without suppressions. Justified S104 (container bind) and S608 (whitelisted columns) inline. Upgraded flask, flask-cors and requests to clear 7 pip-audit advisories | `714c830`, `0a08f6c` |
| Pre-commit onboarding | Added `budget-service/` to the shared pre-commit scope; hooks shown blocking a planted secret on a real `git commit`, then passing; full scan report and `pre-commit-security-report.md` | `696911f`, `c4d7827`, this commit |
| Evidence | Architecture, workflow evidence, local deployment report, AI-assisted tests, scenario transcripts, screenshots | this commit |

## All commits

| Commit | Message |
|---|---|
| `db420dc` | feat(budget): add budget_reconcile multi-agent workflow |
| `98f9b08` | test(budget): cover budget_reconcile scenarios with mocked MCP, RAG and LLM |
| `853c02f` | feat(budget): add Multi-Agent mode routes (human review -> apply) |
| `65b739e` | test(budget): unit-test Multi-Agent routes with the server and budget-db mocked |
| `ec5da7e` | chore(budget): wire MULTI_AGENT_SERVICE_URL and MULTI_AGENT_ENABLED into compose |
| `0d37826` | feat(budget): add Multi-Agent tab (Planner/Worker/Reviewer, human review, history) |
| `7f99199` | feat(budget): validate expense fields in budget-db (400 on invalid data) |
| `d94836b` | test(budget): live endpoint tests for POST and PUT /expenses with cleanup |
| `92aaab2` | test(budget): add Ollama-generated edge cases, reviewed and labelled AI-proposed |
| `706eebb` | ci(student-4): lint-security, unit, build, live endpoint and AI-disabled jobs |
| `1cfc338` | feat(budget): AI_ENABLED switch for AI-Mode and dependency-free /health |
| `d2d61dd` | feat(budget): gunicorn, FLASK_DEBUG, budget-db /health and seed only when empty |
| `50a7433` | feat(budget): same-origin /api proxy and host-based navbar links |
| `194154b` | test(budget): cover AI-Mode switch, health checks, validation and seeding |
| `714c830` | refactor(budget): import blueprints inside create_app (fixes ruff E402) |
| `0a08f6c` | fix(security): upgrade Budget backend flask, flask-cors and requests |
| `31a3e1a` | fix(budget): read Activity whenever the itinerary is read; no filler advice without evidence |
| `696911f` | build(budget): add budget-service/ to the pre-commit scope |
| `c4d7827` | docs(budget): add budget-service README (ports, switches, tests, pre-commit) |

## Files outside `budget-service/`

| File | Change |
|---|---|
| `ai-services/multi-agent-server/workflows/budget_reconcile.py` | new: the only file added to the shared server |
| `ai-services/multi-agent-server/tests/test_budget_reconcile.py` | new: its tests |
| `docker-compose.yml` (root) | Budget block only: `MULTI_AGENT_SERVICE_URL`, `MULTI_AGENT_ENABLED`, `AI_ENABLED` (`BUDGET_*` overrides) |
| `.github/workflows/student-4.yml` | rewritten |
| `.pre-commit-config.yaml` | one line: `budget-service/\|` added to the `files:` scope |
| `docs/release-2/student-4/` | evidence |

The Multi-Agent Server core was not changed.

## Pre-commit (done after #18 merged)
- `budget-service/` added to the pre-commit `files:` scope (`696911f`).
- Real `git commit` with the hooks: a planted canary was blocked, then clean commits passed
  ([`pre-commit-commit-run.txt`](pre-commit-commit-run.txt)).
- Full scan: [`pre-commit-20261020T121232Z.txt`](pre-commit-20261020T121232Z.txt) (all hooks PASS);
  summary in [`pre-commit-security-report.md`](pre-commit-security-report.md).

## CI

Done: the first `student-4` run on GitHub was green
([run 39367021651](https://github.com/Flynn-Jones/voyage-ai/actions/runs/39367021651)); its JUnit and
`post-commit-cicd-report.md` are committed (`e31d70a`).
