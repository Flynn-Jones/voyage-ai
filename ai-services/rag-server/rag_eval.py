"""P@5 / R@5 retrieval benchmarks for the shared RAG corpus."""
from pathlib import Path

from rag_pipeline import retrieve_context

METRICS_PATH = Path(__file__).resolve().parent / "retrieval-metrics.md"

BENCHMARKS = [
    {"query": "budget expenses over estimate", "relevant_keywords": ["expense", "cost"], "expected_relevant": 2},
    {"query": "accommodation options", "relevant_keywords": ["accommodation", "price_per_night"], "expected_relevant": 1},
    {"query": "microservice architecture ports", "relevant_keywords": ["port", "service", "docker"], "expected_relevant": 1},
]


def is_relevant(text: str, keywords: list[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def evaluate_query(benchmark: dict) -> dict:
    response = retrieve_context(benchmark["query"], 5)
    results = response.get("results", [])[:5]
    relevant = [r for r in results if is_relevant(r.get("text", ""), benchmark["relevant_keywords"])]

    return {
        "query": benchmark["query"],
        "retrieved_chunk_ids": [r.get("chunk_id") for r in results],
        "relevant_chunk_ids": [r.get("chunk_id") for r in relevant],
        "p_at_5": len(relevant) / 5,
        "r_at_5": min(1.0, len(relevant) / max(benchmark["expected_relevant"], 1)),
    }


def write_metrics_report(results: list[dict]) -> None:
    lines = ["# Retrieval Metrics", ""]
    for result in results:
        lines.append(f"## {result['query']}")
        lines.append(f"- Retrieved: {result['retrieved_chunk_ids']}")
        lines.append(f"- Relevant: {result['relevant_chunk_ids']}")
        lines.append(f"- P@5: {result['p_at_5']}")
        lines.append(f"- R@5: {result['r_at_5']}")
        lines.append("")
    METRICS_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    results = [evaluate_query(b) for b in BENCHMARKS]
    for result in results:
        print("Query:", result["query"])
        print("Retrieved:", result["retrieved_chunk_ids"])
        print("Relevant:", result["relevant_chunk_ids"])
        print("P@5:", result["p_at_5"])
        print("R@5:", result["r_at_5"])
        print("---")
    write_metrics_report(results)


if __name__ == "__main__":
    main()
