#!/usr/bin/env bash
# Deploy gate: deploy only when EVERY individual workflow succeeded for the commit being deployed.
#
#   scripts/deploy/aws/gate.sh <sha> [--mode latest-main|strict] [--report-only] [--out FILE]
#
# Checks the latest run of student-1..5 and integration-ci for <sha> on main (GitHub API via `gh`).
#   latest-main  (default) a workflow's run for <sha> must have succeeded; if its path filter skipped
#                <sha>, its most recent completed run on main must have succeeded instead. The Source
#                column records which: "current SHA" or "latest green on main".
#   strict       every workflow must have a successful run for exactly <sha>. Becomes the default
#                once every student-N.yml runs on every push to main (student-changes.md #4).
# --report-only  print the table but never fail (the workflow's emergency skip_gate input).
# The Markdown table goes to stdout, --out FILE, and $GITHUB_STEP_SUMMARY when set.
set -euo pipefail

WORKFLOWS=(student-1.yml student-2.yml student-3.yml student-4.yml student-5.yml integration-ci.yml)
SHA="" MODE=latest-main REPORT_ONLY=false OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE="${2:?}"; shift ;;
    --report-only) REPORT_ONLY=true ;;
    --out) OUT="${2:?}"; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    -*) echo "unknown argument: $1" >&2; exit 2 ;;
    *) SHA="$1" ;;
  esac
  shift
done
[ -n "$SHA" ] || { echo "usage: $0 <sha> [--mode latest-main|strict] [--report-only] [--out FILE]" >&2; exit 2; }
case "$MODE" in strict|latest-main) ;; *) echo "--mode must be strict or latest-main" >&2; exit 2 ;; esac
REPO="${GITHUB_REPOSITORY:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"

# newest run as "status<TAB>conclusion<TAB>sha7<TAB>run_id<TAB>url", or nothing
latest_run() {
  gh api "repos/$REPO/actions/workflows/$1/runs?$2&per_page=30" \
    --jq '.workflow_runs | sort_by(.created_at) | last | select(. != null)
          | [.status, (.conclusion // "-"), .head_sha[0:7], (.id|tostring), .html_url] | @tsv' 2>/dev/null || true
}

failed=0
table="| Workflow | Run id | SHA | Conclusion | Source | Gate |
|---|---|---|---|---|---|"
for wf in "${WORKFLOWS[@]}"; do
  row="$(latest_run "$wf" "head_sha=$SHA&branch=main")"
  source="current SHA"
  if [ -z "$row" ] && [ "$MODE" = "latest-main" ]; then
    row="$(latest_run "$wf" "branch=main&status=completed")"
    source="latest green on main (path filter skipped ${SHA:0:7})"
  fi
  if [ -z "$row" ]; then
    table+=$'\n'"| \`$wf\` | - | - | - | no run for ${SHA:0:7} on main | **MISSING** |"
    failed=$((failed + 1))
    continue
  fi
  IFS=$'\t' read -r status conclusion sha7 run_id url <<<"$row"
  [ "$status" = "completed" ] || conclusion="$status"   # queued / in_progress
  if [ "$conclusion" = "success" ]; then
    verdict="ok"
  else
    verdict="**BLOCKED**"
    failed=$((failed + 1))
    [ "$source" = "current SHA" ] || source="latest on main is NOT green (path filter skipped ${SHA:0:7})"
  fi
  table+=$'\n'"| \`$wf\` | [$run_id]($url) | \`$sha7\` | $conclusion | $source | $verdict |"
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
