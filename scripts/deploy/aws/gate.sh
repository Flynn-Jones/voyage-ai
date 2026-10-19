#!/usr/bin/env bash
# Deploy gate: deploy only when EVERY individual workflow succeeded for the commit being deployed.
#
#   scripts/deploy/aws/gate.sh <sha> [--mode strict|latest-main] [--report-only] [--out FILE]
#
# Checks the latest run of student-1..5 and integration-ci for <sha> on main (GitHub API via `gh`).
#   strict       (default) every workflow must have a successful run for exactly <sha>.
#   latest-main  if a workflow did not run for <sha> (its path filter skipped it), its latest
#                completed run on main must have succeeded instead; the table says so.
# --report-only  print the table but never fail (the workflow's emergency skip_gate input).
# The Markdown table goes to stdout, --out FILE, and $GITHUB_STEP_SUMMARY when set.
set -euo pipefail

WORKFLOWS=(student-1.yml student-2.yml student-3.yml student-4.yml student-5.yml integration-ci.yml)
SHA="" MODE=strict REPORT_ONLY=false OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE="${2:?}"; shift ;;
    --report-only) REPORT_ONLY=true ;;
    --out) OUT="${2:?}"; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    -*) echo "unknown argument: $1" >&2; exit 2 ;;
    *) SHA="$1" ;;
  esac
  shift
done
[ -n "$SHA" ] || { echo "usage: $0 <sha> [--mode strict|latest-main] [--report-only] [--out FILE]" >&2; exit 2; }
case "$MODE" in strict|latest-main) ;; *) echo "--mode must be strict or latest-main" >&2; exit 2 ;; esac
REPO="${GITHUB_REPOSITORY:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"

# newest run as "status<TAB>conclusion<TAB>sha7<TAB>run_number<TAB>url", or nothing
latest_run() {
  gh api "repos/$REPO/actions/workflows/$1/runs?$2&per_page=30" \
    --jq '.workflow_runs | sort_by(.created_at) | last | select(. != null)
          | [.status, (.conclusion // "-"), .head_sha[0:7], (.run_number|tostring), .html_url] | @tsv' 2>/dev/null || true
}

failed=0
table="| Workflow | Run | Commit | Status | Conclusion | Gate |
|---|---|---|---|---|---|"
for wf in "${WORKFLOWS[@]}"; do
  row="$(latest_run "$wf" "head_sha=$SHA&branch=main")"
  note=""
  if [ -z "$row" ] && [ "$MODE" = "latest-main" ]; then
    row="$(latest_run "$wf" "branch=main&status=completed")"
    [ -n "$row" ] && note=" (path-filtered for ${SHA:0:7}; latest main run)"
  fi
  if [ -z "$row" ]; then
    table+=$'\n'"| \`$wf\` | - | - | - | - | **MISSING**: no run for ${SHA:0:7} on main |"
    failed=$((failed + 1))
    continue
  fi
  IFS=$'\t' read -r status conclusion sha7 number url <<<"$row"
  if [ "$status" = "completed" ] && [ "$conclusion" = "success" ]; then
    verdict="ok$note"
  else
    verdict="**BLOCKED**$note"
    failed=$((failed + 1))
  fi
  table+=$'\n'"| \`$wf\` | [#$number]($url) | \`$sha7\` | $status | $conclusion | $verdict |"
done

summary="### Deploy gate for \`${SHA:0:7}\` (mode: $MODE)

$table

"
if [ "$failed" -eq 0 ]; then
  summary+="**GATE PASSED**: all ${#WORKFLOWS[@]} workflows succeeded."
elif [ "$REPORT_ONLY" = "true" ]; then
  summary+="**GATE SKIPPED** (skip_gate=true): $failed workflow(s) not green. Emergency deploy; record why."
else
  summary+="**GATE FAILED**: $failed workflow(s) missing or not successful. Not deploying."
fi

printf '%s\n' "$summary"
[ -n "$OUT" ] && printf '%s\n' "$summary" >"$OUT"
[ -n "${GITHUB_STEP_SUMMARY:-}" ] && printf '%s\n' "$summary" >>"$GITHUB_STEP_SUMMARY"
if [ "$failed" -gt 0 ] && [ "$REPORT_ONLY" != "true" ]; then exit 1; fi
exit 0
