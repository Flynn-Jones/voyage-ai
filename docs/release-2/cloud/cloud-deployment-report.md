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

## Reconciliation (Release 2 verification; tested software SHA `e223429`)

Added by hand, above the generated Release Decision section so the smoke-test script can still regenerate this file.

- **The evidence in this report is stale.** It is smoke run `20261022T035641Z`, which predates `f934d5f` (activity seeding fix and rewritten `smoke-test.sh`). No `cloud-deployment` run or smoke run exists for `e223429`.
- **The cluster state is unverified.** The latest successful `cloud-deployment` runs were dispatched from the `ai-aws-integration` branch (run 40149950642, commit `04d299d`, 2026-10-25T01:15:53Z). That branch makes AI Mode intentionally live in the cloud (`767a1d4`). Whether the cluster currently matches the AI-disabled baseline in `scripts/deploy/aws/services.yml` has not been checked (no AWS access in this verification).
- **Conflicting decisions.** `agentic-loop/cloud-deployment-review-20261022T041944Z.md` records `approve-with-conditions` (decided by GiangPiano; condition: activity seeding re-verified via `scripts/deploy/aws/smoke-test.sh`). No recorded smoke run meets that condition. It is treated as superseded and unverified. The Release Decision below stays PENDING.
- **Current review on this evidence:** `agentic-loop/cloud-deployment-review-20261025T030332Z.md` is FAIL (activity CRUD, stale seed data), run with `--no-llm` and no decision (exit 2, decision `PENDING (required)`).
- **What the smoke test proves:** the 6 frontends answer through the ALB, seeded rows are readable on the 5 backends, and the AI routes report disabled. It does **not** prove create, update or delete in the cloud. A fresh AWS baseline deployment, tagged cloud CRUD with cleanup, screenshots or logs, and the release decision are human-authorised evidence still to be produced.

## Release Decision

Status: PENDING

(Record the human release decision here after reviewing
`ai-services/agentic_loop`'s `cloud-deployment-review` mode output.)
