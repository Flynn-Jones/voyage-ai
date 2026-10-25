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
#
# Exit status: 0 only when every check PASSes; 1 when any check FAILs. A check passes
# only if curl exited 0 AND the HTTP status matched AND the body matched; AWS lookup
# failures are FAIL lines too. The three evidence files are always written first
# (unknown stack values are JSON null), so a failing run still leaves them behind.
# The timestamped files are never overwritten: if the name exists, a _NN suffix is used
# (underscore, so the agentic_loop reader's sorted(...)[-1] still picks the newest file).
#
# cloud-deployment-report.md is the file humans edit (the Release Decision), so a rerun
# never erases one: it is regenerated in place only while its "## Release Decision"
# section is still exactly the generated placeholder (or the file is absent). Otherwise
# it is left untouched and this run's report goes to cloud-deployment-report-<run>.md for
# the human to review. The fixed-name file keeps existing either way, which is all the
# reader checks. (A decided report describes the run it was decided on, by its own
# "Smoke test:" line; it is not re-stamped with newer results.)
#
# The JSON checks (list-services output, the itinerary error body) use python3 and fail
# closed if it is missing. SMOKE_CLOUD_DIR overrides the evidence directory
# (default: docs/release-2/cloud).

AWS_REGION="${AWS_REGION:-ap-southeast-2}"
CLUSTER="voyageai-cluster"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
CLOUD_DIR="${SMOKE_CLOUD_DIR:-$REPO_ROOT/docs/release-2/cloud}"
mkdir -p "$CLOUD_DIR"

# Per-run scratch space for response bodies (unique, honours TMPDIR, removed on exit).
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/voyage-smoke.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

TS="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$CLOUD_DIR/cloud-deployment-report.md"

# The generated Release Decision placeholder. A report whose section differs from this
# has had a human decision (or any other edit) recorded in it.
IFS= read -r -d '' RELEASE_DECISION_TEMPLATE <<'TEMPLATE_EOF' || true
## Release Decision

Status: PENDING

(Record the human release decision here after reviewing
`ai-services/agentic_loop`'s `cloud-deployment-review` mode output.)
TEMPLATE_EOF

# Claim this run's evidence names without clobbering an earlier run (same-second
# reruns included): the smoke file is created atomically (noclobber) or we bump a suffix.
RUN_ID="$TS"
n=0
while true; do
  SMOKE_FILE="$CLOUD_DIR/smoke-test-output-$RUN_ID.txt"
  OUTPUTS_FILE="$CLOUD_DIR/stack-outputs-$RUN_ID.json"
  if [ ! -e "$OUTPUTS_FILE" ] && ( set -C; : > "$SMOKE_FILE" ) 2>/dev/null; then
    break
  fi
  n=$((n + 1))
  RUN_ID="${TS}_$(printf '%02d' "$n")"
done

echo "=== VoyageAI AWS smoke test ==="

check() {
  # check <line-prefix> <description-for-stdout>
  local line="$1"
  echo "$line" >> "$SMOKE_FILE"
  echo "$line"
}

# aws_value <label> <aws command...>: sets AWS_VALUE. A nonzero aws exit or an empty/None
# answer records a FAIL line and returns 1, so the script still writes its evidence.
AWS_VALUE=""
aws_value() {
  local label="$1" rc=0
  shift
  AWS_VALUE="$("$@")" || rc=$?
  if [ "$rc" -ne 0 ]; then
    AWS_VALUE=""
    check "FAIL aws:$label aws exit_$rc"
    return 1
  fi
  if [ -z "$AWS_VALUE" ] || [ "$AWS_VALUE" = "None" ]; then
    AWS_VALUE=""
    check "FAIL aws:$label returned no value"
    return 1
  fi
}

ALB_DNS=""
ALB_ARN=""
CLUSTER_ARN=""
SERVICE_ARNS_JSON=""

if aws_value alb-dns aws elbv2 describe-load-balancers --names voyageai-alb --region "$AWS_REGION" \
  --query 'LoadBalancers[0].DNSName' --output text; then
  ALB_DNS="$AWS_VALUE"
fi
if aws_value alb-arn aws elbv2 describe-load-balancers --names voyageai-alb --region "$AWS_REGION" \
  --query 'LoadBalancers[0].LoadBalancerArn' --output text; then
  ALB_ARN="$AWS_VALUE"
fi
echo "ALB: ${ALB_DNS:-unresolved}"

# JSON checks. python3 is parsed with -I; if it is missing these are false (fail closed).
# json_is_string_array <text>: a JSON array of strings (what `aws ecs list-services` returns).
json_is_string_array() {
  python3 -I -c 'import json, sys
d = json.load(sys.stdin)
sys.exit(0 if isinstance(d, list) and all(isinstance(x, str) for x in d) else 1)' <<< "$1" > /dev/null 2>&1
}
# body_error_is <message>: the last response body is a JSON object whose "error" is exactly <message>.
body_error_is() {
  python3 -I -c 'import json, sys
d = json.load(open(sys.argv[1]))
sys.exit(0 if isinstance(d, dict) and d.get("error") == sys.argv[2] else 1)' "$TMP_DIR/body" "$1" > /dev/null 2>&1
}

# http_expect <status> <grep -E pattern or ""> <curl args...>
# Succeeds only when curl exited 0, the HTTP status equals <status>, and the body matches
# the pattern. A matching body behind the wrong status, or from a transfer that failed
# part-way (timeout, reset), fails. Sets WHY ("http_503", "http_200 curl_exit_18", ...)
# for the FAIL line. Without an ALB address there is nothing to call.
WHY=""
http_expect() {
  local want="$1" pattern="$2" rc=0 code body="$TMP_DIR/body"
  shift 2
  rm -f "$body"
  WHY="http_000"
  if [ -z "$ALB_DNS" ]; then
    WHY="http_000 alb_unresolved"
    return 1
  fi
  code=$(curl -s -o "$body" -w '%{http_code}' "$@") || rc=$?
  [ -n "$code" ] || code="000"
  [ -f "$body" ] || : > "$body"
  WHY="http_$code"
  if [ "$rc" -ne 0 ]; then
    WHY="http_$code curl_exit_$rc"
    return 1
  fi
  [ "$code" = "$want" ] || return 1
  if [ -n "$pattern" ] && ! grep -qE "$pattern" "$body"; then
    WHY="http_$code body_mismatch"
    return 1
  fi
}

# ---- frontends: every frontend answers through the ALB -------------------
# Portable (no associative arrays - macOS ships bash 3.2): "name:port" pairs.
for pair in shared:3000 destination:3001 accommodation:3002 activity:3003 budget:3004 itinerary:3005; do
  name="${pair%%:*}"
  port="${pair##*:}"
  if http_expect 200 "" -m 10 "http://$ALB_DNS:$port/"; then
    check "PASS frontend:$name http_200 (port $port)"
  else
    check "FAIL frontend:$name $WHY (port $port)"
  fi
done

# ---- read: one seeded GET per backend, through its own frontend ----------
# These prove the seeded data can be READ. They are not CRUD evidence: a complete tagged
# create/read/update/delete lifecycle is recorded separately in
# docs/release-2/cloud/crud-lifecycle-evidence-<UTC>.json (see cloud_collector.py).
# Destination: table rows render known seed countries
if http_expect 200 "<td>(Japan|Australia|France)" -m 10 "http://$ALB_DNS:3001/"; then
  check "PASS read:destination-service destination table rows render seeded data"
else
  check "FAIL read:destination-service $WHY, no seeded destination rows found on /"
fi

# Accommodation: table rows render known seed destinations
if http_expect 200 "<td>[A-Za-z]" -m 10 "http://$ALB_DNS:3002/"; then
  check "PASS read:accommodation-service accommodation table rows render seeded data"
else
  check "FAIL read:accommodation-service $WHY, no seeded accommodation rows found on /"
fi

# Activity: the Activities page (home) lists every activity in a table
if http_expect 200 'id="activity-row-' -m 10 "http://$ALB_DNS:3003/"; then
  check "PASS read:activity-service activity list renders seeded data"
else
  check "FAIL read:activity-service $WHY, activity-database returned no rows"
fi

# Budget: real expense table
if http_expect 200 "data-expense-id" -m 10 "http://$ALB_DNS:3004/api/expenses"; then
  check "PASS read:budget-service expense table renders seeded data"
else
  check "FAIL read:budget-service $WHY, no expense rows found on /api/expenses"
fi

# Itinerary: JSON read through nginx proxy
if http_expect 200 "itinerary_item_id" -m 10 "http://$ALB_DNS:3005/api/itinerary"; then
  check "PASS read:itinerary-service itinerary read returns seeded rows"
else
  check "FAIL read:itinerary-service $WHY, no itinerary rows found on /api/itinerary"
fi

# ---- ai-disabled: every backend's AI/MCP path reports disabled -----------
# Destination: POST /mcp-lookup, disabled message rendered in the (200) page body
if http_expect 200 "MCP lookup is disabled" -m 10 -X POST "http://$ALB_DNS:3001/mcp-lookup" -d "city=Tokyo"; then
  check "PASS ai-disabled:destination-service MCP lookup reports disabled"
else
  check "FAIL ai-disabled:destination-service $WHY, disabled message not found"
fi

# Accommodation: POST /mcp, propagates the backend's 403
if http_expect 403 "MCP mode is disabled" -m 10 -X POST "http://$ALB_DNS:3002/mcp" -d "text=hello"; then
  check "PASS ai-disabled:accommodation-service http_403 MCP mode is disabled"
else
  check "FAIL ai-disabled:accommodation-service $WHY, disabled message not confirmed"
fi

# Activity: POST /mcp-mode/call, outer 200 but card reports the real backend status
if http_expect 200 "MCP mode is disabled" -m 10 -X POST "http://$ALB_DNS:3003/mcp-mode/call" -d "kind=tool&tool=list_activities"; then
  check "PASS ai-disabled:activity-service MCP mode is disabled (surfaced via card)"
else
  check "FAIL ai-disabled:activity-service $WHY, disabled message not found"
fi

# Budget: POST /api/budget/ai-analyse, direct JSON 403
if http_expect 403 "AI-Mode disabled" -m 10 -X POST "http://$ALB_DNS:3004/api/budget/ai-analyse" -H 'Content-Type: application/json' -d '{}'; then
  check "PASS ai-disabled:budget-service http_403 AI-Mode disabled"
else
  check "FAIL ai-disabled:budget-service $WHY, disabled message not confirmed"
fi

# Itinerary: POST /api/itinerary/ai-review with a valid prompt - the route's AI_ENABLED gate
# (forced false in the cloud) returns an explicit 403 {"error":"AI Mode is disabled"}, like budget.
itinerary_ok=0
if http_expect 403 "" -m 20 -X POST "http://$ALB_DNS:3005/api/itinerary/ai-review" -H 'Content-Type: application/json' -d '{"prompt":"Review day 1","day":1}'; then
  if body_error_is "AI Mode is disabled"; then
    itinerary_ok=1
  else
    WHY="http_403 body_not_expected_json"
  fi
fi
if [ "$itinerary_ok" = 1 ]; then
  check "PASS ai-disabled:itinerary-service http_403 AI Mode is disabled"
else
  check "FAIL ai-disabled:itinerary-service $WHY, disabled message not confirmed"
fi

# ---- stack lookups (failures become FAIL lines; the values stay null in the JSON) ----
if aws_value stack-service-arns aws ecs list-services --cluster "$CLUSTER" --region "$AWS_REGION" --query 'serviceArns' --output json; then
  if json_is_string_array "$AWS_VALUE"; then
    SERVICE_ARNS_JSON="$AWS_VALUE"
  else
    check "FAIL aws:stack-service-arns output is not a JSON array of strings"
  fi
fi
if aws_value stack-account aws sts get-caller-identity --query Account --output text; then
  case "$AWS_VALUE" in
    *[!0-9]*) check "FAIL aws:stack-account returned a non-numeric account id" ;;
    *) CLUSTER_ARN="arn:aws:ecs:$AWS_REGION:$AWS_VALUE:cluster/$CLUSTER" ;;
  esac
fi

echo "=== Smoke test complete: $SMOKE_FILE ==="
cat "$SMOKE_FILE"

# ---- stack outputs ---------------------------------------------------------
json_str() {
  if [ -n "$1" ]; then printf '"%s"' "$1"; else printf 'null'; fi
}
cat > "$OUTPUTS_FILE" <<EOF
{
  "alb_url": $(json_str "${ALB_DNS:+http://$ALB_DNS}"),
  "alb_arn": $(json_str "$ALB_ARN"),
  "cluster_arn": $(json_str "$CLUSTER_ARN"),
  "service_arns": ${SERVICE_ARNS_JSON:-null},
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

report_is_decided() {
  # True when the fixed-name report exists and its Release Decision is not the placeholder
  # (a missing section counts: a hand-written report must not be replaced either).
  [ -f "$REPORT_FILE" ] || return 1
  local nl=$'\n'
  [ "$(sed -n '/^## Release Decision$/,$p' "$REPORT_FILE")" != "${RELEASE_DECISION_TEMPLATE%"$nl"}" ]
}
if report_is_decided; then
  echo "=== $(basename "$REPORT_FILE") records a Release Decision: left untouched ==="
  base="$CLOUD_DIR/cloud-deployment-report-$RUN_ID"
  REPORT_FILE="$base.md"
  n=0
  while [ -e "$REPORT_FILE" ]; do
    n=$((n + 1))
    REPORT_FILE="${base}_$(printf '%02d' "$n").md"
  done
fi

cat > "$REPORT_FILE" <<EOF
# VoyageAI Cloud Deployment Report

## AWS ECS Fargate (voyageai-cluster)

Deployment Status: $STATUS

ALB URL: ${ALB_DNS:+http://}${ALB_DNS:-unresolved}

Smoke test: \`$(basename "$SMOKE_FILE")\`
Stack outputs: \`$(basename "$OUTPUTS_FILE")\`

Timestamp: $TS

## AI services

MCP/RAG/Multi-Agent/Ollama are not deployed to AWS — they remain host-only per
\`ai-services/CLAUDE.md\`. Every feature backend's \`AI_ENABLED\`/\`MCP_ENABLED\`/\`RAG_ENABLED\`/
\`MULTI_AGENT_ENABLED\` flags are forced \`false\` in \`scripts/deploy/aws/services.yml\`.

\`itinerary-be\`'s \`/api/itinerary/ai-review\` route is gated by \`AI_ENABLED\` and answers with an
explicit 403 "AI Mode is disabled", the same deliberate check as the other services.

## Known gaps at this run

$( [ "$FAIL_COUNT" -gt 0 ] && grep '^FAIL' "$SMOKE_FILE" | sed 's/^/- /' || echo "- None: every check passed." )

$RELEASE_DECISION_TEMPLATE
EOF

echo "=== Cloud deployment report written: $REPORT_FILE ==="

# A failing required check must fail the run - after the evidence above is written.
if [ "$FAIL_COUNT" -gt 0 ]; then
  echo "=== Smoke test FAILED: $FAIL_COUNT required check(s) failing ===" >&2
  exit 1
fi
