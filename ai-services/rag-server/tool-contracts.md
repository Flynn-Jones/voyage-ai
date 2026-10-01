# RAG Tool Contracts

Shared local RAG server (not containerised). Containerised backends call it at
`http://host.docker.internal:7002/<refresh|retrieve|answer>`.

Corpus sources (env var names): Destination DB API (`DESTINATION_DB_URL`, default
`http://localhost:6001`, tier_1), Budget DB API (`BUDGET_DB_URL`), Accommodation DB API
(`ACCOMMODATION_DB_URL`), factual repo docs (tier_2; READMEs with demo prompts/usage examples are excluded) and a repository
file index (tier_3). Databases are read only through their HTTP APIs, never through SQLite.
Generation uses `OLLAMA_GENERATE_URL` and `OLLAMA_MODEL`.

## Retrieval acceptance
Relevance decides what is returned; authority tier only breaks ties.
- Query terms: alphanumeric tokens (length > 1) minus a fixed stopword list, with light plural folding.
- A chunk is accepted when it shares at least one term and either
  (>= 2 shared terms and shared/query terms >= 0.5) or a shared term is a Destination city/country.
- Zero-overlap chunks are never returned. Ranking: relevance score, matched-term count, vector distance, tier.
- Candidates are the full corpus; Chroma supplies `distance` when available (`retrieval_mode`: `hybrid`,
  or `lexical_fallback` if the vector store fails).

## refresh_corpus
- Purpose: rebuild the corpus and vector index
- Input: `caller` (optional)
- Output: `status`, `chunk_count`, `collection`, `vector_store_status`, `source_status`
  (`destination_db` / `budget_db` / `accommodation_db`: `"ok"` or `"unavailable: <error>"`), `missing_docs`.
  An unavailable source contributes no chunks.

## retrieve_context
- Input: `query` (required), `k` (optional, default 5), `caller` (optional)
- Output: `status`, `retrieval_mode`, `candidate_count`, `rejected_count`, `results[]` with `rank`, `chunk_id`,
  `source_id`, `authority_tier`, `distance`, `text`, `relevance_score`, `matched_terms`.
  `results` may be empty (valid).

## answer_question
- Input: `query` (required), `k` (optional), `caller` (optional)
- Output always has `status`, `query`, `answer`, `citations[]`, `confidence_category`.

| Case | `status` | HTTP | `confidence_category` |
|---|---|---|---|
| Grounded answer | `success` | 200 | `High` / `Medium` / `Low` |
| No accepted context, or the model replies "Insufficient evidence" | `insufficient_context` | 200 | `Insufficient` (`citations: []`, no unsupported answer) |
| Ollama unreachable / non-2xx / error body / empty reply | `error`, `error_type: llm_unavailable` | 503 | `null` (`answer: null`) |
| Retrieval failure | `error`, `error_type: retrieval_failed` | 500 | `null` (`answer: null`) |

- `retrieval_summary`: `k`, `retrieved_count` (accepted), `candidate_count`, `rejected_count`,
  `retrieval_mode`, `top_score`. Present on success, insufficient_context and llm_unavailable.
- Confidence: `High` = top score >= 0.75 and >= 2 accepted chunks; `Medium` = top score >= 0.5 with >= 2 matched terms;
  `Low` = anything else accepted (e.g. a city-name-only match) or tier_3-only evidence.
- Ollama is not called when nothing is accepted.

## Audit
Every call appends to `rag-audit.jsonl` (`request_id`, `tool_name`, `tool_input`, `tool_output`, `timestamp`,
`duration_ms`, `validation_status`, `outcome`). Answer outcomes: `answer_generated`, `insufficient_context`,
`llm_declined`, `llm_unavailable`, `retrieval_failed`. A failed audit write never fails the call.
