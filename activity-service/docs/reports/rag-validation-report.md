# RAG Validation Report — Dual-Agent Review (activity-service)

This review was produced by the shared agentic loop, menu option **3 – Activity RAG**, on 2026-09-27 (AEST).

```bash
printf '3\n0\n' | AGENTIC_IMPLEMENTATION_MODEL=qwen2.5:0.5b AGENTIC_REVIEW_MODEL=llama3.1:8b \
  activity-service/rag-server/.venv/bin/python ai-services/agentic_loop/app_main.py
```

**The flow:**

1. `collectors/activity_rag_collector.py` gathers the evidence (OBSERVE).
2. `qwen2.5:0.5b` receives `prompts/lab8/implementation/rag_implementation_prompt.txt` plus the evidence (IMPLEMENTATION).
3. `llama3.1:8b` receives `rag_review_prompt.txt` and `rag_reasoning_prompt.txt`, concatenated as its system prompt (REVIEW).

**The collector runs the tools; it does not just check that they exist.** It:

- confirms the required files and the three `def` lines;
- executes `refresh_corpus`, `retrieve_context("total number of activities")` and `answer_question("What does the Harbour Kayaking Tour cost?")`;
- reads the latest mean P@5 / R@5 from `retrieval-metrics.md`.

The lab's collector only checks that strings exist.

The loop was run twice: before the IMPROVE step (hash embeddings) and after it (Ollama embeddings). Both outputs are copied verbatim below.

## Run 1: before IMPROVE (EMBEDDING_MODE=hash)

### Evidence Collected (OBSERVE)

```
Activity RAG evidence: activity-service/rag-server/ has rag_pipeline.py, rag_server.py (FastMCP stdio),
rag_http_server.py and rag_eval.py; 3 tools defined. refresh_corpus -> chunk_count=111,
tiers={'tier_1': 28, 'tier_2': 82, 'tier_3': 1}, embedding_mode=hash, vector_store=ready.
retrieve_context('total number of activities', k=5) -> retrieval_mode=vector, 5 results,
top=activity_count (tier_1, distance=0.750). answer_question('What does the Harbour Kayaking Tour cost?')
-> status=success, answer_source=deterministic, answer='Insufficient evidence', citations=[],
confidence=Unknown. Latest eval: mean P@5=0.16, mean R@5=0.55.
```

### Implementation Agent (qwen2.5:0.5b)

```
The activity-service/rag-server/ rag_pipeline.py has the correct RAG pipeline configuration.
```

### Review Agent (llama3.1:8b)

```
Risk: Low P@5 (0.16) and R@5 (0.55) indicate suboptimal retrieval quality.
Correction: Review corpus design and chunking strategy to improve retrieval performance.
Retest: Evaluate retrieval quality with updated corpus and chunking configuration.
```

## Run 2: after IMPROVE (EMBEDDING_MODE=ollama, nomic-embed-text)

### Evidence Collected (OBSERVE)

```
Activity RAG evidence: activity-service/rag-server/ has rag_pipeline.py, rag_server.py (FastMCP stdio),
rag_http_server.py and rag_eval.py; 3 tools defined. refresh_corpus -> chunk_count=111,
tiers={'tier_1': 28, 'tier_2': 82, 'tier_3': 1}, embedding_mode=ollama, vector_store=ready.
retrieve_context('total number of activities', k=5) -> retrieval_mode=vector, 5 results,
top=activity_count (tier_1, distance=0.143). answer_question('What does the Harbour Kayaking Tour cost?')
-> status=success, answer_source=llm, answer='The Harbour Kayaking Tour costs 45.00.',
citations=['activity_1', 'activity_6', 'assignment_1', 'activity_category_adventure', 'activity_2'],
confidence=High. Latest eval: mean P@5=0.24, mean R@5=0.70.
```

### Implementation Agent (qwen2.5:0.5b)

```
The activity-service/rag-server/ rag_pipeline.py has the following RAG pipeline: refresh_corpus,
retrieve_context, answer_question. Latest eval: mean P@5=0.24, mean R@5=0.70.
```

### Review Agent (llama3.1:8b)

```
Risk: Low P@5 (0.24) indicates potential issues with retrieval quality.
Correction: Review chunk count (111) and tier distribution to ensure sufficient relevant chunks.
Retest: Evaluate retrieval mode (vector) and distance metric (0.143) for accuracy.
```

## Assessment of the review itself

- **Implementation agent (qwen2.5:0.5b).** In both runs it mostly restates the evidence. It does not check the five contract points in its prompt one by one: citations, confidence, `Insufficient evidence` and audit are not mentioned. At 0.5b parameters it adds little beyond the collector's own summary.
- **Review agent (llama3.1:8b).**
  - It followed the `Risk / Correction / Retest` format both times. It did not add the `Strengths / Risks / Recommendations` block from the reasoning prompt; with both prompts concatenated, it answered only the first format.
  - Its risk (low P@5) is real, but its corrections are generic.
  - It did not notice the most concrete defect in the Run 1 evidence: a direct price question about a record that exists came back as `Insufficient evidence`.
  - It did not flag that the Run 2 price answer cites five chunks when only `activity_1` supports it.
- **Where the IMPROVE choice came from.** The review supplied the direction (retrieval quality). The specific weakness (hash embeddings) was chosen from the evaluation metrics and live answers; see `rag-improvement-report.md`.
- **A caveat on P@5.** Two of the five benchmark queries have exactly one relevant chunk, so their P@5 can never exceed 0.20. A mean P@5 of 0.24 therefore understates retrieval quality more than the review assumes.
