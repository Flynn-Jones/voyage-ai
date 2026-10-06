# R1-P05 — Shared agentic-loop repair and final live validation (evidence)

Date 2026-10-01, local macOS, branch `student-1-release-1`. Nothing committed.

## Files changed
- `ai-services/agentic_loop/collectors/mcp_collector.py` — rewritten: official MCP SDK over Streamable HTTP (`MCP_SERVICE_URL`, `/mcp`); no import of `tools.py`.
- `ai-services/agentic_loop/collectors/rag_collector.py` — rewritten: `GET /health` + `POST /answer` (`RAG_SERVICE_URL`); no source-string inspection, no `rag_pipeline` import, no `/refresh`.
- `ai-services/agentic_loop/core/reporter.py` — small `Trace` helper (uses existing `stage()`).
- `ai-services/agentic_loop/core/orchestrator.py` — `run_mode(..., use_llm=True) -> (passed, text)`; collector verdict authoritative; LLM commentary only after PASS and wrapped so any exception yields `LLM COMMENTARY UNAVAILABLE` with the verdict unchanged.
- `ai-services/agentic_loop/core/ai.py` — malformed Ollama HTTP-200 bodies (invalid JSON, wrong shape, missing/non-string content) return the controlled error tuple instead of raising.
- `ai-services/agentic_loop/app_main.py` — `--mode {mcp,rag,activity_rag,all}`, `--no-llm`, exit code; interactive menu preserved.
- `ai-services/agentic_loop/requirements.txt` (+`mcp==1.30.0`, the version used by `ai-services/mcp-server`), new `requirements-dev.txt`, `pytest.ini`, `README.md`.
- New tests: `tests/conftest.py`, `test_mcp_collector.py`, `test_rag_collector.py`, `test_cli.py`.
- `prompts/mcp/implementation/tool_selection_prompt.txt`, `prompts/rag/implementation/rag_implementation_prompt.txt` — reworded to assess the PLAN/ACT/OBSERVE/ADAPT trace (no fixed tool counts).
- Activity collector/mode untouched. `.venv` is local and gitignored (`.gitignore:157`).

## Setup
`python3.11 -m venv ai-services/agentic_loop/.venv && ai-services/agentic_loop/.venv/bin/pip install -r ai-services/agentic_loop/requirements-dev.txt`
Services: Ollama :11434 (host); `docker compose -f student-1/docker-compose.yml up -d --build` (:6001/:5001/:3001 all health 200); MCP `cd ai-services/mcp-server && .venv/bin/python mcp_http_server.py` (:7001); RAG `cd ai-services/rag-server && OLLAMA_MODEL=llama3.1:8b .venv/bin/python rag_http_server.py` (:7002). Env names used: `OLLAMA_MODEL`, `AGENTIC_IMPLEMENTATION_MODEL`, `AGENTIC_REVIEW_MODEL`.
Corpus refresh (operator step) `POST :7002/refresh` -> `status: success`, `chunk_count: 26`, `source_status.destination_db: "ok"`; `budget_db` / `accommodation_db` `unavailable` (teammate services not running; partial availability is supported by the RAG server).

## Automated results (actual)
- Agentic loop: `cd ai-services/agentic_loop && .venv/bin/python -m pytest -q` -> `71 passed` (53 + 18 added to isolate optional LLM failures: malformed Ollama 200 responses and a raising `ai.call` after a deterministic PASS keep PASS and exit 0).
- Shared MCP regression: `18 passed, 1 warning`.
- Shared RAG regression: `41 passed`.
- Student 1: `python3 -m pytest -q -m "not live"` -> `199 passed, 26 deselected`; live (stack + MCP + RAG up): `-m live` -> `22 passed, 4 skipped` (4 = disabled-mode tests, by design); full `pytest` -> `221 passed, 4 skipped`.

## Ground truth and direct service checks
- Destination DB `GET :6001/destinations?country=Japan` -> Tokyo, Kyoto, Osaka.
- MCP `/health` lists `list_destinations`; `mcp_probe.py list` -> 5 tools; `mcp_probe.py call list_destinations '{"country":"Japan"}'` -> isError false, source `destination-database`, count 3, Tokyo/Kyoto/Osaka.
- RAG supported -> HTTP 200, `success`, High, answer "Osaka is known for street food and nightlife.", citations `destination_3/5/1` (`destination-db:/destinations`), retrieved_count 3.
- RAG unsupported ("What is the capital of Mars?") -> HTTP 200, `insufficient_context`, citations `[]`, `Insufficient`, retrieved_count 0, fixed insufficient-evidence answer.

## Shared agentic loop (live)
Command: `ai-services/agentic_loop/.venv/bin/python ai-services/agentic_loop/app_main.py --mode all --no-llm` -> **MCP PASS, RAG PASS (2/2 tasks), OVERALL PASS, exit 0**.
- MCP: PLAN registry listed, selected `list_destinations` with `{"country":"Japan"}` (schema-supported); ACT `call_tool` over `http://localhost:7001/mcp`; OBSERVE isError=False source=destination-database count=3 cities Tokyo/Kyoto/Osaka; ADAPT PASS.
- RAG supported: ADAPT PASS (success, High, 3 Destination DB citations, retrieved_count 3, mentions Osaka). Unsupported: ADAPT PASS "cannot be answered from the available grounded knowledge; refused rather than fabricating".
- LLM-enabled variant (both model variables set to `llama3.1:8b`): same deterministic PASS, exit 0; commentary is secondary and only follows a PASS.
- Full trace in `R1-P05-agentic-loop.txt`.

## Negative control
Shared MCP process stopped; `--mode mcp --no-llm` -> `[MCP][ADAPT] FAIL transport ... (dependency)`, `VERDICT FAIL`, exit 1, no traceback. MCP restarted and /health 200 afterwards.

## Destination checks (via HTTP requests against :3001 — rendered HTML inspected, no visual browser)
- `/` lists 10 destinations; `/destinations/1` 200; search `q=Osaka` -> 1 result.
- `/compare` (city_a=Tokyo, city_b=Osaka, preferences=food) -> comparison rendered, generated by qwen2.5:0.5b.
- `/mcp-lookup` Country=Japan -> Tokyo, Kyoto, Osaka (equals DB ground truth).
- `/ask` supported -> "Osaka is known for street food and nightlife.", Confidence High, citations destination_3/5/1.
- `/ask` "What is the capital of Mars?" -> "Not enough grounded evidence...", Confidence Insufficient, no citations.

## Known limitations
- Browser checks were HTTP-level (rendered HTML), not a visual browser session.
- The supported-answer check requires the answer to mention "Osaka"; model wording could vary with other models.
- Corpus refresh is an operator step; the loop fails (not refreshes) if destination chunks are missing.
- Activity mode was verified only via stubbed orchestrator/CLI tests, not live.
- Standalone Student 1 stack, MCP and RAG processes were left running after validation.
