# Cloud Deployment Review

- Generated: 20261027T012501Z (UTC) by `ai-services/agentic_loop` mode `cloud-deployment-review`
- **Verdict: PASS** (pass 14, warn 0, gap 0, fail 0)

## Human release decision (required)

- **Decision: approve**
- Decided by: GiangPiano
- Conditions: -
- Recorded: 20261027T012501Z

## Evidence consulted

- `docs/release-2/cloud/cloud-deployment-report.md`
- `docs/release-2/cloud/smoke-test-output-20261027T012150Z.txt`
- `docs/release-2/cloud/stack-outputs-20261027T012150Z.json`
- `docs/release-2/cloud/crud-lifecycle-evidence-20261027T012434Z.json`
- `scripts/deploy/aws/services.yml`

## Checks

| Status | Subject | Check | Detail |
|---|---|---|---|
| PASS | cloud | `deployment_report` | cloud-deployment-report.md present |
| PASS | smoke | `frontend` | 6 passing: shared, destination, accommodation, activity, budget, itinerary |
| PASS | smoke | `read` | 5 passing: destination-service, accommodation-service, activity-service, budget-service, itinerary-service |
| PASS | smoke | `ai-mode` | 5 passing: destination-service, accommodation-service, activity-service, budget-service, itinerary-service |
| PASS | cloud | `alb_url` | ALB URL recorded: http://voyageai-alb-615478022.ap-southeast-2.elb.amazonaws.com (stack-outputs-20261027T012150Z.json) |
| PASS | destination-service | `crud_lifecycle` | tagged lifecycle create/read/update/delete passed (tag r2-crud-20261027T012431Z) |
| PASS | accommodation-service | `crud_lifecycle` | tagged lifecycle create/read/update/delete passed (tag r2-crud-20261027T012431Z) |
| PASS | activity-service | `crud_lifecycle` | tagged lifecycle create/read/update/delete passed (tag r2-crud-20261027T012431Z) |
| PASS | budget-service | `crud_lifecycle` | tagged lifecycle create/read/update/delete passed (tag r2-crud-20261027T012431Z) |
| PASS | itinerary-service | `crud_lifecycle` | tagged lifecycle create/read/update/delete passed (tag r2-crud-20261027T012431Z) |
| PASS | MCP_ENABLED | `ai_flags` | configured: {'false': 1, 'true': 5} |
| PASS | RAG_ENABLED | `ai_flags` | configured: {'false': 1, 'true': 5} |
| PASS | MULTI_AGENT_ENABLED | `ai_flags` | configured: {'false': 1, 'true': 5} |
| PASS | AI_ENABLED | `ai_flags` | configured: {'false': 1, 'true': 5} |

## Metrics

```json
{
  "smoke": {
    "frontend": 6,
    "read": 5,
    "ai-mode": 5
  },
  "alb_url": "http://voyageai-alb-615478022.ap-southeast-2.elb.amazonaws.com",
  "crud_lifecycle": {
    "destination-service": "pass",
    "accommodation-service": "pass",
    "activity-service": "pass",
    "budget-service": "pass",
    "itinerary-service": "pass"
  }
}
```

## Recommendations

- None: every check passed.

## LLM commentary (advisory; never changes the verdict)

**Implementation recommendations:** Findings: All checks PASS.
Implementation recommendations: Ensure ALB is correctly routing traffic. Verify AI services are configured for direct calls.

**Review recommendations:** Advice: approve
Reasons: All checks PASS, AI services configured correctly.
Conditions: N/A
