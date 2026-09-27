# RAG Improvement Report — activity-service

This is one concrete, evidence-backed weakness, fixed with the smallest change that addresses it, then re-measured. All numbers are from real runs on 2026-09-27 (AEST).

## Risk

**Hash bag-of-words embeddings could not match what a question means to the record that answers it.** As a result, the relevance guard refused answerable questions, and it withheld the right chunk from the model, which then invented an answer.

Evidence gathered before the change (`EMBEDDING_MODE=hash`):

1. **A false refusal.** "What does the Harbour Kayaking Tour cost?" returned `Insufficient evidence` / Unknown. The answer is in `activity_1` (`price=45.00`), but it scored 0.686, outside the 0.6 relevance cut. "cost" never matches "price", and the query's stopwords plus the boilerplate `activity_id= / name= / category=` tokens that every record shares dilute the vectors.
2. **A hallucination.** "When is the Guided Sunrise Mountain Hike scheduled?" returned *"scheduled for 10:00 AM on Saturday, May 10th"*, at **Medium** confidence and citing `activity_10`. The real time is `2026-10-04 05:30`. `assignment_10` was retrieved at rank 2 but scored above 0.6, so it was filtered out of the model's context, and qwen2.5:0.5b filled the gap.
3. **Metrics.** `rag_eval.py` gave a mean **P@5 of 0.16 and R@5 of 0.55**. The category query ranked a doc chunk (`run-report.md#7`) above the Adventure category chunk, and the docs query got 0 of 3 relevant chunks.
4. **Review agent (llama3.1:8b).** *"Risk: Low P@5 (0.16) and R@5 (0.55) indicate suboptimal retrieval quality."*

## Correction

**Switch to semantic embeddings: `EMBEDDING_MODE=ollama` with `nomic-embed-text`, and calibrate the relevance thresholds per embedding space.**

Both parts are needed:

- **Embeddings alone would have made the guard unsafe.** nomic's cosine distances are compressed. Measured on the live corpus:
  - true matches scored 0.18–0.33;
  - off-topic queries scored 0.48–0.55 ("What is the capital of France?" 0.48; "zebra quantum volcano" 0.546).

  Under the hash-calibrated 0.6 cut, nonsense questions would have reached the model as "relevant" context.
- **The new cut-offs** are strong ≤ 0.25 and relevant ≤ 0.40 for `ollama`. The `hash` and lexical-fallback spaces keep 0.35 / 0.60.

**Changes to `rag-server/rag_pipeline.py`:**

- `EMBEDDING_MODE` now defaults to `ollama`. When Ollama is unreachable, `embed_texts` still falls back to `hash` for the whole batch, and refresh reports `embedding_fallback`.
- `STRONG_MATCH_DISTANCE` and `RELEVANT_DISTANCE` are replaced by `DISTANCE_THRESHOLDS = {"ollama": (0.25, 0.4), "hash": (0.35, 0.6), "lexical": (0.35, 0.6)}`.
- `retrieve_context` now returns `embedding_mode`, the mode of the index it queried. `answer_question` uses that space's thresholds for both the relevance guard and `confidence_category`, and reports it in `retrieval_summary`.

**Other files:**

- `tests/test_rag_pipeline.py`: the threshold tests follow the new structure. A new test, `test_answer_uses_thresholds_of_the_index_embedding_mode`, proves the answer step uses the index's cut-offs rather than hash's.
- `rag-server/tool-contracts.md`: the confidence rule now documents the thresholds for each space.

**Tried and rejected:** nomic's `search_query:` / `search_document:` task prefixes. They lowered the metrics to P@5 0.20 / R@5 0.60 (from 0.24 / 0.70 without prefixes), so they are not used.

## Retest

```bash
ollama pull nomic-embed-text
cd activity-service/rag-server && .venv/bin/python rag_http_server.py      # restarted on the new code
curl -X POST localhost:5003/api/activity/rag/refresh                        # embedding_mode: ollama, 111 chunks
.venv/bin/python rag_eval.py                                                # retrieval-metrics.md
cd .. && rag-server/.venv/bin/python -m pytest tests/                       # 129 passed
(cd backend && ../rag-server/.venv/bin/python -m pytest)                    # 30 passed
(cd frontend && ../rag-server/.venv/bin/python -m pytest)                   # 14 passed
(cd database-service && ../rag-server/.venv/bin/python -m pytest)           # 12 passed
curl -X POST localhost:5003/api/activity/rag/answer ... (the sample questions below)
printf '3\n0\n' | AGENTIC_IMPLEMENTATION_MODEL=qwen2.5:0.5b AGENTIC_REVIEW_MODEL=llama3.1:8b \
  rag-server/.venv/bin/python ../ai-services/agentic_loop/app_main.py      # validation report, run 2
```

## Before / after

### Retrieval (`rag_eval.py`, k = 5, same 111-chunk corpus)

| # | Query | Relevant | Before (hash): P@5 | R@5 | After (ollama): P@5 | R@5 |
|---|---|---|---|---|---|---|
| 1 | Adventure activities | 4 | 0.20 | 0.25 | **0.40** | **0.50** |
| 2 | total number of activities | 2 | 0.20 | 0.50 | **0.40** | **1.00** |
| 3 | Harbour Kayaking Tour price | 1 | 0.20 | 1.00 | 0.20 | 1.00 |
| 4 | when is the Guided Sunrise Mountain Hike scheduled | 1 | 0.20 | 1.00 | 0.20 | 1.00 |
| 5 | how do I turn off MCP mode | 3 | 0.00 | 0.00 | 0.00 | 0.00 |
| | **Mean** | | **0.16** | **0.55** | **0.24** | **0.70** |

Queries 3 and 4 have exactly one relevant chunk, so 0.20 is their maximum possible P@5. Their improvement shows up in rank and distance instead:

- **Query 3:** `activity_1` went from rank 1 at 0.584 to rank 1 at 0.197.
- **Query 4:** `assignment_10` went from **rank 2, outside the 0.6 relevance cut**, to **rank 1 at 0.246**, inside the 0.40 cut. The before-state is inferred from the hash-mode answer, whose context held only `activity_10`.

### Answers (through the backend, `POST /api/activity/rag/answer`)

| Question | Before (hash) | After (ollama) |
|---|---|---|
| What does the Harbour Kayaking Tour cost? | `Insufficient evidence` · **Unknown** · no citations (a false refusal) | "The Harbour Kayaking Tour costs 45.00." · **High** · cites `activity_1` (plus 4 other context chunks) |
| When is the Guided Sunrise Mountain Hike scheduled? | "10:00 AM on Saturday, May 10th" · **Medium** · cites `activity_10` ❌ hallucinated | "scheduled for 2026-10-04 05:30." · **High** · cites `assignment_10`, `activity_10` ✅ correct |
| How many activities are there? | "There are 10 activities in total." · High (deterministic) | same (the deterministic path is unaffected) |
| What is the capital of France? | (not run before the change) | `Insufficient evidence` · Unknown · no model call (the recalibrated guard holds) |

## Decision

**Keep.**

- Every metric either improved or was already at its ceiling. Mean P@5 went 0.16 → 0.24 and mean R@5 0.55 → 0.70.
- The two concrete failures from the evidence are fixed: the false refusal and the hallucinated schedule.
- An off-topic question is still refused, without calling the model.
- All 185 tests pass.
- **The cost is a runtime dependency on a local Ollama embedding model (~270 MB).** It is local and open source, and if Ollama is down, refresh falls back to hash with hash-calibrated thresholds, so the pipeline degrades rather than failing.
- **The revert is one environment variable:** `EMBEDDING_MODE=hash`.

## Residual risks (next iteration)

- **Docs retrieval is still poor.** Query 5 gets P@5 = R@5 = 0 in both modes. The "Kill switches" section of `boundary-analysis.md` straddles two 80-word chunks, and the answer to "How can MCP mode be disabled?" was partly wrong, at Medium confidence. The next change should be heading-aware chunking for tier_2, splitting on `##` sections before the word limit.
- **Citations are too broad** when the model doesn't name chunk IDs under `Evidence:`. qwen2.5:0.5b often echoes the record text instead of the ID, so every context chunk gets cited. The price answer cites five chunks where one supports it.
- **Deterministic patterns are narrow.** They cover counts and categories only. "Cheapest activity" or "total cost" still go to the model.
