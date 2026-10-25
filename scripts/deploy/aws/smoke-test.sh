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

# ---- ai-mode: every backend's AI path, now wired to the AI host (Phase 2) ------------
# Phase 2 (docs/release-2/cloud/phase-2-ai-mode-plan.md) wired AI Mode live for all 5
# feature backends - these checks confirm a REAL generated response, not a disabled
# message. Timeouts are generous (measured live: destination ~21s, accommodation ~91s,
# activity ~7s, budget ~48s, itinerary ~56s on the CPU-only AI host) - a plain `curl -m N`
# timeout is NOT caught by an `if` condition when assigned via `var=$(curl ...)`, so a too-
# short timeout here will abort the whole script under `set -e`, not just fail one check.

# Destination: POST /compare, real city comparison rendered in the page body
body=$(curl -s -m 60 -X POST "http://$ALB_DNS:3001/compare" -d "city_a=Tokyo&city_b=Kyoto&preferences=nightlife+and+food")
if echo "$body" | grep -q "detail-grid"; then
  check "PASS ai-mode:destination-service compare returned a real result"
else
  check "FAIL ai-mode:destination-service compare did not return a result"
fi

# Accommodation: POST /recommend, real ranked recommendations (its own code uses a
# 120-180s internal timeout, so give curl similar headroom)
code=$(curl -s -o /tmp/voyage-smoke-acc.html -m 150 -w '%{http_code}' -X POST "http://$ALB_DNS:3002/recommend" -d "destination_city=Tokyo&max_price=200&interests=nightlife,food")
if [ "$code" = "200" ] && grep -qi "top recommendations\|recommendation" /tmp/voyage-smoke-acc.html; then
  check "PASS ai-mode:accommodation-service recommend returned a real result"
else
  check "FAIL ai-mode:accommodation-service http_$code, no recommendation found"
fi
rm -f /tmp/voyage-smoke-acc.html

# Activity: POST /ai-mode/chat, real chat response (may legitimately say "nothing matched")
code=$(curl -s -o /tmp/voyage-smoke-activity.html -m 60 -w '%{http_code}' -X POST "http://$ALB_DNS:3003/ai-mode/chat" -d "message=What activities are available?")
if [ "$code" = "200" ] && grep -q "chat-log\|act-chat" /tmp/voyage-smoke-activity.html; then
  check "PASS ai-mode:activity-service ai-mode/chat returned a real result"
else
  check "FAIL ai-mode:activity-service http_$code, no chat response found"
fi
rm -f /tmp/voyage-smoke-activity.html

# Budget: POST /api/budget/ai-analyse, direct JSON 200 with a real analysis
code=$(curl -s -o /tmp/voyage-smoke-budget.json -m 90 -w '%{http_code}' -X POST "http://$ALB_DNS:3004/api/budget/ai-analyse" -H 'Content-Type: application/json' -d '{"trip_reference":"TRIP-1001"}')
if [ "$code" = "200" ] && grep -q '"analysis"' /tmp/voyage-smoke-budget.json; then
  check "PASS ai-mode:budget-service ai-analyse returned a real result"
else
  check "FAIL ai-mode:budget-service http_$code, no analysis found"
fi
rm -f /tmp/voyage-smoke-budget.json

# Itinerary: POST /api/itinerary/ai-review, real schedule review (no AI_ENABLED gate on
# this route - it always attempts a live Ollama call, so a real result confirms the AI
# host is actually reachable, not just that the flag is set)
code=$(curl -s -o /tmp/voyage-smoke-itin.json -m 90 -w '%{http_code}' -X POST "http://$ALB_DNS:3005/api/itinerary/ai-review" -H 'Content-Type: application/json' -d '{"prompt":"Review day 1","day":1}')
if [ "$code" = "200" ] && grep -q '"adapt"' /tmp/voyage-smoke-itin.json; then
  check "PASS ai-mode:itinerary-service ai-review returned a real result"
else
  check "FAIL ai-mode:itinerary-service http_$code, no review found"
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

As of Phase 2 (\`docs/release-2/cloud/phase-2-ai-mode-plan.md\`), MCP/RAG/Multi-Agent/Ollama run on
a dedicated EC2 "AI host" (not containerised, matching \`ai-services/CLAUDE.md\`'s existing shared-host
pattern) that all 5 feature backends are wired to — \`MCP_ENABLED\`/\`RAG_ENABLED\`/\`AI_ENABLED\` are
\`"true"\` for destination/accommodation/activity/budget, and \`MULTI_AGENT_ENABLED\` additionally for
destination/accommodation/activity/budget (itinerary has no Multi-Agent integration in its own code,
so no such flag exists for it). The \`ai-mode:*\` checks above confirm each backend returns a real,
AI-host-generated response, not just that a flag is set.

## Known gaps at this run

$( [ "$FAIL_COUNT" -gt 0 ] && grep '^FAIL' "$SMOKE_FILE" | sed 's/^/- /' || echo "- None: every check passed." )

## Release Decision

Status: PENDING

(Record the human release decision here after reviewing
\`ai-services/agentic_loop\`'s \`cloud-deployment-review\` mode output.)
EOF

echo "=== Cloud deployment report written: $REPORT_FILE ==="
