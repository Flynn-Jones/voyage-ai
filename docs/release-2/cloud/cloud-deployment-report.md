# VoyageAI Cloud Deployment Report

## AWS ECS Fargate (voyageai-cluster)

Deployment Status: Success

ALB URL: http://voyageai-alb-615478022.ap-southeast-2.elb.amazonaws.com

Smoke test: `smoke-test-output-20261027T012150Z.txt`
Stack outputs: `stack-outputs-20261027T012150Z.json`

Timestamp: 20261027T012150Z

## AI services

As of Phase 2 (`docs/release-2/cloud/phase-2-ai-mode-plan.md`), MCP/RAG/Multi-Agent/Ollama run on
a dedicated EC2 "AI host" (not containerised, matching `ai-services/CLAUDE.md`'s existing shared-host
pattern) that all 5 feature backends are wired to. All four flags — `MCP_ENABLED`, `RAG_ENABLED`,
`MULTI_AGENT_ENABLED` and `AI_ENABLED` — are `"true"` for every one of destination-api,
accommodation-api, activity-api, budget-backend and itinerary-be; `shared-api` and all six
frontends keep `common_disabled_ai_env`'s `"false"`. The `ai-mode:*` checks above confirm each
backend returns a real, AI-host-generated response, not just that a flag is set.

## Known gaps at this run

- None: every check passed.

## Release Decision

Status: APPROVED by GiangPiano on 2026-10-27 (UTC)

Reviewed: `docs/release-2/agentic-loop/cloud-deployment-review-20261027T012501Z.md`
(+ `-validation-20261027T012501Z.jsonl`), recorded via
`app_main.py --mode cloud-deployment-review --decision approve --decided-by GiangPiano`.

Verdict **PASS** - 14/14 checks, 0 warn, 0 gap, 0 fail, no recommendations.

Approved on this evidence:

- All 18 ECS services on a single image tag `b76c50b`, matching `main`. Every rollout
  COMPLETED, desired == running, nothing unsettled.
- `smoke-test-output-20261027T012150Z.txt`: 16/16 PASS, 0 FAIL - 6 frontends reachable
  through the ALB, a seeded read on each of the 5 backends, and a real AI-host-generated
  response from each of the 5 AI paths.
- `crud-lifecycle-evidence-20261027T012434Z.json`: a complete tagged
  create -> read -> update -> delete on all 5 features against this ALB, every delete
  confirmed by a follow-up 404. The records it creates are removed again, so the
  deployment is left as it was found.
- AI Mode is deliberately LIVE, not disabled: all four flags are `"true"` for the five
  feature APIs against the EC2 AI host, `"false"` for `shared-api` and the frontends.
  The `ai_flags` check reports what is configured rather than asserting it is off.
- Multi-Agent Mode verified end to end through the activity frontend after the prompt and
  timeout fixes: HTTP 200 in 177s with Planner/Worker/Reviewer output and the run awaiting
  human review.

Known limitations accepted with this approval:

- Ollama is CPU-only on a single shared `m7i-flex.large` AI host. A Multi-Agent run takes
  around 2-3 minutes and the timeout ladder
  (`docs/release-2/cloud/ai-timeouts.md`) is sized for that, not for interactive latency.
- The AI host is a single point of failure for every AI path and is not auto-scaled or
  replicated. `services.yml` hardcodes its private IP, so replacing the instance requires
  updating the five feature APIs' `*_SERVICE_URL`/`OLLAMA_*` values.
- The `scripts/deploy/aws/tests` suite and the five student suites do not run on pull
  requests, only inside `cloud-deployment.yml`'s gate, so a breaking change shows green on
  a PR and fails at deploy time. This caused two blocked deploys during this release.

This deployment costs money while it runs. Tear it down with
`scripts/deploy/aws/teardown.sh --yes` once the release has been marked.

