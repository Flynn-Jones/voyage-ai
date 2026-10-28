#!/usr/bin/env bash
# Tear down everything the VoyageAI AWS deployment creates, in reverse order of creation.
#
# This replaces the hand-run snippet in docs/release-2/cloud/aws-setup-guide.md §12, which
# needed shell variables ($ALB_ARN, $TASK_SG, /tmp/voyageai-target-groups.txt) that do not
# survive a new terminal, and which never removed the AI host, the feature-database NLB,
# the IAM role/instance profile, the S3 code bundle, the SSM parameters or the log groups.
#
# Nothing here is derived from services.yml: every resource is DISCOVERED from AWS and
# matched against this project's own names, so a stale manifest cannot orphan a resource
# and nothing outside the project can be matched by accident.
#
#   ./teardown.sh                 # dry run (default): print the plan, change nothing
#   ./teardown.sh --yes           # actually delete
#   ./teardown.sh --yes --keep-ecr --keep-s3
#   ./teardown.sh --yes --include-oidc        # also delete the GitHub Actions deploy role
#
# Safe to re-run: every delete tolerates an already-absent resource.
#
# What is NEVER touched: the default VPC and its subnets, ecsTaskExecutionRole (an AWS
# convention shared with other stacks), and - unless --include-oidc - the GitHub OIDC
# provider plus the voyageai-github-actions role, which cost nothing and whose removal
# would break every teammate's CI deploy.
set -euo pipefail
export AWS_PAGER=""

AWS_REGION="${AWS_REGION:-ap-southeast-2}"
CLUSTER="voyageai-cluster"
ALB_NAME="voyageai-alb"
NLB_NAME="voyageai-db-nlb"
NAMESPACE="voyageai.local"
AI_HOST_TAG="voyageai-ai-host"
AI_ROLE="voyageai-ai-host-role"
AI_PROFILE="voyageai-ai-host-profile"
OIDC_ROLE="voyageai-github-actions"
OIDC_POLICY="voyageai-github-actions-deploy"
SECURITY_GROUPS="voyageai-task-sg voyageai-alb-sg voyageai-db-nlb-sg voyageai-ai-host-sg"
LOG_PREFIX="/voyageai/"
ECR_PREFIX="voyageai/"
SSM_PATH="/voyageai"
# Pause between security-group delete attempts, so a draining ENI has time to go.
RETRY_SLEEP="${TEARDOWN_RETRY_SLEEP:-20}"

APPLY=0
KEEP_ECR=0
KEEP_S3=0
KEEP_IAM=0
KEEP_AI_HOST=0
INCLUDE_OIDC=0

usage() {
  # The header comment block above (stop at the first non-comment line).
  sed -n '2,/^[^#]/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y)        APPLY=1 ;;
    --dry-run)       APPLY=0 ;;
    --keep-ecr)      KEEP_ECR=1 ;;
    --keep-s3)       KEEP_S3=1 ;;
    --keep-iam)      KEEP_IAM=1 ;;
    --keep-ai-host)  KEEP_AI_HOST=1 ;;
    --include-oidc)  INCLUDE_OIDC=1 ;;
    --region)        AWS_REGION="${2:?--region needs a value}"; shift ;;
    -h|--help)       usage 0 ;;
    *) echo "unknown option: $1" >&2; usage 2 ;;
  esac
  shift
done

FAILURES=0
PLANNED=0

say()  { printf '%s\n' "$*"; }
step() { printf '\n--- %s\n' "$*"; }

# aws_read <args...>: a read-only lookup. Always executed, even in a dry run, so the plan
# reflects what is really deployed. Prints nothing and returns 1 when the lookup fails
# (resource absent, permission denied) - callers treat that as "nothing to do".
aws_read() {
  aws "$@" --region "$AWS_REGION" 2>/dev/null
}

# aws_do <description> <args...>: a mutating call, gated by --yes.
aws_do() {
  local what="$1"; shift
  PLANNED=$((PLANNED + 1))
  if [ "$APPLY" -eq 0 ]; then
    say "  DRYRUN $what"
    return 0
  fi
  # One invocation only (a delete must never be retried just to read its error), with both
  # streams captured: an already-absent resource is fine, anything else is not.
  local out rc=0
  out="$(aws "$@" --region "$AWS_REGION" 2>&1)" || rc=$?
  if [ "$rc" -eq 0 ]; then
    say "  DELETED $what"
  else
    case "$out" in
      *NotFound*|*NotFoundException*|*does\ not\ exist*|*NoSuchEntity*|*NoSuchBucket*|*InvalidGroup.NotFound*|*ClusterNotFound*|*ServiceNotFound*|*ParameterNotFound*|*ResourceNotFoundException*)
        say "  ABSENT  $what" ;;
      *)
        say "  FAILED  $what: ${out%%$'\n'*}"
        FAILURES=$((FAILURES + 1)) ;;
    esac
  fi
}

# aws_wait <description> <args...>: a waiter, skipped in a dry run. Never fatal: a waiter
# that times out must not stop the rest of the teardown (that would leak paid resources).
aws_wait() {
  local what="$1"; shift
  if [ "$APPLY" -eq 0 ]; then
    say "  DRYRUN wait for $what"
    return 0
  fi
  say "  waiting for $what ..."
  if ! aws "$@" --region "$AWS_REGION" >/dev/null 2>&1; then
    say "  WARN    stopped waiting for $what; continuing"
  fi
}

say "=== VoyageAI AWS teardown ==="
say "region: $AWS_REGION"
if [ "$APPLY" -eq 0 ]; then
  say "mode:   DRY RUN - nothing will be deleted. Re-run with --yes to apply."
else
  say "mode:   APPLY - resources will be deleted."
fi

# ---- 1. ECS services ---------------------------------------------------------
# Scale to zero, delete, then wait for the tasks to drain: their ENIs hold the task
# security group, so the security groups cannot go until this finishes.
step "1. ECS services in $CLUSTER"
SERVICE_ARNS="$(aws_read ecs list-services --cluster "$CLUSTER" --query 'serviceArns[]' --output text || true)"
TASKDEF_FAMILIES=""
if [ -z "$SERVICE_ARNS" ]; then
  say "  none found"
else
  SERVICE_NAMES=""
  for arn in $SERVICE_ARNS; do
    SERVICE_NAMES="$SERVICE_NAMES ${arn##*/}"
  done
  # Capture each service's task definition family before the services disappear.
  for name in $SERVICE_NAMES; do
    fam="$(aws_read ecs describe-services --cluster "$CLUSTER" --services "$name" \
             --query 'services[0].taskDefinition' --output text || true)"
    case "$fam" in
      arn:*) fam="${fam##*/}"; TASKDEF_FAMILIES="$TASKDEF_FAMILIES ${fam%%:*}" ;;
    esac
  done
  for name in $SERVICE_NAMES; do
    aws_do "ecs service $name (desired-count 0)" \
      ecs update-service --cluster "$CLUSTER" --service "$name" --desired-count 0
  done
  for name in $SERVICE_NAMES; do
    aws_do "ecs service $name" ecs delete-service --cluster "$CLUSTER" --service "$name" --force
  done
  # `ecs wait services-inactive` accepts at most 10 services per call.
  set -- $SERVICE_NAMES
  while [ $# -gt 0 ]; do
    chunk=""
    i=0
    while [ $# -gt 0 ] && [ $i -lt 10 ]; do
      chunk="$chunk $1"; shift; i=$((i + 1))
    done
    aws_wait "services to drain:$chunk" ecs wait services-inactive --cluster "$CLUSTER" --services $chunk
  done
fi

# ---- 2. Load balancers -------------------------------------------------------
# Listeners first (they reference the target groups), then the load balancer, then the
# target groups it fed. Target groups are collected from the load balancer itself, never
# by name pattern, so only this project's are ever deleted.
delete_load_balancer() {
  local lb_name="$1" lb_arn tg_arns listener_arns
  lb_arn="$(aws_read elbv2 describe-load-balancers --names "$lb_name" \
              --query 'LoadBalancers[0].LoadBalancerArn' --output text || true)"
  if [ -z "$lb_arn" ] || [ "$lb_arn" = "None" ]; then
    say "  $lb_name: none found"
    return 0
  fi
  say "  $lb_name: $lb_arn"
  tg_arns="$(aws_read elbv2 describe-target-groups --load-balancer-arn "$lb_arn" \
               --query 'TargetGroups[].TargetGroupArn' --output text || true)"
  listener_arns="$(aws_read elbv2 describe-listeners --load-balancer-arn "$lb_arn" \
                     --query 'Listeners[].ListenerArn' --output text || true)"
  for l in $listener_arns; do
    aws_do "listener ${l##*/}" elbv2 delete-listener --listener-arn "$l"
  done
  aws_do "load balancer $lb_name" elbv2 delete-load-balancer --load-balancer-arn "$lb_arn"
  aws_wait "$lb_name to be deleted" elbv2 wait load-balancers-deleted --load-balancer-arns "$lb_arn"
  for tg in $tg_arns; do
    aws_do "target group ${tg##*/}" elbv2 delete-target-group --target-group-arn "$tg"
  done
}

step "2. Application Load Balancer ($ALB_NAME)"
delete_load_balancer "$ALB_NAME"

step "3. Feature-database Network Load Balancer ($NLB_NAME)"
delete_load_balancer "$NLB_NAME"

# ---- 4. ECS cluster + Service Connect namespace ------------------------------
step "4. ECS cluster + Service Connect namespace"
aws_do "ecs cluster $CLUSTER" ecs delete-cluster --cluster "$CLUSTER"
NS_ID="$(aws_read servicediscovery list-namespaces \
           --query "Namespaces[?Name=='$NAMESPACE'].Id" --output text || true)"
if [ -n "$NS_ID" ] && [ "$NS_ID" != "None" ]; then
  aws_do "service discovery namespace $NAMESPACE ($NS_ID)" \
    servicediscovery delete-namespace --id "$NS_ID"
else
  say "  namespace $NAMESPACE: none found"
fi

# ---- 5. Task definitions -----------------------------------------------------
# Free to keep, but left behind they clutter the console. Only the families the deleted
# services actually referenced are deregistered; on a re-run there is nothing to discover.
step "5. Task definitions"
if [ -z "$TASKDEF_FAMILIES" ]; then
  say "  no families discovered (services already gone); skipping"
else
  for family in $TASKDEF_FAMILIES; do
    revisions="$(aws_read ecs list-task-definitions --family-prefix "$family" --status ACTIVE \
                   --query 'taskDefinitionArns[]' --output text || true)"
    for rev in $revisions; do
      aws_do "task definition ${rev##*/}" ecs deregister-task-definition --task-definition "$rev"
    done
  done
fi

# ---- 6. CloudWatch log groups ------------------------------------------------
step "6. CloudWatch log groups ($LOG_PREFIX*)"
LOG_GROUPS="$(aws_read logs describe-log-groups --log-group-name-prefix "$LOG_PREFIX" \
                --query 'logGroups[].logGroupName' --output text || true)"
if [ -z "$LOG_GROUPS" ]; then
  say "  none found"
else
  for lg in $LOG_GROUPS; do
    aws_do "log group $lg" logs delete-log-group --log-group-name "$lg"
  done
fi

# ---- 7. AI host EC2 instance -------------------------------------------------
step "7. AI host EC2 instance (tag Name=$AI_HOST_TAG)"
if [ "$KEEP_AI_HOST" -eq 1 ]; then
  say "  --keep-ai-host: left running (this one still costs money)"
else
  INSTANCE_IDS="$(aws_read ec2 describe-instances \
                    --filters "Name=tag:Name,Values=$AI_HOST_TAG" \
                              "Name=instance-state-name,Values=pending,running,stopping,stopped" \
                    --query 'Reservations[].Instances[].InstanceId' --output text || true)"
  if [ -z "$INSTANCE_IDS" ]; then
    say "  none found"
  else
    aws_do "ec2 instance(s) $INSTANCE_IDS" ec2 terminate-instances --instance-ids $INSTANCE_IDS
    # Its ENI holds voyageai-ai-host-sg, so the group cannot be deleted until it is gone.
    aws_wait "instance(s) $INSTANCE_IDS to terminate" ec2 wait instance-terminated --instance-ids $INSTANCE_IDS
  fi
fi

# ---- 8. Security groups ------------------------------------------------------
# These reference each other (the task group allows ingress from the ALB, NLB and AI-host
# groups), so a single pass can fail purely on ordering. Retry the whole set a few times:
# each successful delete removes a dependency for the rest.
step "8. Security groups"
sg_id_of() {
  aws_read ec2 describe-security-groups --filters "Name=group-name,Values=$1" \
    --query 'SecurityGroups[0].GroupId' --output text || true
}
# Break the reference cycle BEFORE deleting. These four groups point at each other -
# task-sg allows the ALB, NLB and AI-host groups, and itself on 0-65535 - and AWS refuses
# to delete a group while any rule still references it. That cycle has no valid delete
# order, so retrying alone never clears it; the group-to-group rules have to go first.
# IP/CIDR rules are left alone: they block nothing and the group is about to be deleted.
revoke_group_refs() {
  local name="$1" sg_id perms
  sg_id="$(sg_id_of "$name")"
  [ -n "$sg_id" ] && [ "$sg_id" != "None" ] || return 0
  perms="$(aws_read ec2 describe-security-groups --group-ids "$sg_id" \
             --query 'SecurityGroups[0].IpPermissions' --output json \
           | python3 -I -c 'import json, sys
try:
    perms = json.load(sys.stdin) or []
except ValueError:
    perms = []
print(json.dumps([p for p in perms if p.get("UserIdGroupPairs")]))' 2>/dev/null || echo "[]")"
  [ -n "$perms" ] && [ "$perms" != "[]" ] || return 0
  aws_do "group-to-group ingress rules on $name" \
    ec2 revoke-security-group-ingress --group-id "$sg_id" --ip-permissions "$perms"
}

for sg in $SECURITY_GROUPS; do
  revoke_group_refs "$sg"
done

remaining="$SECURITY_GROUPS"
for attempt in 1 2 3; do
  [ -n "$remaining" ] || break
  still=""
  for sg in $remaining; do
    sg_id="$(sg_id_of "$sg")"
    if [ -z "$sg_id" ] || [ "$sg_id" = "None" ]; then
      [ "$attempt" -eq 1 ] && say "  $sg: none found"
      continue
    fi
    if [ "$APPLY" -eq 0 ]; then
      aws_do "security group $sg ($sg_id)" ec2 delete-security-group --group-id "$sg_id"
      continue
    fi
    if aws ec2 delete-security-group --group-id "$sg_id" --region "$AWS_REGION" >/dev/null 2>&1; then
      PLANNED=$((PLANNED + 1))
      say "  DELETED security group $sg ($sg_id)"
    else
      still="$still $sg"
    fi
  done
  remaining="$still"
  if [ -n "$remaining" ] && [ "$attempt" -lt 3 ]; then
    say "  still in use, retrying:$remaining"
    sleep "$RETRY_SLEEP"
  fi
done
for sg in $remaining; do
  say "  FAILED  security group $sg is still in use (an ENI or another group still references it)"
  FAILURES=$((FAILURES + 1))
done

# ---- 9. ECR repositories -----------------------------------------------------
step "9. ECR repositories ($ECR_PREFIX*)"
if [ "$KEEP_ECR" -eq 1 ]; then
  say "  --keep-ecr: images kept (they do cost storage)"
else
  REPOS="$(aws_read ecr describe-repositories --query 'repositories[].repositoryName' --output text || true)"
  found=0
  for repo in $REPOS; do
    case "$repo" in
      "$ECR_PREFIX"*)
        found=1
        aws_do "ecr repository $repo" ecr delete-repository --repository-name "$repo" --force ;;
    esac
  done
  [ "$found" -eq 1 ] || say "  none found"
fi

# ---- 10. IAM role + instance profile for the AI host -------------------------
step "10. IAM (AI host role + instance profile)"
if [ "$KEEP_IAM" -eq 1 ]; then
  say "  --keep-iam: left in place"
else
  if aws iam get-instance-profile --instance-profile-name "$AI_PROFILE" >/dev/null 2>&1; then
    aws_do "role $AI_ROLE out of instance profile $AI_PROFILE" \
      iam remove-role-from-instance-profile --instance-profile-name "$AI_PROFILE" --role-name "$AI_ROLE"
    aws_do "instance profile $AI_PROFILE" iam delete-instance-profile --instance-profile-name "$AI_PROFILE"
  else
    say "  instance profile $AI_PROFILE: none found"
  fi
  if aws iam get-role --role-name "$AI_ROLE" >/dev/null 2>&1; then
    for pol in $(aws iam list-attached-role-policies --role-name "$AI_ROLE" \
                   --query 'AttachedPolicies[].PolicyArn' --output text 2>/dev/null || true); do
      aws_do "managed policy ${pol##*/} off $AI_ROLE" \
        iam detach-role-policy --role-name "$AI_ROLE" --policy-arn "$pol"
    done
    for pol in $(aws iam list-role-policies --role-name "$AI_ROLE" \
                   --query 'PolicyNames[]' --output text 2>/dev/null || true); do
      aws_do "inline policy $pol off $AI_ROLE" \
        iam delete-role-policy --role-name "$AI_ROLE" --policy-name "$pol"
    done
    aws_do "iam role $AI_ROLE" iam delete-role --role-name "$AI_ROLE"
  else
    say "  role $AI_ROLE: none found"
  fi
fi

# ---- 11. GitHub Actions deploy role (opt-in) ---------------------------------
step "11. GitHub Actions deploy role ($OIDC_ROLE)"
if [ "$INCLUDE_OIDC" -eq 0 ]; then
  say "  skipped (costs nothing, and deleting it breaks the team's CI deploy)."
  say "  Pass --include-oidc to delete it; the OIDC provider itself is always left alone."
elif aws iam get-role --role-name "$OIDC_ROLE" >/dev/null 2>&1; then
  aws_do "inline policy $OIDC_POLICY off $OIDC_ROLE" \
    iam delete-role-policy --role-name "$OIDC_ROLE" --policy-name "$OIDC_POLICY"
  aws_do "iam role $OIDC_ROLE" iam delete-role --role-name "$OIDC_ROLE"
else
  say "  role $OIDC_ROLE: none found"
fi

# ---- 12. S3 code bundle bucket ----------------------------------------------
step "12. S3 code bundle bucket"
ACCOUNT_ID="$(aws_read sts get-caller-identity --query Account --output text || true)"
if [ "$KEEP_S3" -eq 1 ]; then
  say "  --keep-s3: left in place"
elif [ -z "$ACCOUNT_ID" ] || [ "$ACCOUNT_ID" = "None" ]; then
  say "  cannot resolve the account id; skipping (bucket name is voyageai-ai-bundle-<account>)"
else
  BUCKET="voyageai-ai-bundle-$ACCOUNT_ID"
  if aws s3api head-bucket --bucket "$BUCKET" --region "$AWS_REGION" >/dev/null 2>&1; then
    # A bucket must be empty before it can be deleted.
    aws_do "contents of s3://$BUCKET" s3 rm "s3://$BUCKET" --recursive
    aws_do "bucket $BUCKET" s3api delete-bucket --bucket "$BUCKET"
  else
    say "  bucket $BUCKET: none found"
  fi
fi

# ---- 13. SSM parameters ------------------------------------------------------
step "13. SSM parameters ($SSM_PATH/*)"
PARAMS="$(aws_read ssm get-parameters-by-path --path "$SSM_PATH" --recursive \
            --query 'Parameters[].Name' --output text || true)"
if [ -z "$PARAMS" ]; then
  say "  none found"
else
  for prm in $PARAMS; do
    aws_do "ssm parameter $prm" ssm delete-parameter --name "$prm"
  done
fi

# ---- summary -----------------------------------------------------------------
say ""
if [ "$APPLY" -eq 0 ]; then
  say "=== Dry run complete: $PLANNED resource(s) would be deleted. Re-run with --yes. ==="
  exit 0
fi
if [ "$FAILURES" -gt 0 ]; then
  say "=== Teardown finished with $FAILURES failure(s). Re-run it - it is idempotent - and"
  say "    check the AWS console for anything still billing. ===" >&2
  exit 1
fi
say "=== Teardown complete: $PLANNED resource(s) deleted, nothing left failing. ==="
