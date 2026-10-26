# Cloud Deployment Review

- Generated: 20261022T122729Z (UTC) by `ai-services/agentic_loop` mode `cloud-deployment-review`
- **Verdict: PASS** (pass 9, warn 0, gap 0, fail 0)

## Human release decision (required)

- **Decision: PENDING (required)**
- Decided by: -
- Conditions: -
- Recorded: 20261022T122729Z

## Evidence consulted

- `docs/release-2/cloud/cloud-deployment-report.md`
- `docs/release-2/cloud/smoke-test-output-20261022T122515Z.txt`
- `docs/release-2/cloud/stack-outputs-20261022T035641Z.json`
- `docs/release-2/cloud/stack-outputs-20261022T122515Z.json`
- `scripts/deploy/aws/services.yml`

## Checks

| Status | Subject | Check | Detail |
|---|---|---|---|
| PASS | cloud | `deployment_report` | cloud-deployment-report.md present |
| PASS | smoke | `frontend` | 6 passing: shared, destination, accommodation, activity, budget, itinerary |
| PASS | smoke | `crud` | 5 passing: destination-service, accommodation-service, activity-service, budget-service, itinerary-service |
| PASS | smoke | `ai-mode` | 5 passing: destination-service, accommodation-service, activity-service, budget-service, itinerary-service |
| PASS | cloud | `alb_url` | ALB URL recorded: http://voyageai-alb-615478022.ap-southeast-2.elb.amazonaws.com |
| PASS | MCP_ENABLED | `ai_flags` | configured: {'false': 1, 'true': 5} |
| PASS | RAG_ENABLED | `ai_flags` | configured: {'false': 1, 'true': 5} |
| PASS | MULTI_AGENT_ENABLED | `ai_flags` | configured: {'false': 1, 'true': 4} |
| PASS | AI_ENABLED | `ai_flags` | configured: {'false': 1, 'true': 3} |

## Metrics

```json
{
  "smoke": {
    "frontend": 6,
    "crud": 5,
    "ai-mode": 5
  },
  "alb_url": "http://voyageai-alb-615478022.ap-southeast-2.elb.amazonaws.com"
}
```

## Recommendations

- None: every check passed.
