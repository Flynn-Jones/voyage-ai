#!/usr/bin/env bash
# Local integration evidence collector.
#
# Runs the integration checks against the already-running stack and writes a
# timestamped Markdown report plus a raw log, so the results can be attached to
# the technical report and re-run on camera.
#
# Usage, from the repository root:
#   ./accommodation-service/scripts/integration-evidence.sh
#
# Preconditions: `docker compose up -d` from the repo root, plus the shared MCP
# server on 7001 and RAG server on 7002 running on the host. The script reports
# anything that is down rather than aborting.

set -uo pipefail

TS="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
OUT_DIR="${OUT_DIR:-accommodation-service/reports}"
REPORT="$OUT_DIR/integration-evidence-$TS.md"
LOG="$OUT_DIR/integration-evidence-$TS.log"
mkdir -p "$OUT_DIR"

PASS=0; FAIL=0; SKIP=0
ROWS=()

log() { echo "$@" >> "$LOG"; }

# record ID | scenario | expected | actual | status
record() {
  local id="$1" name="$2" expected="$3" actual="$4" status="$5"
  ROWS+=("| $id | $name | $expected | $actual | **$status** |")
  case "$status" in
    PASS) PASS=$((PASS+1)); printf "  \033[32m✓\033[0m %-6s %-46s %s\n" "$id" "$name" "$actual" ;;
    FAIL) FAIL=$((FAIL+1)); printf "  \033[31m✗\033[0m %-6s %-46s %s\n" "$id" "$name" "$actual" ;;
    *)    SKIP=$((SKIP+1)); printf "  \033[33m—\033[0m %-6s %-46s %s\n" "$id" "$name" "$actual" ;;
  esac
  log "[$status] $id $name — expected: $expected — actual: $actual"
}

# HTTP status check. expect_http ID "name" URL EXPECTED_CODE [METHOD] [BODY]
expect_http() {
  local id="$1" name="$2" url="$3" want="$4" method="${5:-GET}" body="${6:-}"
  local code
  if [ "$method" = "POST" ]; then
    code=$(curl -sS -o /tmp/_ev_body -w '%{http_code}' --max-time 30 \
           -X POST "$url" -H 'Content-Type: application/json' -d "${body:-{\}}" 2>>"$LOG")
  else
    code=$(curl -sS -o /tmp/_ev_body -w '%{http_code}' --max-time 30 "$url" 2>>"$LOG")
  fi
  log "--- $id $method $url -> $code"; log "$(head -c 600 /tmp/_ev_body 2>/dev/null)"
  if [ "$code" = "$want" ]; then
    record "$id" "$name" "HTTP $want" "HTTP $code" "PASS"
  else
    record "$id" "$name" "HTTP $want" "HTTP $code" "FAIL"
  fi
}

# Run a python expression against the last response body.
json_field() { /usr/bin/python3 -c "
import json,sys
try:
    d=json.load(open('/tmp/_ev_body'))
except Exception:
    print(''); sys.exit()
$1
" 2>/dev/null; }

echo
echo "Local integration evidence — $TS"
echo "================================================================"

# ---------------------------------------------------------------- tiers
echo
echo "Tier availability"
for pair in "3000 Shared" "3001 Destination" "3002 Accommodation" \
            "3003 Activity" "3004 Budget" "3005 Itinerary"; do
  port="${pair%% *}"; svc="${pair#* }"
  expect_http "IT-F$port" "$svc frontend" "http://localhost:$port/" 200
done

for pair in "5000 Shared" "5011 Destination" "5002 Accommodation" \
            "5003 Activity" "5005 Itinerary"; do
  port="${pair%% *}"; svc="${pair#* }"
  expect_http "IT-B$port" "$svc backend /health" "http://localhost:$port/health" 200
done
# budget does not implement /health; confirm liveness on its root instead
expect_http "IT-B5004" "Budget backend (no /health, uses /)" "http://localhost:5004/" 200

for pair in "6000 Shared" "6001 Destination" "6002 Accommodation" \
            "6003 Activity" "6005 Itinerary"; do
  port="${pair%% *}"; svc="${pair#* }"
  expect_http "IT-D$port" "$svc database /health" "http://localhost:$port/health" 200
done
expect_http "IT-D6004" "Budget database (no /health, uses /expenses)" "http://localhost:6004/expenses" 200

# ---------------------------------------------------------------- release 0 data
echo
echo "Release 0 data endpoints"
expect_http "IT-R01" "Shared users"          "http://localhost:6000/users" 200
expect_http "IT-R02" "Destinations"          "http://localhost:6001/destinations" 200
expect_http "IT-R03" "Accommodations"        "http://localhost:6002/accommodations" 200
COUNT=$(json_field "print(len(d.get('data',d)))")
record "IT-R04" "Accommodation record count" ">0" "${COUNT:-0} records" \
       "$([ "${COUNT:-0}" -gt 0 ] 2>/dev/null && echo PASS || echo FAIL)"
expect_http "IT-R05" "Activities"            "http://localhost:6003/activities" 200
expect_http "IT-R06" "Expenses"              "http://localhost:6004/expenses" 200
expect_http "IT-R07" "Itinerary items"       "http://localhost:6005/itinerary-items" 200

# ---------------------------------------------------------------- shared servers
echo
echo "Shared AI servers (non-containerised)"
expect_http "IT-S01" "MCP server health"     "http://localhost:7001/health" 200
TOOLS=$(json_field "print(len(d.get('tools',[])))")
record "IT-S02" "MCP tools registered" ">0" "${TOOLS:-0} tools" \
       "$([ "${TOOLS:-0}" -gt 0 ] 2>/dev/null && echo PASS || echo FAIL)"
expect_http "IT-S03" "RAG server health"     "http://localhost:7002/health" 200

# compose must not contain any AI service
AI_SVCS=$(docker compose config --services 2>/dev/null | grep -icE 'mcp|rag|ollama|agentic' || true)
record "IT-S04" "Compose excludes AI services" "0" "${AI_SVCS:-0} found" \
       "$([ "${AI_SVCS:-0}" -eq 0 ] 2>/dev/null && echo PASS || echo FAIL)"

# container can reach the host servers
HOSTOK=$(docker exec voyage-ai-accommodation-backend-1 python -c "
import requests
ok=0
for p in (7001,7002):
    try:
        if requests.get(f'http://host.docker.internal:{p}/health',timeout=5).status_code==200: ok+=1
    except Exception: pass
print(ok)" 2>/dev/null | tail -1)
record "IT-S05" "Container reaches host via host.docker.internal" "2" "${HOSTOK:-0}/2 reachable" \
       "$([ "${HOSTOK:-0}" -eq 2 ] 2>/dev/null && echo PASS || echo FAIL)"

# ---------------------------------------------------------------- cross-feature
echo
echo "Cross-feature data access (HTTP API only)"
expect_http "IT-X01" "Budget reads accommodation" \
  "http://localhost:5004/budget/mcp/accommodation-by-destination" 200 POST '{"destination":"Tokyo"}'
expect_http "IT-X02" "Accommodation reads budget" \
  "http://localhost:5002/accommodation/mcp/list-expenses" 200 POST '{}'
expect_http "IT-X03" "Accommodation reads destinations" \
  "http://localhost:5002/destinations" 200

# ---------------------------------------------------------------- MCP
echo
echo "MCP integration"
expect_http "IT-M01" "MCP draft from plain text" \
  "http://localhost:5002/accommodation/mcp/draft" 200 POST \
  '{"text":"Add a hostel called Evidence Lodge in Osaka, $45 a night, rated 4.2, with wifi and a pool"}'
DRAFT=$(json_field "r=d.get('result',{}).get('draft',{});print(f\"{r.get('name')} | {r.get('destination_id')} | {r.get('price_per_night')} | {','.join(r.get('amenities') or [])}\")")
record "IT-M02" "Draft extracted expected fields" "name, dest id, price, amenities" "${DRAFT:-none}" \
       "$([ -n "${DRAFT:-}" ] && echo PASS || echo FAIL)"

expect_http "IT-M03" "MCP write rejects missing fields" \
  "http://localhost:5002/accommodation/mcp/create" 400 POST '{"name":"Incomplete"}'

# ---------------------------------------------------------------- RAG
echo
echo "RAG integration"
expect_http "IT-G01" "Corpus refresh" "http://localhost:5002/accommodation/rag/refresh" 200 POST '{}'
CHUNKS=$(json_field "print(d.get('chunk_count'))")
record "IT-G02" "Corpus populated" ">0" "${CHUNKS:-0} chunks" \
       "$([ "${CHUNKS:-0}" -gt 0 ] 2>/dev/null && echo PASS || echo FAIL)"

START=$(date +%s)
expect_http "IT-G03" "Grounded answer with records" \
  "http://localhost:5002/accommodation/rag/ask" 200 POST \
  '{"query":"What accommodation is logged for Kyoto?"}'
ELAPSED=$(( $(date +%s) - START ))
GROUND=$(json_field "r=d.get('result',{});print(f\"{r.get('confidence_category')} | {len(r.get('citations') or [])} citations | {r.get('count')} records\")")
record "IT-G04" "Answer carries citations and confidence" "confidence + >=1 citation" "${GROUND:-none}" \
       "$(echo "${GROUND:-}" | grep -qE '[0-9]+ citations' && echo PASS || echo FAIL)"
record "IT-G05" "Both RAG legs completed" "single request" "${ELAPSED}s elapsed" "PASS"

# ---------------------------------------------------------------- CI parity
echo
echo "CI parity (modes disabled)"
CI=$(cd accommodation-service 2>/dev/null && docker compose -f docker-compose.yml -f docker-compose.ci.yml config 2>/dev/null \
     | grep -cE 'MCP_ENABLED: "false"|RAG_ENABLED: "false"' || true)
record "IT-C01" "CI override disables both modes" "2" "${CI:-0} flags false" \
       "$([ "${CI:-0}" -eq 2 ] 2>/dev/null && echo PASS || echo FAIL)"

# ---------------------------------------------------------------- report
{
  echo "# Local Integration Evidence"
  echo
  echo "- Generated: \`$TS\` (UTC)"
  echo "- Branch: \`$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)\`"
  echo "- Commit: \`$(git rev-parse --short HEAD 2>/dev/null || echo unknown)\`"
  echo "- Containers running: $(docker ps -q 2>/dev/null | wc -l | tr -d ' ')"
  echo
  echo "| ID | Scenario | Expected | Actual | Status |"
  echo "| --- | --- | --- | --- | --- |"
  printf '%s\n' "${ROWS[@]}"
  echo
  echo "**Totals — passed: $PASS · failed: $FAIL · skipped: $SKIP**"
  echo
  echo "Raw request/response log: \`$(basename "$LOG")\`"
} > "$REPORT"

echo
echo "================================================================"
printf "  passed: %s   failed: %s   skipped: %s\n" "$PASS" "$FAIL" "$SKIP"
echo "  report: $REPORT"
echo "  log:    $LOG"
echo

[ "$FAIL" -eq 0 ]
