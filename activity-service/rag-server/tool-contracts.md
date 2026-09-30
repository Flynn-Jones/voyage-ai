# Tool Contracts — Activity Manager RAG

Three tools, implemented once in `rag_pipeline.py` and exposed two ways:

- `rag_server.py`: FastMCP over stdio, server name "Activity Manager RAG MCP", launched by `mcp-config.json`.
- `rag_http_server.py`: HTTP on `0.0.0.0:$RAG_PORT` (default 6013). The backend reaches it at `http://host.docker.internal:6013`.

## Shared rules

- **Success:** every tool returns a JSON object with `"status": "success"`.
- **Failure:** every tool returns `{"status": "error", "error": "<message>", "error_type": "<type>"}`. The pipeline never raises to the caller. `error_type` is one of:
  - `invalid_input`: HTTP 400.
  - `tool_error`: HTTP 500.
  - `llm_unavailable`: HTTP 500.
- **Audit:** every call appends one JSON line to `rag-audit.jsonl`. The fields are `request_id`, `trace_id`, `tool_name`, `tool_input`, `tool_output` (a summary), `timestamp`, `duration_ms`, `validation_status` (`pass`/`fail`) and `outcome`.
  - Retrieval is logged as chunk ids and distances, never chunk text.
  - Nested calls share one `trace_id`: an `answer_question` call's own retrieval, and an auto-refresh.
- **Authority tiers:**
  - `tier_1`: the activities database. This is the SQLite file when it holds activities, otherwise database-service `GET /activities` and `/assignments`.
  - `tier_2`: `docs/**/*.md|json` in ~80-word chunks, excluding the `rag-*.md` reports this pipeline writes.
  - `tier_3`: one repository file-index chunk.

## `refresh_corpus(caller="student")`

**Policy class:** read + index update.

**Purpose:** Rebuild `corpus/corpus.jsonl` and replace the ChromaDB collection `activity_enterprise_context` (cosine space).

**tier_1 chunks:**

- one `activity_<id>` chunk per activity: `Activity record: activity_id=…, name=…, category=…, price=…, duration=….` The schema has no destination column, so `category` is `activity_type`.
- one `assignment_<id>` chunk per scheduled time.
- summary chunks: `activity_count`, `activity_category_counts`, and one `activity_category_<slug>` per category.

**Output:**

- `status`, `caller`, `chunk_count`, `collection`, `corpus_path`
- `tier_counts`, `tier_1_source`
- `vector_store_status` (`ready` or `degraded`), `embedding_mode`
- when relevant, `embedding_fallback` and `vector_store_error`

**Errors:**

- No activity source is reachable: `tool_error`.
- ChromaDB fails: still `success`, with `vector_store_status: "degraded"` and the error message. Retrieval then uses lexical fallback.
- `EMBEDDING_MODE=ollama` and Ollama fails: the whole batch falls back to `hash`, reported in `embedding_fallback`.

## `retrieve_context(query, k=5, caller="student")`

**Policy class:** read.

**Purpose:** Return the top-k chunks for a short query.

**Input:**

- `query`: non-empty string of at most 1000 characters.
- `k`: integer from 1 to 20. Booleans and strings are rejected.

**Output:**

- `status`, `query`, `caller`, `k`
- `retrieval_mode`: `vector` or `lexical_fallback`
- `results[]`, each with `rank`, `chunk_id`, `source_id`, `authority_tier`, `distance` and `text`

**Behaviour:**

- Auto-refreshes when the collection is missing or empty.
- The query is embedded in the mode the index was built with.
- **Ranking:** by distance first; the authority tier only breaks exact ties. Lab 08 sorts by tier first, which lets a weak tier-1 hit outrank a strong tier-2 hit.
- **Lexical fallback:** used when ChromaDB is unavailable. It scores token overlap over `corpus.jsonl`, and its `distance` is 1 − (the fraction of query tokens matched).

**Errors:**

- Bad `query` or `k`: `invalid_input`.
- Any other failure: `tool_error`.

## `answer_question(query, k=5, caller="student")`

**Policy class:** read + grounded response.

**Purpose:** Answer from retrieved context only, with citations and a confidence category.

**Output:**

- `status`, `query`, `answer`
- `answer_source`: `deterministic` or `llm`
- `citations[]`, each with `chunk_id`, `source_id` and `authority_tier`
- `confidence_category`
- `retrieval_summary`: `k`, `retrieved_count`, `retrieval_mode`, `top_chunk`
- for LLM answers, also `model` and `model_output`

**Behaviour, in order:**

1. `retrieve_context`. A failure there is returned as-is, with its `error_type`.
2. **Deterministic answers** are computed over the full tier_1 record set, not the top-k. They apply when the query mentions activities and either:
   - names a category ("Food activities"): the answer lists every record in that category and cites them all; or
   - asks for a count ("how many / number of / total / count"): the answer is the total and cites `activity_count`.
3. **Relevance guard:** only chunks with `distance ≤ RAG_RELEVANT_DISTANCE` are passed to the model. If none qualify, the answer is `Insufficient evidence` with no model call.
4. **LLM answer:** `OLLAMA_MODEL` (default `qwen2.5:0.5b`) is called via `OLLAMA_GENERATE_URL` at temperature 0.
   - The prompt says: use only the context; reply exactly `Insufficient evidence` if the answer is missing; otherwise reply as `Answer:` / `Evidence:`.
   - Citations are the context chunks the model names under Evidence, or the whole context if it names none.
   - `Insufficient evidence` from the model yields no citations.

**Errors:**

- Ollama unreachable or erroring: `llm_unavailable`, with `retrieval_summary` and the would-be citations attached.

## Confidence rule

Confidence uses cosine distance (0 = identical) together with authority. Tier alone never decides it. The thresholds are environment variables:

- `RAG_STRONG_MATCH_DISTANCE` (default 0.35)
- `RAG_RELEVANT_DISTANCE` (default 0.6)

| Category | Condition |
|---|---|
| **Unknown** | No results, or the answer is `Insufficient evidence`. |
| **High** | The best relevant hit has distance ≤ `STRONG`, **and** there are ≥ 2 tier_1 hits within `RELEVANT`. |
| **Medium** | At least one hit within `RELEVANT`, and the best one is tier_1 or tier_2. |
| **Low** | Nothing within `RELEVANT`, or the best relevant hit is tier_3 (the repository index only). |

**Deterministic answers are always High.** They are exact computations over the complete tier_1 record set, not similarity guesses, and they cite every record they used.
