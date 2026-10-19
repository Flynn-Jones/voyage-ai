# shellcheck shell=bash
# Shared settings and helpers for deploy.sh, smoke-test.sh and teardown.sh. Source it; don't run it.
#
# Parameters (environment variables):
#   AWS_REGION     default us-east-1 (AWS Academy Learner Lab only allows us-east-1 / us-west-2)
#   STACK_PREFIX   default voyage-ai  -> stacks <prefix>-network|ecr|cluster|services, repos <prefix>/<image>
#   IMAGE_TAG      default: current git SHA (12 chars)
#   USE_LAB_ROLE   "true" on Learner Lab: use the existing LabRole instead of creating IAM roles
#   ENABLE_EFS     "true" to persist SQLite on EFS (default false: ephemeral, reseeded on start)
#   DRY_RUN        "true" (or --dry-run) prints every AWS/Docker command instead of running it

set -euo pipefail

AWS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$AWS_DIR/../../.." && pwd)"
AWS_REGION="${AWS_REGION:-us-east-1}"
STACK_PREFIX="${STACK_PREFIX:-voyage-ai}"
IMAGE_TAG="${IMAGE_TAG:-$(git -C "$ROOT" rev-parse --short=12 HEAD)}"
USE_LAB_ROLE="${USE_LAB_ROLE:-false}"
ENABLE_EFS="${ENABLE_EFS:-false}"
DRY_RUN="${DRY_RUN:-false}"
# Used by the scripts that source this file (shellcheck can't see that from here).
# shellcheck disable=SC2034
COMPOSE_PROJECT="voyageai"   # local image names: voyageai-<compose-service>
# shellcheck disable=SC2034
EVIDENCE_DIR="$ROOT/docs/release-2/cloud"
export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION"

if command -v python3 >/dev/null 2>&1 && python3 -c "" >/dev/null 2>&1; then PYTHON=python3; else PYTHON=python; fi

log() { printf '==> %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
is_dry() { [ "$DRY_RUN" = "true" ]; }

# run CMD...  - execute, or print it in dry-run mode
run() {
  if is_dry; then
    { printf '[dry-run]'; printf ' %q' "$@"; printf '\n'; } >&2   # stderr, so a caller's >/dev/null can't hide it
  else
    "$@"
  fi
}

# query CMD...  - like run, for commands whose stdout is captured; dry-run returns a placeholder
query() {
  if is_dry; then
    { printf '[dry-run]'; printf ' %q' "$@"; printf '\n'; } >&2
    printf '<%s>' "${QUERY_PLACEHOLDER:-value}"
  else
    "$@"
  fi
}

stack_name() { printf '%s-%s' "$STACK_PREFIX" "$1"; }
# tr: Windows Python prints CRLF, which would leave "\r" on every name read by `while read` (Git Bash).
manifest() { "$PYTHON" "$AWS_DIR/manifest.py" "$@" | tr -d '\r'; }
utc_stamp() { date -u +%Y%m%dT%H%M%SZ; }

account_id() {
  QUERY_PLACEHOLDER=ACCOUNT_ID query aws sts get-caller-identity --query Account --output text
}

registry() { printf '%s.dkr.ecr.%s.amazonaws.com' "$1" "$AWS_REGION"; }

require_tools() {
  local missing=()
  for tool in "$@"; do command -v "$tool" >/dev/null 2>&1 || missing+=("$tool"); done
  if [ ${#missing[@]} -gt 0 ]; then
    if is_dry; then log "dry-run: missing tools would be needed for a real run: ${missing[*]}"
    else die "missing required tools: ${missing[*]}"; fi
  fi
}

# deploy_stack <short-name> <template> [Key=Value ...] - create or update, waits for completion
deploy_stack() {
  local name="$1" template="$2"; shift 2
  log "CloudFormation: $(stack_name "$name") <- $(basename "$template")"
  run aws cloudformation deploy \
    --region "$AWS_REGION" \
    --stack-name "$(stack_name "$name")" \
    --template-file "$template" \
    --capabilities CAPABILITY_NAMED_IAM \
    --no-fail-on-empty-changeset \
    --tags "project=$STACK_PREFIX" \
    --parameter-overrides "StackPrefix=$STACK_PREFIX" "$@"
}

stack_output() {  # stack_output <short-name> <OutputKey>
  QUERY_PLACEHOLDER="$2" query aws cloudformation describe-stacks --region "$AWS_REGION" \
    --stack-name "$(stack_name "$1")" --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text
}
