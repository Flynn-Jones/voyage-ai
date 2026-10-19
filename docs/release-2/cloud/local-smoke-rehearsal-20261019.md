# Local smoke rehearsal (NOT cloud evidence)

Before any AWS account existed, the in-VPC smoke runner was rehearsed against the full app on Docker:

```bash
docker compose -p voyageai build          # all 18 images, exactly the names deploy.sh pushes
DESTINATION_MCP_ENABLED=false DESTINATION_RAG_ENABLED=false MCP_ENABLED=false RAG_ENABLED=false \
ACTIVITY_MCP_ENABLED=false ACTIVITY_RAG_ENABLED=false ITINERARY_MCP_ENABLED=false ITINERARY_RAG_ENABLED=false \
  docker compose -p voyageai up -d
python scripts/deploy/aws/smoke-runner/smoke_runner.py --local
```

Date: 2026-10-19 (UTC), branch `shared/aws-deployment`. Local Ollama **was** reachable, which in the
cloud it is not. The cloud file `smoke-test-output-<UTC>.txt` is written only by `smoke-test.sh` after a
real deployment.

## Result: 16 pass, 5 fail

| Check | Result | Notes |
|---|---|---|
| `frontend` ×6 (:3000–:3005) | PASS (HTTP 200) | via curl on the host ports |
| `crud` ×6 | **PASS** | shared is read-only (`/users`, `/trips`, `/nav` 200); destination, accommodation, activity, budget and itinerary each pass create/read/update/delete, then read-after-delete returns 404 |
| `ai-disabled` MCP + RAG ×10 | **PASS** | destination 503 `"disabled"`; the others 403 `"... is disabled"` |
| `ai-disabled` AI-Mode ×5 | **FAIL (expected)** | No backend reads `AI_ENABLED`. Destination `ai-compare` returned a real Ollama comparison (200), accommodation `/ai/recommend` 200, budget `ai-analyse` 200, itinerary `ai-review` 200, and activity `ai-summary` ran without a disabled check. **This is student change #3 in [student-changes.md](student-changes.md).** |

Runner fixes made from this rehearsal: itinerary returns its id as `itinerary_item_id`. The activity
`ai-summary` check now sends `activity_id`, and the itinerary `ai-review` check sends `prompt` + `day`, so
both reach the AI code instead of failing input validation.
