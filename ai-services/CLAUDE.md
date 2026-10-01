# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Scope

`ai-services/` holds the three **shared, non-containerised** AI components added in Release 1 of
VoyageAI (UTS Advanced Software Development, Group 50). The repo root (`voyage-ai/`) holds the five
student-owned feature services (`accommodation-service/`, `activity-service/`, `budget-service/`,
`itinerary-service/`, `student-1/`) plus `shared/`; see the root `README.md` for the product brief.

Everything here runs **on the host, not in Docker**, on purpose: there is exactly one shared MCP
instance and one shared RAG instance for the whole team, and containerised backends dial them via
`host.docker.internal` — the same pattern already used for Ollama. Do not containerise these
servers without also rewiring every feature's `MCP_SERVICE_URL`/`RAG_SERVICE_URL`.

## The three components

```
mcp-server/     4 read-only tools over feature data + repo files   → :7001
rag-server/     corpus build + retrieval + grounded answering      → :7002
agentic_loop/   CLI that validates the above two, LLM-reviewed     (no port)
```

### mcp-server

`tools.py` is the single implementation of all four tools; `server.py` (stdio MCP via `FastMCP`)
and `mcp_http_server.py` (plain `http.server`, port 7001) are two thin transports over it. **Add or
change a tool in `tools.py` and register it in all three places**: the `@mcp.tool()` wrapper +
`AVAILABLE_TOOLS` in `server.py`, the `TOOLS` dict in `mcp_http_server.py`, and `tool-contracts.md`
(which is the published contract other students code against).

Tools never touch SQLite. `list_expenses` and `get_accommodation_by_destination` go over HTTP to the
feature database tiers (`BUDGET_DB_URL` default `localhost:6004`, `ACCOMMODATION_DB_URL` default
`localhost:6002`). `project_files` and `ci_report` read the repo, sandboxed to `REPO_ROOT`.

Error convention: tools **return** `{"error": ...}` rather than raising. `mcp_http_server` maps a
result containing `error` to HTTP 502, so a downed feature DB surfaces as 502, not 500.

### rag-server

`rag_pipeline.py` is the whole pipeline; `rag_server.py` (stdio MCP) and `rag_http_server.py`
(port 7002, routes `/refresh` `/retrieve` `/answer`) are transports. Three tools: `refresh_corpus`,
`retrieve_context`, `answer_question`.

Things that are easy to get wrong here:

- **`embed_texts` is a hand-rolled SHA-256 hashing embedder, not a semantic model.** It sums byte
  values per token into a fixed 256-dim vector. Retrieval is effectively lexical overlap — do not
  reason about it as if it had semantic similarity, and do not swap the tokenizer casually (the
  `_TOKEN_RE` split on non-alphanumerics exists so `destination_id=dest-tokyo` yields `tokyo`).
- **Authority tiers drive ranking more than distance does.** `tier_1` (live budget/accommodation DB
  records) > `tier_2` (repo docs) > `tier_3` (repo file index), and the sort in `retrieve_context`
  puts tier first, distance second. `confidence_from_results` derives High/Medium/Low from tier
  counts alone.
- **Chroma failure is non-fatal.** Any exception in the vector path drops to `lexical_fallback_retrieve`
  and the response says `retrieval_mode: "lexical_fallback"`; `refresh_corpus` reports
  `vector_store_status: "degraded"` instead of failing. A passing call does not prove Chroma worked —
  check those fields.
- `answer_question` must keep returning `confidence_category: "Insufficient"` with an
  insufficient-evidence answer rather than generating; `rag_collector` and the review prompts both
  assert on this.
- Every tool call appends to `rag-audit.jsonl`. That file, `corpus/*.jsonl`, `chroma/`, and
  `retrieval-metrics.md` are gitignored generated artefacts.
- `DOC_SOURCES` currently lists `docs/ARCHITECTURE.md` and `budget-service/SUMMARY.md`, **neither of
  which exists** — missing paths are silently skipped, so tier_2 is thinner than the code implies.
  (`README.md` refers to `docs/ARCHITECTURE.md` too.)

### agentic_loop

An interactive CLI implementing OBSERVE → implementation-LLM → review-LLM over two modes (`mcp`,
`rag`). Each mode is four pluggable pieces, so adding a mode means adding one entry to each:

| piece | file | role |
| --- | --- | --- |
| mode entry | `config/review_config.py` | label, prompt family, prompt file paths |
| collector | `collectors/<mode>_collector.py` | OBSERVE: returns `(ok, evidence_string)` |
| pipeline | `pipelines/<mode>_pipeline.py` | builds implementation + review user prompts |
| prompts | `<repo root>/prompts/<family>/` | plain `.txt` system prompts, loaded by `core/prompts.py` |

`core/orchestrator.py` dispatches via the `COLLECTORS`/`PIPELINES` dicts — it should not need edits
to gain a mode. `core/ai.py` calls Ollama `/api/chat` at temperature 0 with two configurable models
(`AGENTIC_IMPLEMENTATION_MODEL`, `AGENTIC_REVIEW_MODEL`), mirroring a propose/review dual-agent
pattern; both default to `qwen2.5:7b`.

Collectors are **static evidence gatherers**, not tests: they check files exist and that required
`def <name>` strings are present. `mcp_collector` additionally imports `tools.py` by file path and
calls all four tools — but since tools return `{"error": ...}` instead of raising, that passes even
with every feature DB down. Prompts cap model output at 35–55 words; the orchestrator repeats the
cap in the user prompt. The CLI prints to stdout only — it writes no report files.

## Commands

No venv is checked in, no test suite, no linter, no formatter. Each component has its own
`requirements.txt`.

```bash
# from ai-services/
python -m venv .venv && source .venv/bin/activate
pip install -r mcp-server/requirements.txt -r rag-server/requirements.txt -r agentic_loop/requirements.txt
```

```bash
# HTTP servers — what containerised backends talk to. Run from the component dir.
cd mcp-server && python mcp_http_server.py     # :7001, override with PORT
cd rag-server && python rag_http_server.py     # :7002, override with PORT

# stdio MCP servers — for MCP clients (Claude Desktop, VS Code). mcp-config.json
# passes a bare "server.py", so the client's cwd must be mcp-server/.
cd mcp-server && python server.py
cd rag-server && python rag_server.py

# Smoke-test each module directly — every file has a __main__ that exercises its own tools.
cd mcp-server && python tools.py               # runs all 4 tools, prints JSON
cd rag-server && python rag_pipeline.py        # refresh → retrieve → answer
cd rag-server && python rag_eval.py            # P@5/R@5 benchmarks → retrieval-metrics.md

# Agentic loop — must be run from the repo root (it resolves prompts/ relative to it).
cd .. && python ai-services/agentic_loop/app_main.py
```

```bash
# Health checks
curl localhost:7001/health      # also lists registered MCP tools
curl localhost:7002/health
curl -X POST localhost:7001/list_expenses -H 'Content-Type: application/json' -d '{}'
curl -X POST localhost:7002/answer -H 'Content-Type: application/json' \
  -d '{"query":"What accommodation is logged for Tokyo?","k":5}'
```

Prerequisites for meaningful (non-`error`) output: Ollama running on `:11434` with `qwen2.5:7b`
pulled, and the feature database containers up (`budget-db` on 6004, `accommodation-db` on 6002).
Bring those up from the repo root with `./up-all.sh` (per-project `docker compose`, not the root
compose file).

## Integration with feature services

Only `budget-service` consumes these today. Its clients (`budget-service/budget-be/services/mcp_api.py`,
`rag_api.py`) add a layer this directory does not have: `MCP_ENABLED`/`RAG_ENABLED` env flags plus
per-request `X-MCP-Mode` / `X-RAG-Mode` headers, with a disabled service answering **403** — asserted
by `.github/workflows/student-4.yml`. A new feature integrating MCP/RAG should copy that client shape.

Wiring gotcha: `budget-service/docker-compose.yml` sets `MCP_SERVICE_URL` / `RAG_SERVICE_URL` to
`host.docker.internal:7001/:7002`, but the **root `docker-compose.yml` does not set them at all**.
Bring the stack up via `up-all.sh` (or the per-service compose file) or budget-be falls back to
`localhost:7001`, which is unreachable from inside its container.

No CI workflow builds or tests anything under `ai-services/` directly.

## Conventions

- Every path is derived from `REPO_ROOT` computed as `Path(__file__).resolve().parent...` — keep it
  that way rather than relying on cwd, and fix the comment-documented hop count if a file moves.
- Cross-feature data is read over each feature's database-tier HTTP API only. Nothing in
  `ai-services/` opens another service's SQLite file or imports its Python.
- Service URLs and model names are always `os.environ.get(NAME, <localhost default>)` so the same
  code runs on the host and behind `host.docker.internal`.
- `tool-contracts.md` in `mcp-server/` and `rag-server/` is the interface other students build
  against — update it in the same change as the tool.
