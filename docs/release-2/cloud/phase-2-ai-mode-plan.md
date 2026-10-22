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
is no "host" for an ECS Fargate task to dial back into. Enabling AI Mode in the cloud means standing
up a real equivalent of that "host" for the first time — a persistent EC2 instance rather than a
developer's laptop (see the Decision section below) — which is a real infrastructure change, not a
flag flip, hence a proposal, not a checklist to just run through.

## Decision: EC2, not a second ECS cluster — one host, preserving the existing shape

**Decided: self-hosted on a single EC2 instance, not ECS Fargate, and not Bedrock.**

`ai-services/CLAUDE.md` already states MCP, RAG, the Multi-Agent Server, and Ollama are
**deliberately not containerised** — one shared instance of each, reached via `host.docker.internal`
from a developer's laptop. Putting them on EC2 preserves that exact shape instead of reversing it:
the EC2 instance simply *becomes* the shared "host machine," persistent in the cloud instead of
someone's laptop. Concretely:

- **One EC2 instance** runs all four as plain processes — the same `mcp_http_server.py` (:7001),
  `rag_http_server.py` (:7002), the multi-agent server's own app (:7004), and `ollama serve`
  (:11434) already used locally, supervised by systemd units instead of a developer running them in
  a terminal. **No new Dockerfiles, no second ECS cluster, no Service Connect wiring** for these
  four — real complexity avoided, not deferred.
- **Instance size: CPU-only to start, `m7i-flex.large` (2 vCPU / 8GB RAM)** — this account's Free
  Tier restriction blocks launching any non-free-tier-eligible instance type at all (confirmed via
  `aws ec2 describe-instance-types --filters Name=free-tier-eligible,Values=true`), and
  `m7i-flex.large` is the largest-memory option on that list, with enough headroom to actually load
  `qwen2.5:7b` (≈4.7GB quantized). **Upgrading to a GPU instance later is a one-line change** (swap to
  `g4dn.xlarge`, ~$0.50–0.75/hr, and use an NVIDIA-ready AMI) if faster responses turn out to matter
  and the account's Free Tier restriction allows it — not a re-architecture, just a different
  `--instance-type` and AMI.
- Rejected: a second ECS Fargate cluster (Fargate has no GPU option at all, and would mean writing
  Dockerfiles for services this project has explicitly chosen not to containerise) and Bedrock (its
  per-token pricing is attractive, but it requires rewriting `core/ai.py`'s Ollama HTTP client and
  every feature's AI-mode route to a different API shape — a real code change this phase doesn't
  need to take on).

## Networking

The EC2 instance sits in the same VPC as `voyageai-cluster`, with a dedicated security group:
inbound only from the existing ECS task security group, on 7001/7002/7004/11434 — **never a public
inbound rule**, matching the existing pattern where only the ALB is internet-facing. The five
feature backends already read `MCP_SERVICE_URL`/`RAG_SERVICE_URL`/`MULTI_AGENT_SERVICE_URL` from
environment variables (currently `""`) — enabling AI Mode for a backend becomes: point that env var
at the EC2 instance's private IP, then flip its `_ENABLED` flag to `"true"`. No feature-backend
application code changes — it's exactly the client shape already documented in
`ai-services/CLAUDE.md`'s "Integration with feature services" section, just pointed at an EC2
private address instead of `host.docker.internal`.

**Real finding (discovered via a failing RAG corpus refresh, not anticipated when this was
written):** MCP and RAG also read the 5 feature databases directly (`BUDGET_DB_URL`,
`ACCOMMODATION_DB_URL`, etc.), and ECS Service Connect's `*.voyageai.local` DNS names turned out to
be **not resolvable VPC-wide** — Service Connect uses per-task Envoy sidecar proxies that resolve
those names only for other Service-Connect-enabled tasks in the same mesh. The AI host was never
part of that mesh, so the name genuinely didn't exist from its point of view (a
`NameResolutionError`, not a security-group problem — no amount of security-group tuning would have
fixed it). The fix: `scripts/deploy/aws/ai-db-nlb-setup.sh` creates an internal Network Load
Balancer in front of the 5 databases, registered by each task's current private IP — NLBs get a
real, VPC-wide-resolvable DNS name, independent of Service Connect. **Caveat:** ECS Fargate tasks
get a fresh IP on every replacement, and this NLB's targets are registered by IP, not auto-synced to
the ECS service — re-run `ai-db-nlb-setup.sh` any time a database task is redeployed and MCP/RAG
start failing to reach it.

## Concrete steps — status: 1, 2, 3, and the NLB fix are DONE and verified live; 4 and 5 are not started

1. **✅ Provision the EC2 instance and its security group.** `scripts/deploy/aws/ai-ec2-setup.sh`
   (one-time) creates a security group scoped to the ECS task security group only, a private S3
   bucket + IAM role for code delivery, and launches an `m7i-flex.large` running Amazon Linux whose
   user-data installs Ollama (its own installer sets up its systemd unit) and pulls `qwen2.5:7b` via
   the REST API (the `ollama` CLI panics with `$HOME is not defined` in cloud-init's context). It
   records the instance ID, private IP, and bundle bucket in SSM Parameter Store under
   `/voyageai/ai-host/*` — no fragile local files. `m7i-flex.large` (2 vCPU/8GB) was chosen because
   this account's Free Tier restriction blocks launching any non-free-tier-eligible instance type at
   all — confirmed via `describe-instance-types`, and the largest-memory option on that list.
2. **✅ Give MCP/RAG a real path to the 5 feature databases.** `scripts/deploy/aws/ai-db-nlb-setup.sh`
   (one-time, re-run after any database task redeploy) — see "Networking" above for why this exists.
3. **✅ Deploy the application code.** `scripts/deploy/aws/ai-ec2-deploy.sh` (repeatable) packages
   `ai-services/{mcp-server,rag-server,multi-agent-server}`, uploads them to the S3 bucket, and uses
   SSM Run Command (no SSH, no public inbound port) to install each service's `requirements.txt` into
   a shared venv, write systemd units for all three (env vars pointing `*_DB_URL` at the NLB from
   step 2), and restart them. Re-run this any time the code changes. **Verified live:** MCP has 13
   tools registered and successfully reads real budget-database rows through the NLB; RAG's corpus
   refresh populated 58 real chunks across all 5 features (`"ok"` for every `source_status` entry);
   Multi-Agent reports Ollama/MCP/RAG all reachable with 5 workflows loaded.
4. **Point the pilot feature at the instance.** Add the private IP recorded at
   `/voyageai/ai-host/private-ip` to `scripts/deploy/aws/services.yml` as `MCP_SERVICE_URL`/
   `RAG_SERVICE_URL`/`MULTI_AGENT_SERVICE_URL` for **one** feature backend only (Budget — it has the
   fullest existing MCP/RAG/Multi-Agent integration and the most test coverage), and flip its
   matching `_ENABLED` flags to `"true"`. Leave every other backend's AI flags `"false"` until the
   pilot is validated. **No change needed to `cloud-deployment.yml` itself** — it already loops over
   every entry in `services.yml` and redeploys whichever ones changed.
5. **Update the evidence contract.** Today, `ai-services/agentic_loop/collectors/cloud_collector.py`
   and `scripts/deploy/aws/smoke-test.sh` only assert AI routes report **disabled**. Once Budget's AI
   Mode is live, both need a corresponding "AI routes return a real, grounded answer" check for
   Budget specifically — mirroring how `testing-review` already validates MCP/RAG locally — while
   the remaining four backends keep their existing disabled-check.
6. **Treat it as a feature-flagged pilot, not a permanent always-on state.** Stop the EC2 instance
   (not terminate — an EBS-backed stopped instance costs nothing in compute, just pennies of EBS
   storage) outside the demo window, and flip Budget's AI flags back to `"false"` when it's not being
   actively demoed.

## Open questions for the team to resolve before starting

1. Whether `m7i-flex.large` CPU-only response times are acceptable for the showcase, or whether the
   `g4dn.xlarge` GPU upgrade (one-line change — see the Decision section above) is worth the extra
   cost before the demo.
2. Whether the showcase video needs AI Mode live in the cloud at all, or whether a recorded
   local-deployment demo (already fully working today, per the existing local setup) satisfies the
   rubric's distinction between "Local Deployment" (AI enabled) and "Cloud Baseline Deployment" (AI
   disabled by default) — if the rubric is satisfied by the local demo alone, this entire phase may
   not be necessary before submission.
