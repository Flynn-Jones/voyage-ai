# Phase 2 proposal: enabling AI Mode in the cloud

**Status: proposal only — nothing here is implemented.** The current cloud baseline deliberately
forces `AI_ENABLED`/`MCP_ENABLED`/`RAG_ENABLED`/`MULTI_AGENT_ENABLED` to `false` for every backend
(see [`cloud-architecture.md`](cloud-architecture.md) §3), matching the documented rubric
requirement that the baseline deployment ships with AI off. This document proposes what it would
take to turn AI on for a subset of the cloud deployment, as a deliberate next phase — not a quick
flag flip.

## Why this is a real decision, not a config change

`ai-services/CLAUDE.md` is explicit: MCP, RAG, the Multi-Agent Server, and Ollama are **not
containerised on purpose** — there's exactly one shared instance of each for the whole team,
reached by containerised backends via `host.docker.internal` on a developer's machine. In AWS there
is no "host" for an ECS Fargate task to dial back into. Enabling AI Mode in the cloud means
containerising these four services for the first time, which is a real architectural change, not a
flag flip — hence a proposal, not a checklist to just run through.

## Decision 1: inference backend

| Option | Trade-off |
|---|---|
| **Self-hosted Ollama on Fargate**, same model (`qwen2.5:7b`) | Keeps the exact local behaviour. CPU-only Fargate will be slow for a 7B model (multi-second responses) and needs a generously sized task (≥4 vCPU / 8GB+); the task bills 24/7 whether or not it's answering a query. |
| **Self-hosted Ollama, smaller model** (`qwen2.5:0.5b`/`1.5b`) | CPU-viable at modest Fargate sizing, cheap to run continuously — but answer quality drops, and prompts tuned/tested against `qwen2.5:7b` locally may need re-validation. |
| **Managed inference (AWS Bedrock)** | No model-serving infra to run or pay for idly; pay-per-token instead of per-hour. Requires a real code change — swapping `core/ai.py`'s Ollama HTTP client (and the equivalent in each feature's AI-mode route) for a `boto3 bedrock-runtime` `invoke_model` call behind the same function signature, plus IAM scoped to `bedrock:InvokeModel` only. Trades local model control for managed reliability and cost-per-use instead of cost-per-uptime. |

**Recommendation to decide as a team, not pre-decided here:** if the showcase only needs AI Mode
live for a short demo window, Bedrock's per-token pricing likely wins on cost; if the team wants to
keep using exactly the already-tuned `qwen2.5:7b` prompts without any client-code changes,
self-hosted Ollama is simpler to reason about but costs money every hour it's running regardless of
traffic.

## Decision 2: topology

Add one more small ECS cluster (e.g. `voyageai-ai-cluster`) hosting `mcp-server`, `rag-server`,
`multi-agent-server`, and (if self-hosted) `ollama`, all on the existing `voyageai.local` Service
Connect namespace. The five feature backends already read `MCP_SERVICE_URL`/`RAG_SERVICE_URL`/
`MULTI_AGENT_SERVICE_URL` from environment variables (currently set to `""`) — enabling AI Mode for
a backend becomes: point that env var at the new Service Connect DNS name, then flip its `_ENABLED`
flag to `"true"`. No application code in the feature backends needs to change for this part; it's
exactly the same client shape documented in `ai-services/CLAUDE.md`'s "Integration with feature
services" section, just pointed at a cloud DNS name instead of `host.docker.internal`.

## Concrete steps

1. **Write Dockerfiles.** None exist today for `mcp-server`, `rag-server`, or `multi-agent-server` —
   each already has an HTTP transport (`mcp_http_server.py` :7001, `rag_http_server.py` :7002, and
   the multi-agent server's own app :7004), so this is a standard "wrap an existing Flask/http.server
   app" Dockerfile, not new application code.
2. **Decide RAG's persistence story.** `chroma/` and `corpus/*.jsonl` are currently local, gitignored,
   rebuilt by `refresh_corpus`. Simplest cloud option: each task rebuilds its own Chroma store on
   boot (mirrors the already-accepted pattern of feature databases reseeding on every Fargate task
   start — see `cloud-architecture.md` §4) rather than provisioning EFS for persistence. Revisit if
   corpus build time becomes a problem.
3. **Size (or swap) the inference backend** per Decision 1 above.
4. **Add manifest entries.** `scripts/deploy/aws/services.yml` is already the single source of truth
   the whole pipeline reads from — add `mcp-server`/`rag-server`/`multi-agent-server` (and `ollama`,
   if self-hosted) as new entries with their `ecr_repo`/`dockerfile`/`context`/`container_port`/
   `service_connect_name`. **No change needed to `cloud-deployment.yml` itself** — it already loops
   over every entry in `services.yml` for build, push, and task-definition registration.
5. **Enable incrementally, not all-at-once.** Recommend piloting on one feature first — Budget has
   the fullest existing MCP/RAG/Multi-Agent integration and the most test coverage, making it the
   natural first candidate. Flip its `MCP_SERVICE_URL`/`RAG_SERVICE_URL`/`MULTI_AGENT_SERVICE_URL`
   and matching `_ENABLED` flags only; leave the other four backends' AI routes disabled until the
   pilot is validated.
6. **Update the evidence contract.** Today, `ai-services/agentic_loop/collectors/cloud_collector.py`
   and `scripts/deploy/aws/smoke-test.sh` only assert AI routes report **disabled**. Once a backend's
   AI Mode is live, both need a corresponding "AI routes return a real, grounded answer" check for
   that backend specifically — mirroring how `testing-review` already validates MCP/RAG locally —
   while the remaining backends keep their existing disabled-check.
7. **Treat it as a feature-flagged pilot, not a permanent always-on state**, given both LLM inference
   and the extra Fargate tasks bill for uptime regardless of traffic. Enable for a demo window,
   disable the flags afterward, rather than leaving it running continuously by default.

## Open questions for the team to resolve before starting

1. Self-hosted Ollama vs. Bedrock vs. a smaller local model — see Decision 1.
2. Which single feature backend to pilot first (Budget is the recommendation above, not a decision).
3. Whether the showcase video needs AI Mode live in the cloud at all, or whether a recorded
   local-deployment demo (already fully working today, per the existing local setup) satisfies the
   rubric's distinction between "Local Deployment" (AI enabled) and "Cloud Baseline Deployment" (AI
   disabled by default) — if the rubric is satisfied by the local demo alone, this entire phase may
   not be necessary before submission.
