"""The AI timeout ladder holds across every service.

Ollama is CPU-only on the shared AI host, so these calls are slow by nature. The rule
(docs/release-2/cloud/ai-timeouts.md) is that **every hop must outwait the hop it calls** -
otherwise an outer layer gives up first and the user is shown a transport error for work
the inner service actually completed. That is exactly how activity's Multi-Agent Mode
broke: a 139s run behind a 60s frontend timeout.

The defaults live in a dozen files across five features, so this reads them out of the
source rather than importing five Flask apps, and fails if anyone inverts the ordering.
"""
import re

import pytest

import config

REPO_ROOT = config.REPO_ROOT

# var -> (path relative to the repo root, expected default)
BACKEND_DEFAULTS = {
    "OLLAMA_TIMEOUT_SECONDS": [
        ("activity-service/backend/services/ai_client.py", 180),
        ("budget-service/budget-be/services/llm_client.py", 180),
        ("accommodation-service/backend/src/services/llm_client.py", 180),
        ("itinerary-service/itinerary-be/services/llm_client.py", 180),
        ("student-1/backend/llm_client.py", 180),
        ("ai-services/rag-server/rag_pipeline.py", 180),
        ("ai-services/multi-agent-server/config.py", 180),
    ],
    "MCP_TIMEOUT_SECONDS": [
        ("activity-service/backend/routes/mcp_mode.py", 60),
        ("budget-service/budget-be/services/mcp_api.py", 60),
        ("accommodation-service/backend/src/services/mcp_api.py", 60),
        ("student-1/backend/mcp_client.py", 60),
    ],
    "RAG_TIMEOUT_SECONDS": [
        ("activity-service/backend/services/rag_api.py", 240),
        ("budget-service/budget-be/services/rag_api.py", 240),
        ("accommodation-service/backend/src/services/rag_api.py", 240),
        ("student-1/backend/rag_client.py", 240),
    ],
    "MULTI_AGENT_TIMEOUT_SECONDS": [
        ("activity-service/backend/services/multi_agent_api.py", 420),
        ("budget-service/budget-be/services/multi_agent_api.py", 420),
        ("accommodation-service/backend/src/services/multi_agent_api.py", 420),
        ("student-1/backend/multi_agent_client.py", 420),
        ("itinerary-service/itinerary-be/app.py", 420),
    ],
}

CASES = [(var, path, want) for var, rows in BACKEND_DEFAULTS.items() for path, want in rows]


def default_in(path: str, var: str):
    """The numeric default in `os.environ.get("VAR", "N")` (or os.getenv) in that file."""
    source = (REPO_ROOT / path).read_text(encoding="utf-8")
    match = re.search(rf'os\.(?:environ\.get|getenv)\(\s*"{var}"\s*,\s*"?([0-9.]+)"?', source)
    assert match, f"{path}: no env-configurable {var} default found"
    return float(match.group(1))


requires_repo = pytest.mark.skipif(
    not (REPO_ROOT / "activity-service").is_dir(),
    reason="feature services are not present (e.g. the AI host ships only ai-services/)",
)


@requires_repo
@pytest.mark.parametrize("var, path, want", CASES, ids=[f"{v}:{p}" for v, p, _ in CASES])
def test_timeout_default_matches_the_documented_ladder(var, path, want):
    assert default_in(path, var) == want


@requires_repo
def test_each_hop_outwaits_the_hop_it_calls():
    ollama = default_in("ai-services/multi-agent-server/config.py", "OLLAMA_TIMEOUT_SECONDS")
    mas_to_services = default_in("ai-services/multi-agent-server/config.py",
                                 "MULTI_AGENT_SERVICE_TIMEOUT_SECONDS")
    backend_to_rag = default_in("activity-service/backend/services/rag_api.py", "RAG_TIMEOUT_SECONDS")
    backend_to_mas = default_in("activity-service/backend/services/multi_agent_api.py",
                                "MULTI_AGENT_TIMEOUT_SECONDS")
    backend_to_mcp = default_in("activity-service/backend/routes/mcp_mode.py", "MCP_TIMEOUT_SECONDS")

    # a RAG call wraps one Ollama generation, so RAG must outwait Ollama
    assert backend_to_rag > ollama, f"RAG {backend_to_rag}s does not outwait Ollama {ollama}s"
    # the Multi-Agent Server calls RAG, and the backend waits on the whole run
    assert mas_to_services >= backend_to_rag - ollama or mas_to_services >= ollama, (
        f"MAS->services {mas_to_services}s is too tight for an Ollama-backed RAG call")
    assert backend_to_mas > backend_to_rag, (
        f"Multi-Agent {backend_to_mas}s does not outwait RAG {backend_to_rag}s")
    # MCP is plain database reads - it should be the quickest to give up
    assert backend_to_mcp < ollama, f"MCP {backend_to_mcp}s should be well under Ollama {ollama}s"


@requires_repo
def test_every_frontend_outwaits_its_own_multi_agent_backend():
    """The failure that started this: a frontend giving up before its backend does."""
    backend = default_in("activity-service/backend/services/multi_agent_api.py",
                         "MULTI_AGENT_TIMEOUT_SECONDS")
    for path in ("activity-service/frontend/app.py", "student-1/frontend/app.py"):
        source = (REPO_ROOT / path).read_text(encoding="utf-8")
        match = re.search(r'_read_timeout\(\s*"MULTI_AGENT_REQUEST_TIMEOUT_SECONDS"\s*,\s*([0-9.]+)', source)
        assert match, f"{path}: no MULTI_AGENT_REQUEST_TIMEOUT_SECONDS default"
        assert float(match.group(1)) > backend, (
            f"{path} waits {match.group(1)}s but its backend waits {backend}s")


@requires_repo
def test_nginx_frontends_outwait_their_backend():
    """budget-fe and itinerary-fe are nginx, whose proxy_read_timeout DEFAULTS to 60s -
    a missing directive is an invisible 60s ceiling, which is how ai-review got cut off."""
    backend = default_in("budget-service/budget-be/services/multi_agent_api.py",
                         "MULTI_AGENT_TIMEOUT_SECONDS")
    budget = (REPO_ROOT / "budget-service/budget-fe/nginx.conf").read_text(encoding="utf-8")
    budget_timeouts = [int(v) for v in re.findall(r"proxy_read_timeout\s+(\d+)s", budget)]
    assert budget_timeouts, "budget-fe nginx has no proxy_read_timeout (silent 60s default)"
    assert min(budget_timeouts) > backend, f"budget-fe nginx {budget_timeouts} vs backend {backend}s"

    itinerary = (REPO_ROOT / "itinerary-service/itinerary-fe/nginx.conf").read_text(encoding="utf-8")
    # every /api/ location needs one explicitly; inheriting nginx's 60s default is the bug
    api_locations = re.findall(r"location\s+(/api/[^\s{]*)\s*\{(.*?)\n    \}", itinerary, re.S)
    assert api_locations, "no /api/ locations parsed from itinerary-fe nginx.conf"
    for path, block in api_locations:
        assert "proxy_read_timeout" in block, (
            f"itinerary-fe '{path}' has no proxy_read_timeout, so it inherits nginx's 60s default")
