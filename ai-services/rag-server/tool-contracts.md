# RAG Tool Contracts

Shared local RAG server (not containerised). Containerised backends call it at
`http://host.docker.internal:7002/<refresh|retrieve|answer>`.

## refresh_corpus
- Purpose: rebuild the corpus and vector index from budget-db, accommodation-db, and repo docs
- Input: `caller` (optional)
- Output: `status`, `chunk_count`, `collection`, `vector_store_status` or `error`

## retrieve_context
- Purpose: retrieve the top-k relevant chunks for a query
- Input: `query` (required), `k` (optional, default 5), `caller` (optional)
- Output: `status`, `results[]` with `chunk_id`, `source_id`, `authority_tier`, `distance`, `text`

## answer_question
- Purpose: answer a question grounded only in retrieved context
- Input: `query` (required), `k` (optional), `caller` (optional)
- Output: `answer`, `citations[]`, `confidence_category` (`High`/`Medium`/`Low`/`Insufficient`)
- When no sufficiently relevant context is retrieved, returns `confidence_category: "Insufficient"` and an
  insufficient-evidence answer instead of a generated guess.
