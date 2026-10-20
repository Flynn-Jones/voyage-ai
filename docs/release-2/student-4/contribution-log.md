# Contribution log: Budget (Student 4), Release 2

Branch `student-4/release-2-budget-reconciler`, built on main after the shared Multi-Agent Server (#17) merged.

## What I built

| Area | What | Commits |
|---|---|---|
| Multi-Agent workflow | `budget_reconcile` on the shared Multi-Agent Server:<br>- Planner picks read-only evidence (each source with a reason)<br>- Worker drafts lines only for costs already chosen in Itinerary and computes the totals in Python<br>- Reviewer flags overspend, actual above estimate, fuzzy duplicates, missing categories and missing evidence<br>- the LLM review is grounded in those computed checks<br>- out-of-scope requests are declined | `8257406`, `45bcb0e`, `4dfd5df` |
| Human review → Adapt | budget-be `/budget/multi-agent/*` routes:<br>- the decision is forwarded once (a second one is 409)<br>- only approved/edited lines are written as Planned<br>- a failed write rolls back<br>- created ids are reported to the server<br>- 403 when `MULTI_AGENT_ENABLED=false` | `c8c2748`, `4d590e4`, `8cd11d5` |
| UI | Multi-Agent tab: Planner/Worker/Reviewer panels, totals, an approve/edit table, decision note, created ids, a history timeline, and the disabled state | `31c421a` |
| Data quality | budget-db validates every write (types, required fields, status) and returns 400 on bad data | `5c6630a` |
| Post-commit testing | Live endpoint tests for POST/PUT `/expenses`; Ollama-generated edge cases reviewed and committed as AI-proposed; `student-4.yml` with 5 jobs, running on every push to main | `db74dfb`, `94264da`, `6f02e83` |
| Cloud readiness | `AI_ENABLED` switch and `/health`; gunicorn; `FLASK_DEBUG`; seed only when empty; same-origin `/api` nginx proxy; host-based navbar links | `b0d802e`, `54216d3`, `f31bd21`, `6999a22` |
| Security (pre-commit findings) | Fixed ruff E402 without suppressions. Justified S104 (container bind) and S608 (whitelisted columns) inline. Upgraded flask, flask-cors and requests to clear 7 pip-audit advisories | `9fcbdbc`, `b8d4f3a` |
| Evidence | Architecture, workflow evidence, local deployment report, AI-assisted tests, scenario transcripts, screenshots | this commit |

## All commits

| Commit | Message |
|---|---|
| `8257406` | feat(budget): add budget_reconcile multi-agent workflow |
| `45bcb0e` | test(budget): cover budget_reconcile scenarios with mocked MCP, RAG and LLM |
| `c8c2748` | feat(budget): add Multi-Agent mode routes (human review -> apply) |
| `4d590e4` | test(budget): unit-test Multi-Agent routes with the server and budget-db mocked |
| `8cd11d5` | chore(budget): wire MULTI_AGENT_SERVICE_URL and MULTI_AGENT_ENABLED into compose |
| `31c421a` | feat(budget): add Multi-Agent tab (Planner/Worker/Reviewer, human review, history) |
| `5c6630a` | feat(budget): validate expense fields in budget-db (400 on invalid data) |
| `db74dfb` | test(budget): live endpoint tests for POST and PUT /expenses with cleanup |
| `94264da` | test(budget): add Ollama-generated edge cases, reviewed and labelled AI-proposed |
| `6f02e83` | ci(student-4): lint-security, unit, build, live endpoint and AI-disabled jobs |
| `b0d802e` | feat(budget): AI_ENABLED switch for AI-Mode and dependency-free /health |
| `54216d3` | feat(budget): gunicorn, FLASK_DEBUG, budget-db /health and seed only when empty |
| `f31bd21` | feat(budget): same-origin /api proxy and host-based navbar links |
| `6999a22` | test(budget): cover AI-Mode switch, health checks, validation and seeding |
| `9fcbdbc` | refactor(budget): import blueprints inside create_app (fixes ruff E402) |
| `b8d4f3a` | fix(security): upgrade Budget backend flask, flask-cors and requests |
| `4dfd5df` | fix(budget): read Activity whenever the itinerary is read; no filler advice without evidence |

## Files outside `budget-service/`

| File | Change |
|---|---|
| `ai-services/multi-agent-server/workflows/budget_reconcile.py` | new: the only file added to the shared server |
| `ai-services/multi-agent-server/tests/test_budget_reconcile.py` | new: its tests |
| `docker-compose.yml` (root) | Budget block only: `MULTI_AGENT_SERVICE_URL`, `MULTI_AGENT_ENABLED`, `AI_ENABLED` (`BUDGET_*` overrides) |
| `.github/workflows/student-4.yml` | rewritten |
| `docs/release-2/student-4/` | evidence |

The Multi-Agent Server core was not changed.

## Still to do after #18 (pre-commit) merges
- Add `budget-service/` to the pre-commit `files:` scope.
- Run `scripts/test/precommit-report.sh student-4` and capture one real `git commit` with the hooks (blocked,
  then clean).
- Write `pre-commit-security-report.md`.

Done: the first `student-4` run on GitHub was green
([run 39367021651](https://github.com/Flynn-Jones/voyage-ai/actions/runs/39367021651)); its JUnit and
`post-commit-cicd-report.md` are committed (`504075c`).
