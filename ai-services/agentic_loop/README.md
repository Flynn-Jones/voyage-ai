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
`--mode activity_rag` is available but is never part of `all`.
`--no-llm` skips the optional LLM commentary.

## Verdict / exit semantics
- Exit 0 only if every selected deterministic validator passes; exit 1 otherwise.
- The collector decides the verdict. LLM commentary runs only after a PASS and can never change it;
  an unavailable LLM is reported separately.

## Tests
`cd ai-services/agentic_loop && .venv/bin/python -m pytest -q`
