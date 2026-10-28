#!/usr/bin/env bash
# Recreate the AWS infrastructure that teardown.sh removes, so a torn-down deployment can be
# brought back. This is the inverse of teardown.sh and the scripted form of
# docs/release-2/cloud/aws-setup-guide.md §1-§7.
#
# WHY THIS EXISTS: cloud-deployment.yml is a *deploy*, not a *provision*. It runs
# `docker push` (ECR does not auto-create repositories) and `ecs update-service` (which
# needs the service to already exist), so dispatching it against a torn-down account fails.
# Three things in particular are recreated by nothing else:
#
#   * the 18 ECR repositories - `docker push` to a missing repo is RepositoryNotFoundException
#   * the 18 /voyageai/<service> log groups - taskdefs.py does NOT set awslogs-create-group,
#     and ecsTaskExecutionRole only carries AmazonECSTaskExecutionRolePolicy, which does not
#     allow logs:CreateLogGroup. A Fargate task whose log group is missing fails to start.
#   * the ECS cluster, Service Connect namespace, security groups and the ALB
#
#   ./provision.sh              # dry run (default): print the plan, change nothing
#   ./provision.sh --yes        # create what is missing
#
# Safe to re-run: every step is idempotent and skips a resource that already exists, so it
# can also repair a partially torn-down account.
#
# It deliberately stops short of images, task definitions and services - those need a build,
# and the guide's taskdefs.py / services.py already do them. The final summary prints the
# exact remaining commands in order.
set -euo pipefail
export AWS_PAGER=""

AWS_REGION="${AWS_REGION:-ap-southeast-2}"
CLUSTER="voyageai-cluster"
NAMESPACE="voyageai.local"
ALB_NAME="voyageai-alb"
ALB_SG_NAME="voyageai-alb-sg"
TASK_SG_NAME="voyageai-task-sg"
MANIFEST="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/services.yml"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
FRONTEND_PORTS="3000 3001 3002 3003 3004 3005"
# Namespace creation is asynchronous. Overridable so the offline tests do not sit
# through the real five-minute poll.
NS_WAIT_TRIES="${PROVISION_NS_WAIT_TRIES:-30}"
NS_WAIT_SLEEP="${PROVISION_NS_WAIT_SLEEP:-10}"

APPLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y)  APPLY=1 ;;
    --dry-run) APPLY=0 ;;
    --region)  AWS_REGION="${2:?--region needs a value}"; shift ;;
    -h|--help) sed -n '2,24p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

CREATED=0
EXISTING=0
FAILURES=0

say()  { printf '%s\n' "$*"; }
step() { printf '\n--- %s\n' "$*"; }

aws_read() { aws "$@" --region "$AWS_REGION" 2>/dev/null; }

# do <description> <args...>: a creating call, gated by --yes.
do_create() {
  local what="$1"; shift
  if [ "$APPLY" -eq 0 ]; then
    say "  DRYRUN create $what"
    CREATED=$((CREATED + 1))
    return 0
  fi
  local out rc=0
  out="$(aws "$@" --region "$AWS_REGION" 2>&1)" || rc=$?
  if [ "$rc" -eq 0 ]; then
    say "  CREATED $what"
    CREATED=$((CREATED + 1))
  else
    case "$out" in
      *AlreadyExists*|*Duplicate*|*already\ exists*|*ResourceInUse*)
        say "  EXISTS  $what"
        EXISTING=$((EXISTING + 1)) ;;
      *)
        say "  FAILED  $what: ${out%%$'\n'*}"
        FAILURES=$((FAILURES + 1)) ;;
    esac
  fi
}

# services.yml is read through python+PyYAML, as every other script here does. A missing
# interpreter or module must be FATAL: these loops run inside $(...), where `set -e` cannot
# see the failure, so without this check steps 1 and 2 would silently provision nothing and
# the run would still report success - the exact failure that leaves ECR repositories and
# log groups missing and the next deploy broken.
PYTHON="${PYTHON:-python3}"
if ! "$PYTHON" -c 'import yaml' 2>/dev/null; then
  echo "FATAL: $PYTHON cannot import yaml (PyYAML)." >&2
  echo "       services.yml drives the ECR repositories and log groups; without it this" >&2
  echo "       script would skip them silently. Install it (pip install pyyaml) or point" >&2
  echo "       PYTHON at an interpreter that has it, e.g. PYTHON=.venv/bin/python $0 ..." >&2
  exit 1
fi

# Service names / ecr repos / ports come from services.yml - never hardcoded here, so a
# manifest change cannot silently leave a resource unprovisioned.
manifest_field() {
  "$PYTHON" - "$MANIFEST" "$1" <<'PY'
import sys
import yaml
with open(sys.argv[1]) as f:
    manifest = yaml.safe_load(f)
field = sys.argv[2]
for s in manifest["services"]:
    if field == "frontends":
        if s.get("alb_listener_port"):
            print(f'{s["name"]}\t{s["container_port"]}\t{s["alb_listener_port"]}')
    else:
        print(s[field])
PY
}

say "=== VoyageAI AWS provision (inverse of teardown.sh) ==="
say "region: $AWS_REGION"
if [ "$APPLY" -eq 0 ]; then
  say "mode:   DRY RUN - nothing will be created. Re-run with --yes to apply."
else
  say "mode:   APPLY"
fi

# ---- 1. ECR repositories -----------------------------------------------------
step "1. ECR repositories"
for repo in $(manifest_field ecr_repo); do
  if aws_read ecr describe-repositories --repository-names "$repo" >/dev/null; then
    say "  exists  $repo"
    EXISTING=$((EXISTING + 1))
  else
    do_create "ecr repository $repo" ecr create-repository --repository-name "$repo"
  fi
done

# ---- 2. CloudWatch log groups ------------------------------------------------
# The gap that makes a torn-down account un-redeployable: taskdefs.py names
# /voyageai/<service> but never sets awslogs-create-group, and the execution role cannot
# create one, so a missing group means the task never starts.
step "2. CloudWatch log groups (/voyageai/*)"
# `--output text` separates names with TABS; normalise so the space-delimited match below
# actually hits, otherwise every existing group is reported as missing.
EXISTING_LGS="$(aws_read logs describe-log-groups --log-group-name-prefix "/voyageai/" \
                  --query 'logGroups[].logGroupName' --output text | tr '\t' ' ' || true)"
for name in $(manifest_field name); do
  lg="/voyageai/$name"
  case " $EXISTING_LGS " in
    *" $lg "*) say "  exists  $lg"; EXISTING=$((EXISTING + 1)) ;;
    *)         do_create "log group $lg" logs create-log-group --log-group-name "$lg" ;;
  esac
done

# ---- 3. Networking lookups ---------------------------------------------------
step "3. Default VPC and subnets"
VPC_ID="$(aws_read ec2 describe-vpcs --filters Name=is-default,Values=true \
            --query 'Vpcs[0].VpcId' --output text || true)"
if [ -z "$VPC_ID" ] || [ "$VPC_ID" = "None" ]; then
  say "  FAILED  no default VPC in $AWS_REGION - provision cannot continue" >&2
  exit 1
fi
SUBNET_IDS="$(aws_read ec2 describe-subnets --filters Name=vpc-id,Values="$VPC_ID" \
                --query 'Subnets[].SubnetId' --output text || true)"
say "  vpc:     $VPC_ID"
say "  subnets: $SUBNET_IDS"

sg_id() {
  aws_read ec2 describe-security-groups --filters "Name=group-name,Values=$1" \
    --query 'SecurityGroups[0].GroupId' --output text || true
}

# ---- 4. Security groups ------------------------------------------------------
step "4. Security groups"
ALB_SG="$(sg_id "$ALB_SG_NAME")"
if [ -n "$ALB_SG" ] && [ "$ALB_SG" != "None" ]; then
  say "  exists  $ALB_SG_NAME ($ALB_SG)"
  EXISTING=$((EXISTING + 1))
elif [ "$APPLY" -eq 1 ]; then
  ALB_SG="$(aws ec2 create-security-group --group-name "$ALB_SG_NAME" \
              --description "ALB ingress for VoyageAI frontends" --vpc-id "$VPC_ID" \
              --query GroupId --output text --region "$AWS_REGION")"
  say "  CREATED $ALB_SG_NAME ($ALB_SG)"
  CREATED=$((CREATED + 1))
  for port in $FRONTEND_PORTS; do
    aws ec2 authorize-security-group-ingress --group-id "$ALB_SG" --protocol tcp \
      --port "$port" --cidr 0.0.0.0/0 --region "$AWS_REGION" >/dev/null 2>&1 \
      && say "    + ingress tcp/$port from 0.0.0.0/0"
  done
else
  say "  DRYRUN create $ALB_SG_NAME + ingress on $FRONTEND_PORTS from 0.0.0.0/0"
  CREATED=$((CREATED + 1))
fi

TASK_SG="$(sg_id "$TASK_SG_NAME")"
if [ -n "$TASK_SG" ] && [ "$TASK_SG" != "None" ]; then
  say "  exists  $TASK_SG_NAME ($TASK_SG)"
  EXISTING=$((EXISTING + 1))
elif [ "$APPLY" -eq 1 ]; then
  TASK_SG="$(aws ec2 create-security-group --group-name "$TASK_SG_NAME" \
               --description "Intra-cluster traffic for VoyageAI tasks" --vpc-id "$VPC_ID" \
               --query GroupId --output text --region "$AWS_REGION")"
  say "  CREATED $TASK_SG_NAME ($TASK_SG)"
  CREATED=$((CREATED + 1))
  # frontends reachable from the ALB only
  for port in 3000 3001 3002 3003 3004 80; do
    aws ec2 authorize-security-group-ingress --group-id "$TASK_SG" --protocol tcp \
      --port "$port" --source-group "$ALB_SG" --region "$AWS_REGION" >/dev/null 2>&1 \
      && say "    + ingress tcp/$port from $ALB_SG_NAME"
  done
  # every task reachable from every other task (Service Connect mesh)
  aws ec2 authorize-security-group-ingress --group-id "$TASK_SG" --protocol tcp \
    --port 0-65535 --source-group "$TASK_SG" --region "$AWS_REGION" >/dev/null 2>&1 \
    && say "    + ingress tcp/0-65535 from itself (Service Connect)"
else
  say "  DRYRUN create $TASK_SG_NAME + ALB ingress + self-ingress"
  CREATED=$((CREATED + 1))
fi

# ---- 5. ECS cluster ----------------------------------------------------------
step "5. ECS cluster"
STATUS="$(aws_read ecs describe-clusters --clusters "$CLUSTER" \
            --query 'clusters[0].status' --output text || true)"
if [ "$STATUS" = "ACTIVE" ]; then
  say "  exists  $CLUSTER"
  EXISTING=$((EXISTING + 1))
else
  do_create "ecs cluster $CLUSTER" ecs create-cluster --cluster-name "$CLUSTER"
fi

# ---- 6. Service Connect namespace --------------------------------------------
step "6. Service Connect namespace ($NAMESPACE)"
NS_ID="$(aws_read servicediscovery list-namespaces \
           --query "Namespaces[?Name=='$NAMESPACE'].Id" --output text || true)"
if [ -n "$NS_ID" ] && [ "$NS_ID" != "None" ]; then
  say "  exists  $NAMESPACE ($NS_ID)"
  EXISTING=$((EXISTING + 1))
else
  # Namespace creation is asynchronous; services.py needs it ACTIVE before it runs.
  do_create "private dns namespace $NAMESPACE" \
    servicediscovery create-private-dns-namespace --name "$NAMESPACE" --vpc "$VPC_ID"
  if [ "$APPLY" -eq 1 ]; then
    say "  waiting up to $((NS_WAIT_TRIES * NS_WAIT_SLEEP))s for $NAMESPACE to become available ..."
    for _ in $(seq 1 "$NS_WAIT_TRIES"); do
      NS_ID="$(aws_read servicediscovery list-namespaces \
                 --query "Namespaces[?Name=='$NAMESPACE'].Id" --output text || true)"
      [ -n "$NS_ID" ] && [ "$NS_ID" != "None" ] && break
      sleep "$NS_WAIT_SLEEP"
    done
    say "  namespace id: ${NS_ID:-<still pending - check before running services.py>}"
  fi
fi

# ---- 7. ALB, target groups, listeners ----------------------------------------
step "7. Application Load Balancer ($ALB_NAME)"
LB_ARN="$(aws_read elbv2 describe-load-balancers --names "$ALB_NAME" \
            --query 'LoadBalancers[0].LoadBalancerArn' --output text || true)"
if [ -n "$LB_ARN" ] && [ "$LB_ARN" != "None" ]; then
  say "  exists  $ALB_NAME"
  EXISTING=$((EXISTING + 1))
elif [ "$APPLY" -eq 1 ]; then
  say "  delegating to alb.py (creates the ALB, one target group and one listener per frontend)"
  ( cd "$REPO_ROOT" && AWS_REGION="$AWS_REGION" VPC_ID="$VPC_ID" SUBNET_IDS="$SUBNET_IDS" \
      ALB_SG="$ALB_SG" "$PYTHON" scripts/deploy/aws/alb.py )
  CREATED=$((CREATED + 1))
else
  say "  DRYRUN create $ALB_NAME + target groups and listeners for:"
  manifest_field frontends | while IFS=$'\t' read -r name cport albport; do
    say "            $name  ALB :$albport -> container :$cport"
  done
  CREATED=$((CREATED + 1))
fi

# ---- summary -----------------------------------------------------------------
say ""
if [ "$APPLY" -eq 0 ]; then
  say "=== Dry run complete: $CREATED resource(s) would be created, $EXISTING already present. ==="
  say "    Re-run with --yes to apply."
  exit 0
fi
if [ "$FAILURES" -gt 0 ]; then
  say "=== Provision finished with $FAILURES failure(s). It is idempotent - fix and re-run. ===" >&2
  exit 1
fi
say "=== Infrastructure ready: $CREATED created, $EXISTING already present. ==="
say ""
say "Images, task definitions and services are NOT provisioned here - they need a build."
say "Finish the restore with, from the repo root:"
say ""
say "  export AWS_REGION=$AWS_REGION"
say "  export AWS_ACCOUNT_ID=\$(aws sts get-caller-identity --query Account --output text)"
say "  export GIT_SHA=\$(git rev-parse --short HEAD)"
say "  export EXEC_ROLE_ARN=\$(aws iam get-role --role-name ecsTaskExecutionRole --query Role.Arn --output text)"
say "  export VPC_ID=$VPC_ID"
say "  export SUBNET_IDS=\"$SUBNET_IDS\""
say "  export TASK_SG=${TASK_SG:-<task sg id>}"
say ""
say "  # 1. build and push all 18 images (see aws-setup-guide.md §2)"
say "  # 2. python3 scripts/deploy/aws/taskdefs.py     # register the 18 task definitions"
say "  # 3. python3 scripts/deploy/aws/services.py     # create the 18 ECS services"
say "  # 4. ./scripts/deploy/aws/ai-ec2-setup.sh       # AI host EC2 + IAM + S3 bundle"
say "  #    ./scripts/deploy/aws/ai-db-nlb-setup.sh    # internal NLB for the feature DBs"
say "  #    ./scripts/deploy/aws/ai-ec2-deploy.sh      # MCP/RAG/Multi-Agent onto the AI host"
say "  #    (then update the AI host IP in services.yml and redeploy the 5 feature APIs)"
say ""
say "After that, cloud-deployment.yml works again (it update-services existing services)."
