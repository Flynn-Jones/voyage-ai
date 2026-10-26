#!/bin/bash
set -euo pipefail

# Creates an internal Network Load Balancer in front of the 5 feature databases MCP/RAG
# read directly (destination/accommodation/activity/budget/itinerary), so the AI host EC2
# instance can reach them.
#
# Why this exists: ECS Service Connect's *.voyageai.local DNS names are NOT resolvable
# VPC-wide - Service Connect uses per-task Envoy sidecar proxies that resolve those names
# only for other Service-Connect-enabled tasks in the same mesh. A plain EC2 instance was
# never part of that mesh, so the name genuinely doesn't exist from its point of view (a
# NameResolutionError, not a connectivity/security-group problem). An NLB gets a real,
# VPC-wide-resolvable DNS name, independent of Service Connect - the standard AWS pattern
# for reaching an ECS Service Connect app from outside the mesh.
#
# Safe to re-run: skips creating what already exists, but ALWAYS re-resolves and
# re-registers each database's current task IP - run this again any time a database task
# gets replaced (e.g. after cloud-deployment.yml redeploys it) and RAG/MCP start failing to
# reach it, since ECS Fargate tasks get a fresh IP on every replacement and this NLB's
# target groups are registered by IP, not auto-synced to the ECS service.

AWS_REGION="${AWS_REGION:-ap-southeast-2}"
CLUSTER="voyageai-cluster"
NLB_NAME="voyageai-db-nlb"
NLB_SG_NAME="voyageai-db-nlb-sg"
PARAM_PREFIX="/voyageai/db-nlb"

# service name -> port
DB_SERVICES="destination-database:6001 accommodation-database:6002 activity-database:6003 budget-database:6004 itinerary-db:6005"

echo "=== VoyageAI feature-database NLB setup ==="

VPC_ID=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --region "$AWS_REGION" --query 'Vpcs[0].VpcId' --output text)
SUBNET_IDS=$(aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC_ID" --region "$AWS_REGION" --query 'Subnets[].SubnetId' --output text)
TASK_SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values=voyageai-task-sg --region "$AWS_REGION" --query 'SecurityGroups[0].GroupId' --output text)
AI_HOST_SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values=voyageai-ai-host-sg --region "$AWS_REGION" --query 'SecurityGroups[0].GroupId' --output text)

if [ -z "$TASK_SG" ] || [ "$TASK_SG" = "None" ] || [ -z "$AI_HOST_SG" ] || [ "$AI_HOST_SG" = "None" ]; then
  echo "voyageai-task-sg or voyageai-ai-host-sg not found - run aws-setup-guide.md and ai-ec2-setup.sh first." >&2
  exit 1
fi

# Step 1: security group for the NLB - inbound only from the AI host.
NLB_SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values="$NLB_SG_NAME" \
  --region "$AWS_REGION" --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)
if [ -z "$NLB_SG" ] || [ "$NLB_SG" = "None" ]; then
  NLB_SG=$(aws ec2 create-security-group --group-name "$NLB_SG_NAME" \
    --description "Feature-database NLB - inbound from the AI host only" \
    --vpc-id "$VPC_ID" --region "$AWS_REGION" --query GroupId --output text)
  for port in 6001 6002 6003 6004 6005; do
    aws ec2 authorize-security-group-ingress --group-id "$NLB_SG" \
      --protocol tcp --port "$port" --source-group "$AI_HOST_SG" --region "$AWS_REGION"
  done
  echo "Created $NLB_SG_NAME ($NLB_SG), inbound 6001-6005 from $AI_HOST_SG."
else
  echo "$NLB_SG_NAME already exists ($NLB_SG)."
fi

# Belt-and-suspenders: allow the NLB's own security group to reach the database tasks too,
# in case client IP preservation doesn't apply (traffic would then arrive sourced from the
# NLB's own ENIs, not the AI host's).
for port in 6001 6002 6003 6004 6005; do
  aws ec2 authorize-security-group-ingress --group-id "$TASK_SG" \
    --protocol tcp --port "$port" --source-group "$NLB_SG" --region "$AWS_REGION" 2>/dev/null || true
done
echo "Ensured $TASK_SG allows inbound 6001-6005 from $NLB_SG_NAME too."

# Step 2: the NLB itself.
NLB_ARN=$(aws elbv2 describe-load-balancers --names "$NLB_NAME" --region "$AWS_REGION" \
  --query 'LoadBalancers[0].LoadBalancerArn' --output text 2>/dev/null || true)
if [ -z "$NLB_ARN" ] || [ "$NLB_ARN" = "None" ]; then
  NLB_ARN=$(aws elbv2 create-load-balancer --name "$NLB_NAME" --type network --scheme internal \
    --subnets $SUBNET_IDS --security-groups "$NLB_SG" --region "$AWS_REGION" \
    --query 'LoadBalancers[0].LoadBalancerArn' --output text)
  echo "Created NLB $NLB_NAME."
else
  echo "NLB $NLB_NAME already exists."
fi

NLB_DNS=$(aws elbv2 describe-load-balancers --load-balancer-arns "$NLB_ARN" --region "$AWS_REGION" \
  --query 'LoadBalancers[0].DNSName' --output text)

# Step 3: one target group + listener per database, then register its CURRENT task IP.
for pair in $DB_SERVICES; do
  svc="${pair%%:*}"
  port="${pair##*:}"
  tg_name="tg-${svc}"

  TG_ARN=$(aws elbv2 describe-target-groups --names "$tg_name" --region "$AWS_REGION" \
    --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null || true)
  if [ -z "$TG_ARN" ] || [ "$TG_ARN" = "None" ]; then
    TG_ARN=$(aws elbv2 create-target-group --name "$tg_name" --protocol TCP --port "$port" \
      --vpc-id "$VPC_ID" --target-type ip --health-check-protocol TCP \
      --region "$AWS_REGION" --query 'TargetGroups[0].TargetGroupArn' --output text)
    aws elbv2 create-listener --load-balancer-arn "$NLB_ARN" --protocol TCP --port "$port" \
      --default-actions "Type=forward,TargetGroupArn=$TG_ARN" --region "$AWS_REGION" >/dev/null
    echo "Created target group + listener for $svc (:$port)."
  fi

  TASK_ARN=$(aws ecs list-tasks --cluster "$CLUSTER" --service-name "$svc" --region "$AWS_REGION" \
    --query 'taskArns[0]' --output text)
  if [ -z "$TASK_ARN" ] || [ "$TASK_ARN" = "None" ]; then
    echo "WARNING: no running task found for $svc - skipping target registration." >&2
    continue
  fi
  # Service Connect tasks have 2 attachments; the ENI one isn't always at a fixed index.
  TASK_IP=$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" --region "$AWS_REGION" \
    --query "tasks[0].attachments[?type=='ElasticNetworkInterface'].details[] | [?name=='privateIPv4Address'].value | [0]" \
    --output text)
  if [ -z "$TASK_IP" ] || [ "$TASK_IP" = "None" ]; then
    echo "WARNING: could not resolve a private IP for $svc's task - skipping." >&2
    continue
  fi

  # Each database runs as a single task (desiredCount=1), so exactly one IP should ever
  # be registered - deregister any other targets first (stale IPs from a prior task that
  # got replaced) so they don't accumulate as permanently-unhealthy dead weight.
  STALE_IPS=$(aws elbv2 describe-target-health --target-group-arn "$TG_ARN" --region "$AWS_REGION" \
    --query "TargetHealthDescriptions[?Target.Id!='$TASK_IP'].Target.Id" --output text)
  for stale_ip in $STALE_IPS; do
    aws elbv2 deregister-targets --target-group-arn "$TG_ARN" --targets "Id=$stale_ip" --region "$AWS_REGION"
    echo "Deregistered stale target $stale_ip from $tg_name."
  done

  aws elbv2 register-targets --target-group-arn "$TG_ARN" --targets "Id=$TASK_IP" --region "$AWS_REGION"
  echo "Registered $svc -> $TASK_IP:$port"
done

aws ssm put-parameter --name "$PARAM_PREFIX/dns-name" --value "$NLB_DNS" --type String --overwrite --region "$AWS_REGION"

echo "=== Database NLB setup complete ==="
echo "NLB DNS name: $NLB_DNS"
echo "Re-run this script any time a database task is replaced (IP changes) and MCP/RAG lose access."
