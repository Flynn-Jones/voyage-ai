"""Scans ai-services/rag-server/ for the required files and the 3 required RAG tool functions."""
from pathlib import Path

REQUIRED_TOOLS = ["refresh_corpus", "retrieve_context", "answer_question"]


def collect(app_dir: Path, repo_root: Path) -> tuple:
    rag_server_dir = repo_root / "ai-services" / "rag-server"

    required_paths = [
        rag_server_dir / "rag_pipeline.py",
        rag_server_dir / "rag_server.py",
        rag_server_dir / "rag_http_server.py",
        rag_server_dir / "requirements.txt",
        repo_root / "prompts" / "rag" / "implementation" / "rag_implementation_prompt.txt",
        repo_root / "prompts" / "rag" / "review" / "rag_review_prompt.txt",
        repo_root / "prompts" / "rag" / "review" / "rag_reasoning_prompt.txt",
    ]

    missing = [str(path.relative_to(repo_root)) for path in required_paths if not path.exists()]
    if missing:
        return False, "RAG evidence incomplete. Missing: " + ", ".join(missing)

    pipeline_text = (rag_server_dir / "rag_pipeline.py").read_text(encoding="utf-8")
    missing_tools = [tool for tool in REQUIRED_TOOLS if f"def {tool}" not in pipeline_text]
    if missing_tools:
        return False, "rag_pipeline.py missing required tools: " + ", ".join(missing_tools)

    has_confidence = "confidence_category" in pipeline_text
    has_insufficient = "Insufficient" in pipeline_text

    return True, (
        "RAG evidence: ai-services/rag-server/ contains rag_pipeline.py, rag_server.py, and rag_http_server.py; "
        f"{len(REQUIRED_TOOLS)} tools defined (refresh_corpus, retrieve_context, answer_question); "
        f"confidence_category present={has_confidence}; insufficient-context handling present={has_insufficient}."
    )
