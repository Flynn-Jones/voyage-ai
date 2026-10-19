# Shared agentic loop (host-local validator)

Runs real tasks against the shared MCP and RAG services and prints a
PLAN / ACT / OBSERVE / ADAPT trace plus a deterministic `VERDICT PASS|FAIL`.
Not containerised.

## Setup
```
python3.11 -m venv ai-services/agentic_loop/.venv
ai-services/agentic_loop/.venv/bin/pip install -r ai-services/agentic_loop/requirements-dev.txt
```

## Required host services (canonical ports)
- Destination DB API `:6001` (Student 1 stack) -- data source for MCP/RAG
- Shared MCP `:7001` (`/mcp`): `cd ai-services/mcp-server && .venv/bin/python mcp_http_server.py`
- Shared RAG `:7002`: `cd ai-services/rag-server && OLLAMA_MODEL=<installed model> .venv/bin/python rag_http_server.py`,
  then (operator setup, not done by the loop) `POST :7002/refresh`
- Ollama `:11434` (RAG answers and optional LLM commentary)

Env names: `MCP_SERVICE_URL`, `RAG_SERVICE_URL`, `AGENTIC_TIMEOUT_SECONDS`, `AGENTIC_RAG_TIMEOUT_SECONDS`,
`OLLAMA_BASE_URL`, `AGENTIC_IMPLEMENTATION_MODEL`, `AGENTIC_REVIEW_MODEL`.

## Run (from repo root)
```
PY=ai-services/agentic_loop/.venv/bin/python
$PY ai-services/agentic_loop/app_main.py --mode mcp --no-llm   # list_destinations {"country":"Japan"} over /mcp
$PY ai-services/agentic_loop/app_main.py --mode rag --no-llm   # supported + unsupported question via POST /answer
$PY ai-services/agentic_loop/app_main.py --mode all --no-llm   # mcp + rag
$PY ai-services/agentic_loop/app_main.py                       # interactive menu (also has activity_rag)
```
`--mode activity_rag` is available but is never part of `all`; it drives the same shared RAG
server (:7002) with `scope="activity"`, as Activity's backend does (no separate Activity RAG server).
`--no-llm` skips the optional LLM commentary.

## Verdict / exit semantics
- Exit 0 only if every selected deterministic validator passes; exit 1 otherwise.
- The collector decides the verdict. LLM commentary runs only after a PASS and can never change it;
  an unavailable LLM is reported separately.

## Tests
`cd ai-services/agentic_loop && .venv/bin/python -m pytest -q`

## Release 2 review modes

Three review modes. Each gathers evidence into PASS/WARN/GAP/FAIL checks and writes
`docs/release-2/agentic-loop/<mode>-<UTC>.md` plus `<mode>-validation-<UTC>.jsonl` (one JSON line per check, then a
summary line). Missing evidence is a **GAP**, never a crash. The implementation and review models then add
recommendations on any verdict. As in Release 1, they are advisory and never change the verdict.

```
$PY ai-services/agentic_loop/app_main.py --mode multi-agent-review [--workflow budget_x] [--limit 200]
$PY ai-services/agentic_loop/app_main.py --mode testing-review
$PY ai-services/agentic_loop/app_main.py --mode cloud-deployment-review --decision approve|approve-with-conditions|reject \
    [--conditions "..."] [--decided-by "name"]
```
Common flags: `--no-llm` and `--out-dir DIR`. The interactive menu has these as options 4–6.

| Mode | Evidence | Checks |
|---|---|---|
| `multi-agent-review` | `GET /runs`, `/workflows` (`MULTI_AGENT_SERVICE_URL`, default `:7004`) and `MULTI_AGENT_AUDIT_LOG` (default `ai-services/multi-agent-server/logs/audit.jsonl`). If the server is down, it uses the audit log alone and records a gap. | Planner → Worker → Reviewer in order; Human Review recorded for decided runs; an `applied` entry for applied runs; no refused or non-allowlisted tool calls; failed runs; p50/p95/max latency per agent (WARN above `AGENTIC_LATENCY_WARN_MS`, default 60000); fallback rate (WARN above 50%) |
| `testing-review` | `docs/release-2/student-N/pre-commit-security-report.md` (and its raw `pre-commit-*.txt`), `post-commit-cicd-report.md`, any `*.xml` JUnit | Report present; scans PASS (the newest raw scan output beats the report's own claim); CI/CD report present and not FAIL; at least 2 passing tests (JUnit is authoritative; test names in the CI/CD report give only a WARN) |
| `cloud-deployment-review` | `docs/release-2/cloud/`: `cloud-deployment-report.md`, `smoke-test-output*.txt`, `stack-outputs*.json`, plus `scripts/deploy/aws/services.yml` | Every frontend answers through the ALB; CRUD round-trip on every backend; AI routes report disabled; ALB URL recorded; `MCP/RAG/MULTI_AGENT/AI_ENABLED` are all `false` |

Smoke output contract (other lines are ignored): `PASS|FAIL frontend:<name>|crud:<service>|ai-disabled:<service> <detail>`.

**Verdict:** PASS (no fail or gap), INCOMPLETE (gaps only), FAIL. Exit code is 0 only for PASS.

**Human release decision (cloud-deployment-review, required):** pass it with `--decision`. On a terminal it is
prompted after the evidence and recommendations are shown. `approve-with-conditions` requires conditions. The
decision, who made it and when go in both report files. The exit code follows the decision: 0 for approve or
approve-with-conditions, 1 for reject, and 2 when it's missing. The report is still written, marked
`PENDING (required)`.
