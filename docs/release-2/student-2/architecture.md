# Architecture: Accommodation (student-2) — Release 2

## 1. Frontend → Backend → Database

```mermaid
flowchart LR
    U[Traveller] -->|HTML/HTMX| FE["frontend :3002\n(Jinja templates)"]
    FE -->|JSON over HTTP| BE["backend :5002\n(public REST API)"]
    BE -->|JSON over HTTP| DB["database :6002\n(SQLAlchemy + SQLite,\nonly tier that opens the DB file)"]
    BE -->|read-only HTTP| DEST["Destination database :6001\n(another feature's DB tier)"]
```

- Only `database/` touches `accommodation-db.sqlite`; `backend/` reaches it exclusively through
  `services/database_api.py` over HTTP, converting transport failures to 502 and 404s to 404.
- `backend/` also reads Destination's database tier directly (never its SQLite file) for
  cross-feature destination lookups.

## 2. AI-Mode, MCP and RAG flows (all disabled by default in the cloud)

```mermaid
flowchart TB
    BE["backend :5002"]
    subgraph gates [" disabled -> 403, checked before any work "]
      AI["/ai/recommend, /ai/health\nAI_ENABLED"]
      MCP["/accommodation/mcp/*\nMCP_ENABLED (+ X-MCP-Mode header)"]
      RAG["/accommodation/rag/*\nRAG_ENABLED (+ X-RAG-Mode header)"]
    end
    BE --> AI --> OLLAMA["Ollama :11434\n(qwen2.5:7b)"]
    BE --> MCP --> MCPSRV["shared MCP server :7001\n(non-containerised)"]
    BE --> RAG --> RAGSRV["shared RAG server :7002\n(non-containerised)"]
    MCPSRV --> ACCDB["accommodation-db :6002"]
```

- All three switches follow the same shape: read once at import time from an env var (default
  `true`), checked as the first line of the route body, 403 with a clear `error` message when
  off. `AI_ENABLED` is new in this change; `MCP_ENABLED`/`RAG_ENABLED` already existed.
- MCP and RAG additionally honour a per-request `X-MCP-Mode`/`X-RAG-Mode` header, matching the
  shared client shape documented in `ai-services/CLAUDE.md`.
- The shared MCP/RAG servers are deliberately **not containerised** — `backend` reaches them via
  `host.docker.internal` from inside Docker, the same pattern already used for Ollama.

## 3. Multi-Agent flow: Plan → Act → Observe → Human Review → Adapt

```mermaid
sequenceDiagram
    participant FE as frontend/curl
    participant BE as backend :5002\n(routes/multi_agent_mode.py)
    participant MAS as Multi-Agent Server :7004\n(accommodation_shortlist)
    participant MCP as MCP server :7001
    participant LLM as Ollama

    FE->>BE: POST /accommodation/multi-agent/run\n{destination, budget_cap, interests}
    BE->>BE: 403 if MULTI_AGENT_ENABLED=false (stop here)
    BE->>MAS: POST /workflows/accommodation_shortlist/runs
    MAS->>LLM: Planner prompt (choose read-only tools)
    LLM-->>MAS: plan (or fallback_plan on failure)
    MAS->>MCP: search_accommodations / get_accommodation_by_destination / list_destinations
    MCP-->>MAS: candidate accommodations (accommodation-db), destination reference data
    MAS->>MAS: Worker: deterministic Python ranking + budget/interest checks\n(no LLM touches a price)
    MAS->>LLM: Reviewer prompt (restate computed checks)
    LLM-->>MAS: review (grounded/clamped by validate_review, or fallback_review)
    MAS-->>BE: state: awaiting_human_review {plan, work, review}
    BE-->>FE: shortlist + risk + recommendations

    FE->>BE: POST .../runs/<id>/review {decision, approved_ids, rejected_ids}
    BE->>MAS: POST /runs/<id>/review
    MAS-->>BE: approved_items, rejected_ids, state
    BE->>MAS: POST /runs/<id>/applied (nothing written to accommodation-db: read-only workflow)
    MAS-->>BE: state: applied
    BE-->>FE: final result
```

- `accommodation_shortlist`'s `allowed_tools` are all read-only (`search_accommodations`,
  `get_accommodation_by_destination`, `list_destinations`); it never calls `create_accommodation`
  and never writes to accommodation-db — human review decides what to keep, and
  `routes/multi_agent_mode.py` reports the run "applied" purely to close its lifecycle out.
- A tool failure becomes a `no_data` evidence entry, not a run failure; an LLM plan that names the
  write tool, or a review that recommends booking a specific place, is rejected by
  `validate_plan`/`validate_review` and replaced by the deterministic fallback.
- Live evidence of this exact flow (not simulated): [`multi-agent-workflow-evidence.md`](multi-agent-workflow-evidence.md).

## 4. Cloud deployment flow (proposed, not yet implemented — see `cloud-deployment-proposal.md`)

The group is going for the bonus marks (AI enabled in the cloud + cloud security controls), so
the diagram below shows both the required default (AI disabled) and the opt-in bonus path (AI
host reachable, security-group-restricted):

```mermaid
flowchart LR
    subgraph ci [GitHub Actions]
      S2[student-2.yml\nbuild + test on every push to main]
      GATE["cloud-deployment.yml\nTODAY: Release 1 stub, not real yet\ninputs.ai_enabled, default false"]
    end
    S2 -->|green on main| GATE
    GATE -->|build + push images| REG[Amazon ECR]
    REG --> CAPP["Amazon ECS: Express Mode (frontends)\n+ plain Fargate behind a shared ALB (backends/db)"]
    CAPP -->|ai_enabled=false, the required default| HEALTH1[GET /health on every tier,\nassert every AI route -> 403]
    CAPP -.->|ai_enabled=true, bonus opt-in,\nprivate VPC call only| AIHOST["AI host (EC2, security-group-restricted\nto the ECS tasks only):\nollama :11434 · mcp :7001 · rag :7002 · multi-agent :7004"]
    AIHOST -.-> HEALTH2[smoke-check: AI host reachable,\none real AI route works end to end]
```

- The required baseline disables every AI-shaped surface by default, using the same env vars
  already proven by `docker-compose.ci.yml` and the `verify-ai-disabled` CI job — no new
  mechanism, just the same flags applied by the (not-yet-built) deployment pipeline instead of a
  compose override. `inputs.ai_enabled` defaults to `false` so this stays the default path.
- The bonus path points the same env vars at an EC2 host running the exact same
  `ollama`/`mcp_http_server.py`/`rag_http_server.py`/`multi-agent-server/server.py` processes
  already used locally, reachable only from the feature services' security group — see
  `cloud-deployment-proposal.md` sections 3-4 for the full design and the security controls around it.
- This diagram is illustrative: `cloud-deployment.yml` does not implement any of this today, and
  this change does not create it — see `cloud-deployment-proposal.md` for the full reasoning and
  what the team still needs to agree on.
