# Retrieval Metrics — Activity Manager RAG

Written by `rag-server/rag_eval.py`; do not edit by hand.

- Generated: 2026-09-27T05:20:36+00:00
- Embedding mode: `ollama`
- Retrieval mode: `vector`
- Corpus: 111 chunks {'tier_1': 28, 'tier_2': 82, 'tier_3': 1}, tier_1 source `database-service:http://localhost:6003`
- k = 5; P@5 = relevant retrieved / min(5, retrieved); R@5 = relevant retrieved / expected relevant

| # | Type | Query | Retrieved (rank order) | Relevant | Hits | P@5 | R@5 |
|---|---|---|---|---|---|---|---|
| 1 | category | Adventure activities | `activity_category_adventure`, `activity_category_counts`, `activity_category_sightseeing`, `activity_1`, `activity_count` | `activity_1`, `activity_7`, `activity_10`, `activity_category_adventure` | 2 | 0.40 | 0.50 |
| 2 | total count | total number of activities | `activity_count`, `activity_category_adventure`, `activity_category_wellness`, `activity_category_counts`, `activity_category_culture` | `activity_count`, `activity_category_counts` | 2 | 0.40 | 1.00 |
| 3 | single record | Harbour Kayaking Tour price | `activity_1`, `assignment_1`, `activity_6`, `activity_category_adventure`, `activity_2` | `activity_1` | 1 | 0.20 | 1.00 |
| 4 | schedule | when is the Guided Sunrise Mountain Hike scheduled | `assignment_10`, `activity_10`, `assignment_8`, `assignment_3`, `assignment_1` | `assignment_10` | 1 | 0.20 | 1.00 |
| 5 | docs/report | how do I turn off MCP mode | `docs/reports/integration-report.md#1`, `docs/reports/integration-report.md#16`, `docs/reports/integration-report.md#5`, `docs/reports/integration-report.md#4`, `docs/reports/run-report.md#17` | `docs/reports/boundary-analysis.md#7`, `docs/reports/integration-report.md#6`, `docs/reports/run-report.md#12` | 0 | 0.00 | 0.00 |

**Mean P@5: 0.24 · Mean R@5: 0.70**
