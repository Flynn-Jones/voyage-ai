#!/usr/bin/env bash
# Delete every VoyageAI AWS resource (stops all charges): services -> cluster/ALB -> ECR -> network.
#
#   scripts/deploy/aws/teardown.sh [--dry-run] [--yes]
#
# ECR repositories are deleted together with their images (EmptyOnDelete). SQLite data is
# ephemeral anyway; with ENABLE_EFS=true the EFS file system is deleted too. Without --yes it
# asks for confirmation (type the stack prefix).

# shellcheck source=scripts/deploy/aws/lib.sh
source "$(dirname "$0")/lib.sh"

ASSUME_YES=false
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --yes) ASSUME_YES=true ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done
require_tools aws

if ! is_dry && [ "$ASSUME_YES" != "true" ]; then
  printf 'This deletes stacks %s-{services,cluster,ecr,network} in %s. Type "%s" to continue: ' \
    "$STACK_PREFIX" "$AWS_REGION" "$STACK_PREFIX" >&2
  read -r answer
  [ "$answer" = "$STACK_PREFIX" ] || die "aborted"
fi

for short in services cluster ecr network; do
  name="$(stack_name "$short")"
  log "Deleting $name"
  run aws cloudformation delete-stack --region "$AWS_REGION" --stack-name "$name"
  run aws cloudformation wait stack-delete-complete --region "$AWS_REGION" --stack-name "$name"
done

log "Leftovers check (should print nothing):"
QUERY_PLACEHOLDER="none" query aws cloudformation list-stacks --region "$AWS_REGION" \
  --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE DELETE_FAILED ROLLBACK_COMPLETE UPDATE_ROLLBACK_COMPLETE \
  --query "StackSummaries[?starts_with(StackName, '$STACK_PREFIX-')].[StackName,StackStatus]" --output text
echo
log "Teardown complete. Check Billing > Bills the next day to confirm charges stopped."
