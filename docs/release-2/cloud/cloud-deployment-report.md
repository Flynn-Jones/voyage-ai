# VoyageAI Cloud Deployment Report

## AWS ECS Fargate (voyageai-cluster)

Deployment Status: Success

ALB URL: http://voyageai-alb-615478022.ap-southeast-2.elb.amazonaws.com

Smoke test: `smoke-test-output-20261026T121038Z.txt`
Stack outputs: `stack-outputs-20261026T121038Z.json`

Timestamp: 20261026T121038Z

## AI services

As of Phase 2 (`docs/release-2/cloud/phase-2-ai-mode-plan.md`), MCP/RAG/Multi-Agent/Ollama run on
a dedicated EC2 "AI host" (not containerised, matching `ai-services/CLAUDE.md`'s existing shared-host
pattern) that all 5 feature backends are wired to — `MCP_ENABLED`/`RAG_ENABLED`/`AI_ENABLED` are
`"true"` for destination/accommodation/activity/budget, and `MULTI_AGENT_ENABLED` additionally for
destination/accommodation/activity/budget (itinerary has no Multi-Agent integration in its own code,
so no such flag exists for it). The `ai-mode:*` checks above confirm each backend returns a real,
AI-host-generated response, not just that a flag is set.

## Known gaps at this run

- None: every check passed.

## Release Decision

Status: PENDING

(Record the human release decision here after reviewing
`ai-services/agentic_loop`'s `cloud-deployment-review` mode output.)

