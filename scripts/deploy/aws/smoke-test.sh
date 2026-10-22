#!/bin/bash
set -euo pipefail

# Validates the live voyageai-cluster ECS Fargate deployment and writes the evidence
# ai-services/agentic_loop's cloud-deployment-review mode reads:
#   docs/release-2/cloud/smoke-test-output-<UTC>.txt   (this script)
#   docs/release-2/cloud/stack-outputs-<UTC>.json       (this script)
#   docs/release-2/cloud/cloud-deployment-report.md     (this script)
#
# Mirrors the Lab 11 "deployment-testing.sh" pattern (validate -> auto-write the
# evidence files a release review reads), adapted from Azure Container Apps to the
# ECR + ECS Fargate + ALB architecture documented in docs/release-2/cloud/cloud-architecture.md.
#
# Run from the voyage-ai repo root, with AWS credentials for the voyageai-cluster account.

AWS_REGION="${AWS_REGION:-ap-southeast-2}"
CLUSTER="voyageai-cluster"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
CLOUD_DIR="$REPO_ROOT/docs/release-2/cloud"
mkdir -p "$CLOUD_DIR"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
SMOKE_FILE="$CLOUD_DIR/smoke-test-output-$TS.txt"
OUTPUTS_FILE="$CLOUD_DIR/stack-outputs-$TS.json"
REPORT_FILE="$CLOUD_DIR/cloud-deployment-report.md"

echo "=== VoyageAI AWS smoke test ==="

ALB_DNS=$(aws elbv2 describe-load-balancers --names voyageai-alb --region "$AWS_REGION" \
  --query 'LoadBalancers[0].DNSName' --output text)
ALB_ARN=$(aws elbv2 describe-load-balancers --names voyageai-alb --region "$AWS_REGION" \
  --query 'LoadBalancers[0].LoadBalancerArn' --output text)
echo "ALB: $ALB_DNS"

: > "$SMOKE_FILE"

check() {
  # check <line-prefix> <description-for-stdout>
  local line="$1"
  echo "$line" >> "$SMOKE_FILE"
  echo "$line"
}

# ---- frontends: every frontend answers through the ALB -------------------
# Portable (no associative arrays - macOS ships bash 3.2): "name:port" pairs.
for pair in shared:3000 destination:3001 accommodation:3002 activity:3003 budget:3004 itinerary:3005; do
  name="${pair%%:*}"
  port="${pair##*:}"
  code=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "http://$ALB_DNS:$port/" || echo "000")
  if [ "$code" = "200" ]; then
    check "PASS frontend:$name http_$code (port $port)"
  else
    check "FAIL frontend:$name http_$code (port $port)"
  fi
done

# ---- crud: one real read per backend, through its own frontend -----------
# Destination: table rows render known seed countries
body=$(curl -s -m 10 "http://$ALB_DNS:3001/")
if echo "$body" | grep -q "<td>Japan\|<td>Australia\|<td>France"; then
  check "PASS crud:destination-service destination table rows render seeded data"
else
  check "FAIL crud:destination-service no seeded destination rows found on /"
fi

# Accommodation: table rows render known seed destinations
body=$(curl -s -m 10 "http://$ALB_DNS:3002/")
if echo "$body" | grep -qE "<td>[A-Za-z]"; then
  check "PASS crud:accommodation-service accommodation table rows render seeded data"
else
  check "FAIL crud:accommodation-service no seeded accommodation rows found on /"
fi

# Activity: the Activities page (home) lists every activity in a table
body=$(curl -s -m 10 "http://$ALB_DNS:3003/")
if echo "$body" | grep -q 'id="activity-row-'; then
  check "PASS crud:activity-service activity list renders seeded data"
else
  check "FAIL crud:activity-service activity-database returned no rows (empty seed data in this task)"
fi

# Budget: real expense table
body=$(curl -s -m 10 "http://$ALB_DNS:3004/api/expenses")
if echo "$body" | grep -q 'data-expense-id'; then
  check "PASS crud:budget-service expense table renders seeded data"
else
  check "FAIL crud:budget-service no expense rows found on /api/expenses"
fi

# Itinerary: JSON read through nginx proxy
body=$(curl -s -m 10 "http://$ALB_DNS:3005/api/itinerary")
if echo "$body" | grep -q 'itinerary_item_id'; then
  check "PASS crud:itinerary-service itinerary read returns seeded rows"
else
  check "FAIL crud:itinerary-service no itinerary rows found on /api/itinerary"
fi

# ---- ai-disabled: every backend's AI/MCP path reports disabled -----------
# Destination: POST /mcp-lookup, disabled message rendered in the page body
body=$(curl -s -m 10 -X POST "http://$ALB_DNS:3001/mcp-lookup" -d "city=Tokyo")
if echo "$body" | grep -q "MCP lookup is disabled"; then
  check "PASS ai-disabled:destination-service MCP lookup reports disabled"
else
  check "FAIL ai-disabled:destination-service disabled message not found"
fi

# Accommodation: POST /mcp, propagates the backend's 403
code=$(curl -s -o /tmp/voyage-smoke-acc.html -m 10 -w '%{http_code}' -X POST "http://$ALB_DNS:3002/mcp" -d "text=hello")
if [ "$code" = "403" ] && grep -q "MCP mode is disabled" /tmp/voyage-smoke-acc.html; then
  check "PASS ai-disabled:accommodation-service http_403 MCP mode is disabled"
else
  check "FAIL ai-disabled:accommodation-service http_$code, disabled message not confirmed"
fi
rm -f /tmp/voyage-smoke-acc.html

# Activity: POST /mcp-mode/call, outer 200 but card reports the real backend status
body=$(curl -s -m 10 -X POST "http://$ALB_DNS:3003/mcp-mode/call" -d "kind=tool&tool=list_activities")
if echo "$body" | grep -q "MCP mode is disabled"; then
  check "PASS ai-disabled:activity-service MCP mode is disabled (surfaced via card)"
else
  check "FAIL ai-disabled:activity-service disabled message not found"
fi

# Budget: POST /api/budget/ai-analyse, direct JSON 403
code=$(curl -s -o /tmp/voyage-smoke-budget.json -m 10 -w '%{http_code}' -X POST "http://$ALB_DNS:3004/api/budget/ai-analyse" -H 'Content-Type: application/json' -d '{}')
if [ "$code" = "403" ] && grep -q "AI-Mode disabled" /tmp/voyage-smoke-budget.json; then
  check "PASS ai-disabled:budget-service http_403 AI-Mode disabled"
else
  check "FAIL ai-disabled:budget-service http_$code, disabled message not confirmed"
fi
rm -f /tmp/voyage-smoke-budget.json

# Itinerary: POST /api/itinerary/ai-review with a valid prompt - no AI_ENABLED gate exists in
# this route (see docs/release-2/cloud/cloud-deployment-report.md note below); it fails closed
# with a 502 because the cloud task cannot reach the host-only Ollama, which is the required
# net effect even though the mechanism differs from the other four services' explicit 403 checks.
code=$(curl -s -o /tmp/voyage-smoke-itin.json -m 20 -w '%{http_code}' -X POST "http://$ALB_DNS:3005/api/itinerary/ai-review" -H 'Content-Type: application/json' -d '{"prompt":"Review day 1","day":1}')
if [ "$code" = "502" ] && grep -q "AI review service is unavailable" /tmp/voyage-smoke-itin.json; then
  check "PASS ai-disabled:itinerary-service http_502 AI review service is unavailable (no AI_ENABLED gate; fails closed on unreachable Ollama)"
else
  check "FAIL ai-disabled:itinerary-service http_$code, expected unavailable response not confirmed"
fi
rm -f /tmp/voyage-smoke-itin.json

echo "=== Smoke test complete: $SMOKE_FILE ==="
cat "$SMOKE_FILE"

# ---- stack outputs ---------------------------------------------------------
SERVICE_ARNS_JSON=$(aws ecs list-services --cluster "$CLUSTER" --region "$AWS_REGION" --query 'serviceArns' --output json)
cat > "$OUTPUTS_FILE" <<EOF
{
  "alb_url": "http://$ALB_DNS",
  "alb_arn": "$ALB_ARN",
  "cluster_arn": "arn:aws:ecs:$AWS_REGION:$(aws sts get-caller-identity --query Account --output text):cluster/$CLUSTER",
  "service_arns": $SERVICE_ARNS_JSON,
  "generated": "$TS"
}
EOF
echo "=== Stack outputs written: $OUTPUTS_FILE ==="

# ---- cloud deployment report ----------------------------------------------
FAIL_COUNT=$(grep -c '^FAIL' "$SMOKE_FILE" || true)
if [ "$FAIL_COUNT" -eq 0 ]; then
  STATUS="Success"
else
  STATUS="Partial ($FAIL_COUNT check(s) failing)"
fi

cat > "$REPORT_FILE" <<EOF
# VoyageAI Cloud Deployment Report

## AWS ECS Fargate (voyageai-cluster)

Deployment Status: $STATUS

ALB URL: http://$ALB_DNS

Smoke test: \`$(basename "$SMOKE_FILE")\`
Stack outputs: \`$(basename "$OUTPUTS_FILE")\`

Timestamp: $TS

## AI services

MCP/RAG/Multi-Agent/Ollama are not deployed to AWS — they remain host-only per
\`ai-services/CLAUDE.md\`. Every feature backend's \`AI_ENABLED\`/\`MCP_ENABLED\`/\`RAG_ENABLED\`/
\`MULTI_AGENT_ENABLED\` flags are forced \`false\` in \`scripts/deploy/aws/services.yml\`.

One asymmetry found during this smoke test: \`itinerary-be\`'s \`/api/itinerary/ai-review\` route
has no explicit \`AI_ENABLED\` gate (unlike the other four services' deliberate 403 checks) — it
fails closed with a 502 because the cloud task cannot reach the host-only Ollama. Net effect is
the same (AI does not run in the cloud) but the mechanism is incidental, not a deliberate check.

## Known gaps at this run

$( [ "$FAIL_COUNT" -gt 0 ] && grep '^FAIL' "$SMOKE_FILE" | sed 's/^/- /' || echo "- None: every check passed." )

## Release Decision

Status: PENDING

(Record the human release decision here after reviewing
\`ai-services/agentic_loop\`'s \`cloud-deployment-review\` mode output.)
EOF

echo "=== Cloud deployment report written: $REPORT_FILE ==="
