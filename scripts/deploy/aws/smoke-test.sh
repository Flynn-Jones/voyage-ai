#!/usr/bin/env bash
# Smoke-test the deployed app and save evidence for the cloud-deployment-review agentic-loop mode.
#
#   scripts/deploy/aws/smoke-test.sh [--dry-run] [--alb-dns <dns-name>]
#
# 1. frontend:<name>@<port>  GET every frontend through its ALB listener (from this machine).
# 2. crud:<service> and ai-disabled:<service>  run INSIDE the VPC by the smoke-runner ECS service
#    (scaled 0 -> 1 -> 0), which reaches the private backends by their Service Connect names.
#
# Writes docs/release-2/cloud/smoke-test-output-<UTC>.txt: one "PASS|FAIL <kind>:<name> <detail>"
# line per check (the format agentic_loop's cloud-deployment-review parses). Exit 1 if any FAIL.

# shellcheck source=scripts/deploy/aws/lib.sh
source "$(dirname "$0")/lib.sh"

ALB_DNS=""
RUNNER_TIMEOUT_SECONDS="${RUNNER_TIMEOUT_SECONDS:-600}"
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --alb-dns) ALB_DNS="${2:?--alb-dns needs a value}"; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done
require_tools aws curl "$PYTHON"

STAMP="$(utc_stamp)"
OUT="$EVIDENCE_DIR/smoke-test-output-$STAMP.txt"
RESULTS="$(mktemp)"
trap 'rm -f "$RESULTS"' EXIT
[ -n "$ALB_DNS" ] || ALB_DNS="$(stack_output cluster AlbDnsName)"
log "ALB: $ALB_DNS"

# ---- 1. frontends through the ALB ---------------------------------------------------------
while read -r port name; do
  url="http://$ALB_DNS:$port/"
  if is_dry; then
    printf '[dry-run] curl -s -o /dev/null -w %%{http_code} --max-time 15 %q\n' "$url"
    continue
  fi
  code=000
  for _ in 1 2 3; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$url" || true)"
    case "$code" in 2??|3??) break ;; esac
    sleep 5
  done
  case "$code" in
    2??|3??) echo "PASS frontend:$name@$port HTTP $code $url" ;;
    *) echo "FAIL frontend:$name@$port HTTP $code $url (port $port may also be blocked on your network)" ;;
  esac | tee -a "$RESULTS"
done < <(manifest listeners)

# ---- 2. CRUD + AI-disabled checks inside the VPC -----------------------------------------
LOG_GROUP="/ecs/$STACK_PREFIX"
scale_runner() {
  run aws ecs update-service --region "$AWS_REGION" --cluster "$STACK_PREFIX" --service smoke-runner \
    --desired-count "$1" --query 'service.desiredCount' --output text >/dev/null
}
trap 'scale_runner 0 || true; rm -f "$RESULTS"' EXIT

log "Starting the in-VPC smoke-runner task"
scale_runner 1
if is_dry; then
  printf '[dry-run] poll aws ecs list-tasks --service-name smoke-runner, then aws logs get-log-events --log-group-name %q --log-stream-name smoke-runner/smoke-runner/<task-id> until SMOKE-DONE\n' "$LOG_GROUP"
else
  task_id=""
  deadline=$((SECONDS + RUNNER_TIMEOUT_SECONDS))
  while [ -z "$task_id" ] && [ $SECONDS -lt $deadline ]; do
    task_arn="$(aws ecs list-tasks --region "$AWS_REGION" --cluster "$STACK_PREFIX" --service-name smoke-runner \
      --desired-status RUNNING --query 'taskArns[0]' --output text)"
    if [ "$task_arn" != "None" ]; then task_id="${task_arn##*/}"; else sleep 10; fi
  done
  [ -n "$task_id" ] || die "smoke-runner task did not start within ${RUNNER_TIMEOUT_SECONDS}s"
  stream="smoke-runner/smoke-runner/$task_id"
  log "Reading $LOG_GROUP/$stream"
  done_line=""
  while [ -z "$done_line" ] && [ $SECONDS -lt $deadline ]; do
    sleep 10
    lines="$(aws logs get-log-events --region "$AWS_REGION" --log-group-name "$LOG_GROUP" \
      --log-stream-name "$stream" --start-from-head --query 'events[].message' --output text 2>/dev/null | tr '\t' '\n' || true)"
    done_line="$(printf '%s\n' "$lines" | grep -m1 '^SMOKE-DONE' || true)"
  done
  [ -n "$done_line" ] || die "smoke-runner did not finish within ${RUNNER_TIMEOUT_SECONDS}s (see $LOG_GROUP/$stream)"
  printf '%s\n' "$lines" | grep -E '^(PASS|FAIL) (crud|ai-disabled):' | tee -a "$RESULTS"
  log "$done_line"
fi
scale_runner 0

# ---- evidence ------------------------------------------------------------------------------
if is_dry; then
  log "dry-run: would write ${OUT#"$ROOT"/}"
  exit 0
fi
mkdir -p "$EVIDENCE_DIR"
{
  echo "# VoyageAI cloud smoke test"
  echo "# date: $(date -u +%Y-%m-%dT%H:%M:%SZ)  region: $AWS_REGION  prefix: $STACK_PREFIX  image: $IMAGE_TAG"
  echo "# alb: http://$ALB_DNS/  (frontends on 3000-3005)"
  echo "# format: PASS|FAIL <frontend|crud|ai-disabled>:<name> <detail>"
  cat "$RESULTS"
} >"$OUT"
fails="$(grep -c '^FAIL' "$RESULTS" || true)"
log "Saved ${OUT#"$ROOT"/}: $(grep -c '^PASS' "$RESULTS" || true) pass, $fails fail"
[ "$fails" -eq 0 ]
