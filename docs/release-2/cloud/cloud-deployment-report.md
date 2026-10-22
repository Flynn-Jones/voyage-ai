# VoyageAI Cloud Deployment Report

## AWS ECS Fargate (voyageai-cluster)

Deployment Status: Partial (1 check(s) failing)

ALB URL: http://voyageai-alb-615478022.ap-southeast-2.elb.amazonaws.com

Smoke test: `smoke-test-output-20261022T035641Z.txt`
Stack outputs: `stack-outputs-20261022T035641Z.json`

Timestamp: 20261022T035641Z

## AI services

MCP/RAG/Multi-Agent/Ollama are not deployed to AWS — they remain host-only per
`ai-services/CLAUDE.md`. Every feature backend's `AI_ENABLED`/`MCP_ENABLED`/`RAG_ENABLED`/
`MULTI_AGENT_ENABLED` flags are forced `false` in `scripts/deploy/aws/services.yml`.

One asymmetry found during this smoke test: `itinerary-be`'s `/api/itinerary/ai-review` route
has no explicit `AI_ENABLED` gate (unlike the other four services' deliberate 403 checks) — it
fails closed with a 502 because the cloud task cannot reach the host-only Ollama. Net effect is
the same (AI does not run in the cloud) but the mechanism is incidental, not a deliberate check.

## Known gaps at this run

- FAIL crud:activity-service activity-database returned no rows (empty seed data in this task)

## Release Decision

Status: PENDING

(Record the human release decision here after reviewing
`ai-services/agentic_loop`'s `cloud-deployment-review` mode output.)
