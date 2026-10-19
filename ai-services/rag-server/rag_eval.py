"""P@5 / R@5 retrieval benchmarks for the shared RAG corpus, per feature.

Each benchmark names the feature whose slice of the shared corpus it targets
(metadata.service), the scope its real caller sends (None = unscoped), and
relevance keywords. A retrieved chunk is relevant when it belongs to that
feature AND contains a keyword. Ground truth is derived from the live corpus
with the same rule, so R@5 = relevant retrieved / min(5, relevant in corpus)
instead of a hand-typed expected count that drifts with the data.

Run after a refresh against live databases:  python rag_eval.py [--refresh]
"""
import sys
from pathlib import Path

from rag_pipeline import read_corpus, refresh_corpus, retrieve_context

METRICS_PATH = Path(__file__).resolve().parent / "retrieval-metrics.md"
K = 5

BENCHMARKS = [
    # Destination (Student 1) — unscoped, as student-1/backend/rag_client.py sends it.
    {"feature": "destination", "query": "Which destination is known for street food?",
     "keywords": ["street food"]},
    {"feature": "destination", "query": "beach destinations in Australia", "keywords": ["beach", "australia"]},
    # Accommodation (Student 2) — unscoped.
    {"feature": "accommodation", "query": "accommodation options", "keywords": ["accommodation", "price_per_night"]},
    {"feature": "accommodation", "query": "accommodation in Tokyo price per night", "keywords": ["tokyo"]},
    # Activity (Student 3) — scope="activity", as activity-service/backend/routes/rag_mode.py sends it.
    {"feature": "activity", "scope": "activity", "query": "harbour sunset kayak",
     "keywords": ["harbour sunset kayak"]},
    {"feature": "activity", "scope": "activity", "query": "adventure activities", "keywords": ["adventure"]},
    {"feature": "activity", "scope": "activity", "query": "when is the chinatown street food crawl scheduled",
     "keywords": ["chinatown street food crawl"]},
    # Itinerary (Student 5) — scope="itinerary" + trip_reference (the first trip in the live corpus).
    {"feature": "itinerary", "scope": "itinerary", "query": "What activities are planned for this trip?",
     "keywords": ["itinerary record"], "trip_reference": "<first>"},
    # Budget (Student 4) — unscoped.
    {"feature": "budget", "query": "budget expenses over estimate", "keywords": ["expense", "cost"]},
    {"feature": "budget", "query": "transport expenses actual cost", "keywords": ["category=transport"]},
    # Shared repo docs — unscoped.
    {"feature": "shared", "query": "microservice architecture ports", "keywords": ["port", "service", "docker"]},
]


def chunk_service(chunk: dict) -> str:
    return (chunk.get("metadata") or {}).get("service", "shared")


def is_relevant(text: str, keywords: list[str]) -> bool:
    lowered = (text or "").lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def first_trip_reference(corpus_by_id: dict) -> str | None:
    trips = sorted({(c.get("metadata") or {}).get("trip_reference") for c in corpus_by_id.values()
                    if chunk_service(c) == "itinerary"} - {None})
    return trips[0] if trips else None


def evaluate_query(benchmark: dict, corpus_by_id: dict) -> dict:
    scope = benchmark.get("scope")
    trip = benchmark.get("trip_reference")
    if trip == "<first>":
        trip = first_trip_reference(corpus_by_id)
    extra = {"trip_reference": trip} if scope == "itinerary" else {}
    response = retrieve_context(benchmark["query"], K, caller="rag_eval", scope=scope, **extra)
    results = response.get("results", [])[:K]
    feature, keywords = benchmark["feature"], benchmark["keywords"]

    def relevant(chunk_id, text):
        stored = corpus_by_id.get(chunk_id, {})
        if trip and (stored.get("metadata") or {}).get("trip_reference") != trip:
            return False
        return chunk_service(stored) == feature and is_relevant(text, keywords)

    hits = [r for r in results if relevant(r.get("chunk_id"), r.get("text", ""))]
    in_corpus = sum(relevant(cid, c.get("text", "")) for cid, c in corpus_by_id.items())
    denominator = min(K, in_corpus)
    return {
        "feature": feature,
        "scope": scope,
        "query": benchmark["query"] + (f" (trip_reference={trip})" if trip else ""),
        "retrieval_mode": response.get("retrieval_mode"),
        "status": response.get("status"),
        "retrieved_chunk_ids": [r.get("chunk_id") for r in results],
        "relevant_chunk_ids": [r.get("chunk_id") for r in hits],
        "relevant_in_corpus": in_corpus,
        "p_at_5": round(len(hits) / K, 2),
        "r_at_5": round(len(hits) / denominator, 2) if denominator else None,
    }


def feature_summary(results: list[dict]) -> list[dict]:
    rows = []
    for feature in dict.fromkeys(r["feature"] for r in results):
        group = [r for r in results if r["feature"] == feature]
        recalls = [r["r_at_5"] for r in group if r["r_at_5"] is not None]
        rows.append({
            "feature": feature,
            "scope": group[0]["scope"] or "(unscoped)",
            "queries": len(group),
            "mean_p_at_5": round(sum(r["p_at_5"] for r in group) / len(group), 2),
            "mean_r_at_5": round(sum(recalls) / len(recalls), 2) if recalls else None,
        })
    return rows


def write_metrics_report(results: list[dict], refresh: dict | None) -> None:
    lines = ["# Retrieval Metrics", "",
             "Shared RAG server (:7002), one corpus / one Chroma collection. Generated by `rag_eval.py`.",
             "Relevant = chunk belongs to the benchmark's feature (`metadata.service`) and contains a keyword;",
             f"R@{K} = relevant retrieved / min({K}, relevant chunks of that kind in the corpus).", ""]
    if refresh:
        lines += [f"Corpus: {refresh.get('chunk_count')} chunks; per service: {refresh.get('service_counts')}; "
                  f"sources: {refresh.get('source_status')}; vector store: {refresh.get('vector_store_status')}.", ""]
    lines += ["## Per feature", "", "| Feature | Scope | Queries | Mean P@5 | Mean R@5 |", "| --- | --- | --- | --- | --- |"]
    for row in feature_summary(results):
        lines.append(f"| {row['feature']} | {row['scope']} | {row['queries']} | {row['mean_p_at_5']} | "
                     f"{row['mean_r_at_5'] if row['mean_r_at_5'] is not None else 'n/a'} |")
    lines += ["", "## Per query", ""]
    for result in results:
        lines.append(f"### [{result['feature']}] {result['query']}")
        lines.append(f"- Scope: {result['scope'] or '(unscoped)'}; mode: {result['retrieval_mode']}")
        lines.append(f"- Retrieved: {result['retrieved_chunk_ids']}")
        lines.append(f"- Relevant: {result['relevant_chunk_ids']} (relevant in corpus: {result['relevant_in_corpus']})")
        lines.append(f"- P@5: {result['p_at_5']}")
        lines.append(f"- R@5: {result['r_at_5'] if result['r_at_5'] is not None else 'n/a (none in corpus)'}")
        lines.append("")
    METRICS_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    refresh = refresh_corpus(caller="rag_eval") if "--refresh" in sys.argv else None
    corpus_by_id = {c["chunk_id"]: c for c in read_corpus()}
    results = [evaluate_query(b, corpus_by_id) for b in BENCHMARKS]
    for result in results:
        print(f"[{result['feature']}] {result['query']}  (scope={result['scope']}, mode={result['retrieval_mode']})")
        print("  Retrieved:", result["retrieved_chunk_ids"])
        print("  Relevant: ", result["relevant_chunk_ids"], f"of {result['relevant_in_corpus']} in corpus")
        print("  P@5:", result["p_at_5"], " R@5:", result["r_at_5"])
    print("--- per feature")
    for row in feature_summary(results):
        print(f"  {row['feature']:<14} {row['scope']:<11} P@5={row['mean_p_at_5']}  R@5={row['mean_r_at_5']}")
    write_metrics_report(results, refresh)
    print(f"wrote {METRICS_PATH}")


if __name__ == "__main__":
    main()
