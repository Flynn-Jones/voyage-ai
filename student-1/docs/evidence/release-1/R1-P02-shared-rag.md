# R1-P02 - Shared RAG repair (Destination coverage, relevance gate, honest failures)

Scope: `ai-services/rag-server/` only. `ai-services/mcp-server/**` and R1-P01 (`89d166a`) untouched.

## Changes
- `rag_pipeline.py`: Destination loader (via Destination DB API), relevance-first acceptance/ranking,
  zero-overlap rejection, confidence from relevance, `insufficient_context` status, explicit `llm_unavailable` error,
  `source_status`/`missing_docs` on refresh, richer audit, non-failing audit writes.
- `rag_http_server.py`: `success`/`insufficient_context` -> 200, `llm_unavailable` -> 503, other errors -> 500.
- `tool-contracts.md`: updated contract. `tests/`: 41 focused tests (see Results).
- Not changed: `rag_server.py`, embeddings/Chroma setup, default `OLLAMA_MODEL`, `rag_eval.py`, Student 1 UI/backend.

## Env var names (values not recorded)
`DESTINATION_DB_URL`, `BUDGET_DB_URL`, `ACCOMMODATION_DB_URL`, `OLLAMA_GENERATE_URL`, `OLLAMA_MODEL`, `PORT` (default 7002).

## Commands
```
cd ai-services/rag-server
python3.11 -m venv .venv && .venv/bin/pip install -r tests/requirements.txt
.venv/bin/python -m pytest tests
OLLAMA_MODEL=llama3.1:8b .venv/bin/python rag_http_server.py     # port 7002
```

## Results
- Tests: `41 passed` (30 original + 8 real-corpus regression cases + 3 stale-persisted-corpus cases). Mutation checks: with the READMEs restored to `DOC_SOURCES`, 8 tests fail; with `EXCLUDED_SOURCE_IDS` emptied, the 2 stale-corpus tests fail.
- Live (Destination DB API on 6001 with the 10 seeded rows, `OLLAMA_MODEL=llama3.1:8b`):
  - `/refresh` -> 200, `destination_db: ok`.
  - `/retrieve` "street food and nightlife" -> `destination_3`, `destination_5` (+1 README chunk), with `matched_terms`;
    "quantum physics homework help" -> `results: []`.
  - `/answer` supported -> 200 `success`, "Osaka is known for street food and nightlife.", citations from
    `destination-db:/destinations`, `High`. "average daily cost in Paris" -> 200.0, cites `destination_9`.
  - `/answer` "capital of Mars" -> 200 `insufficient_context`, no citations, no LLM call.
  - Ollama unreachable (dead port) and unpulled default model (`qwen2.5:7b`, 404) -> 503 `llm_unavailable`.
  - Destination DB stopped -> `/refresh` 200 with `destination_db: unavailable: ...`, 40 chunks (no destination chunks).
    "recommended trip length for Kyoto" -> `insufficient_context`.
  - Audit log shows `answer_generated`, `insufficient_context`, `llm_unavailable` outcomes.

## Deviations / known limitations
- Docker daemon was not running, so the Destination DB service (`student-1/database/app.py`) was run directly
  against a scratch SQLite file instead of via compose. Same API; RAG never touched SQLite.
- Budget (6004) and Accommodation (6002) services were not running; refresh reported them unavailable.
- Word-overlap acceptance is deliberately simple (no document classifier).
- Default `OLLAMA_MODEL=qwen2.5:7b` is not installed on this machine; validation used `llama3.1:8b`.
- `rag_eval.py` and the Itinerary README source were left as optional follow-ups.

## Codex blocker fix: README demo text was answerable evidence
- Problem: with the Destination DB down, "street food and nightlife" matched README demo prompts/taglines
  and produced a `success` answer ("Tokyo...") with README citations and `High` confidence.
- Fix: `README.md` and `student-1/README.md` removed from `DOC_SOURCES` (comment in code explains why).
  Both leaked (root README: tagline + demo prompts). `DOC_SOURCES` is now `docs/ARCHITECTURE.md`,
  `budget-service/SUMMARY.md`, `accommodation-service/SUMMARY.md` (the first two do not exist yet and are
  reported in `missing_docs`). No classifier or routing added; endpoint contracts/statuses unchanged.
- Regression tests (`tests/test_rag_pipeline.py`, `real_docs_env`): use the production `DOC_SOURCES` and the real
  repo checkout with the Destination API down; three queries must return `insufficient_context`, `citations: []`,
  `Insufficient`, 0 Ollama calls, and no README source in the corpus. A second test confirms that with
  Destination data available, citations come only from `destination-db:/destinations`.
- Live re-run (`llama3.1:8b`): DB up -> 200 `success`, Osaka, `High`, citations `destination_3/5/1`;
  unrelated query -> `insufficient_context`; DB down -> `insufficient_context`, no citations (16 chunks);
  Ollama dead port -> 503 `llm_unavailable`.
- Trade-off: the project README is no longer retrievable (e.g. "microservice architecture ports" has less coverage).

## Second Codex blocker fix: stale persisted corpus still held README chunks
- Problem: a corpus.jsonl/Chroma index built before the README exclusion could still serve README chunks
  (the service reuses a non-empty persisted corpus without refreshing).
- Real representation (checked in the original and current loaders, corpus.jsonl and Chroma metadata): `source_id` is the
  repo-relative path, i.e. `README.md` and `student-1/README.md`.
- Fix: `EXCLUDED_SOURCE_IDS = {"README.md", "student-1/README.md"}` in `rag_pipeline.py`; `_corpus_chunks()` drops
  those chunks before scoring, so they can't be accepted, affect confidence, be cited, or reach Ollama. Chroma is only
  used for distances over that filtered set. No refresh/versioning/migration added.
- Tests: persisted-style corpus.jsonl + Chroma seeded with legacy README chunks (plus one unrelated chunk so no auto-refresh
  runs), Destination API down, in both hybrid and lexical_fallback modes: `/retrieve` returns no README chunks,
  `/answer` -> `insufficient_context`, `[]`, `Insufficient`, 0 Ollama calls.
- Live: legacy README chunks injected into the real persisted corpus.jsonl, restart without refresh, Destination DB down ->
  `/retrieve` `[]`, `/answer` `insufficient_context`. Also re-run: DB up -> success/Osaka/High; unrelated -> insufficient_context;
  Ollama dead port -> 503 `llm_unavailable`.
- Known, non-blocking (out of scope): entity-name acceptance can still send e.g. "Tokyo mayor" to Ollama.
