"""Environment-driven settings for the shared Multi-Agent Server.

Every value is read at call time (not import time) so tests and the CLI can
override them with environment variables without reloading modules.
"""
import os
from pathlib import Path

# config.py lives in ai-services/multi-agent-server/
SERVICE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SERVICE_DIR.parent.parent

SERVICE_NAME = "multi-agent-server"
DEFAULT_PORT = 7004  # 7003 was Activity's retired server; do not reuse it.
PROBE_TIMEOUT_SECONDS = 2  # /health dependency probes; they run in parallel


def port() -> int:
    return int(os.environ.get("MULTI_AGENT_PORT", str(DEFAULT_PORT)))


def host() -> str:
    return os.environ.get("MULTI_AGENT_HOST", "0.0.0.0")  # noqa: S104 - host service reached from containers


def ollama_base_url() -> str:
    return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


def ollama_model() -> str:
    return os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")


def ollama_timeout() -> float:
    return float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "180"))


def mcp_service_url() -> str:
    return os.environ.get("MCP_SERVICE_URL", "http://localhost:7001/mcp").rstrip("/")


def rag_service_url() -> str:
    return os.environ.get("RAG_SERVICE_URL", "http://localhost:7002").rstrip("/")


def service_timeout() -> float:
    return float(os.environ.get("MULTI_AGENT_SERVICE_TIMEOUT_SECONDS", "240"))


def db_path() -> Path:
    return Path(os.environ.get("MULTI_AGENT_DB_PATH", str(SERVICE_DIR / "runs.db")))


def audit_log_path() -> Path:
    return Path(os.environ.get("MULTI_AGENT_AUDIT_LOG", str(SERVICE_DIR / "logs" / "audit.jsonl")))
