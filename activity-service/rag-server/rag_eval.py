"""Retrieval evaluation for the Activity Manager RAG pipeline: P@5 and R@5 per benchmark query.

Refreshes the corpus first, so the metrics describe the index as it is now.
Benchmark queries are built from the live activities data (the corpus's
tier_1 records), and each query's relevant set is a predicate over the
corpus, so both follow the data instead of hard-coding chunk ids.

    P@5 = relevant retrieved / min(5, retrieved_count)
    R@5 = relevant retrieved / expected relevant (capped at 1.0)

Run from rag-server/: python rag_eval.py   (writes retrieval-metrics.md)
"""
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import rag_pipeline

K = 5
METRICS_PATH = Path(__file__).resolve().parent / "retrieval-metrics.md"

Benchmark = tuple[str, str, Callable[[dict], bool]]  # (label, query, is_relevant(chunk))


def records(corpus: list[dict], source_type: str) -> list[dict]:
    return [c for c in corpus if c["metadata"].get("source_type") == source_type]


def build_benchmarks(corpus: list[dict]) -> list[Benchmark]:
    activities = records(corpus, "activity_record")
    if not activities:
        raise RuntimeError("corpus has no activity records; is the activities database reachable?")
    category = Counter(a["metadata"]["category"] for a in activities).most_common(1)[0][0]
    first, last = activities[0]["metadata"], activities[-1]["metadata"]
    category_ids = {"activity_category_" + rag_pipeline.slug(category)}

    return [
        ("category", f"{category} activities",
         lambda c: c["chunk_id"] in category_ids or c["metadata"].get("category") == category),
        ("total count", "total number of activities",
         lambda c: c["chunk_id"] in ("activity_count", "activity_category_counts")),
        ("single record", f"{first['name']} price",
         lambda c: c["chunk_id"] == f"activity_{first['activity_id']}"),
        ("schedule", f"when is the {last['name']} scheduled",
         lambda c: c["metadata"].get("source_type") == "assignment_record"
         and c["metadata"].get("activity_id") == last["activity_id"]),
        ("docs/report", "how do I turn off MCP mode",
         lambda c: c["authority_tier"] == "tier_2" and "403" in c["text"]
         and ("MCP_ENABLED" in c["text"] or "X-MCP-Mode" in c["text"])),
    ]


def evaluate(label: str, query: str, is_relevant: Callable[[dict], bool], corpus: list[dict]) -> dict:
    result = rag_pipeline.retrieve_context(query, K, caller="rag_eval")
    if result["status"] != "success":
        raise RuntimeError(f"retrieval failed for {query!r}: {result['error']}")
    retrieved = [r["chunk_id"] for r in result["results"]]
    relevant = [c["chunk_id"] for c in corpus if is_relevant(c)]
    hits = [chunk_id for chunk_id in retrieved if chunk_id in relevant]
    return {
        "label": label, "query": query, "retrieved": retrieved, "relevant": relevant, "hits": hits,
        "retrieval_mode": result["retrieval_mode"],
        "precision": len(hits) / min(K, len(retrieved)) if retrieved else 0.0,
        "recall": min(1.0, len(hits) / len(relevant)) if relevant else 0.0,
    }


def render(rows: list[dict], refresh: dict) -> str:
    mean_p = sum(r["precision"] for r in rows) / len(rows)
    mean_r = sum(r["recall"] for r in rows) / len(rows)
    lines = [
        "# Retrieval Metrics — Activity Manager RAG",
        "",
        "Written by `rag-server/rag_eval.py`; do not edit by hand.",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- Embedding mode: `{refresh.get('embedding_mode')}`",
        f"- Retrieval mode: `{', '.join(sorted({r['retrieval_mode'] for r in rows}))}`",
        f"- Corpus: {refresh.get('chunk_count')} chunks {refresh.get('tier_counts')}, "
        f"tier_1 source `{refresh.get('tier_1_source')}`",
        f"- k = {K}; P@5 = relevant retrieved / min(5, retrieved); R@5 = relevant retrieved / expected relevant",
        "",
        "| # | Type | Query | Retrieved (rank order) | Relevant | Hits | P@5 | R@5 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, start=1):
        lines.append(
            f"| {i} | {r['label']} | {r['query']} | {', '.join(f'`{c}`' for c in r['retrieved'])} | "
            f"{', '.join(f'`{c}`' for c in r['relevant'])} | {len(r['hits'])} | "
            f"{r['precision']:.2f} | {r['recall']:.2f} |"
        )
    lines += ["", f"**Mean P@5: {mean_p:.2f} · Mean R@5: {mean_r:.2f}**", ""]
    return "\n".join(lines)


def main() -> int:
    refresh = rag_pipeline.refresh_corpus(caller="rag_eval")
    if refresh["status"] != "success":
        print(f"refresh failed: {refresh['error']}", file=sys.stderr)
        return 1
    corpus = rag_pipeline.read_corpus()
    rows = [evaluate(label, query, is_relevant, corpus) for label, query, is_relevant in build_benchmarks(corpus)]
    report = render(rows, refresh)
    METRICS_PATH.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
