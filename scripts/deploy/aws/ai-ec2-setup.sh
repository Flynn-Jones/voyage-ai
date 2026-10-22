#!/bin/bash
set -euo pipefail

# One-time: provisions the EC2 instance that hosts MCP, RAG, the Multi-Agent Server, and
# Ollama together - the cloud equivalent of the single "host machine" these four services
# already run on locally (see ai-services/CLAUDE.md). They stay NOT containerised, exactly
# as documented - this instance just replaces a developer's laptop with a persistent box.
#
# See docs/release-2/cloud/phase-2-ai-mode-plan.md for the full reasoning (EC2 over a
# second ECS cluster, CPU instance to start with a one-line GPU upgrade path later).
#
# Run once. Re-running is mostly safe (security group / IAM / S3 steps are idempotent) but
# will NOT recreate the EC2 instance if one is already tracked in SSM Parameter Store -
# terminate it manually first if you want a fresh instance.
#
# After this, run ai-ec2-deploy.sh to actually put the application code on the instance -
# this script only prepares the box (Ollama + Python + the model), not the servers.

AWS_REGION="${AWS_REGION:-ap-southeast-2}"
INSTANCE_TYPE="${AI_HOST_INSTANCE_TYPE:-t3.xlarge}"  # swap to g4dn.xlarge + a GPU AMI to upgrade later
OLLAMA_MODEL="${AI_HOST_OLLAMA_MODEL:-qwen2.5:7b}"
SG_NAME="voyageai-ai-host-sg"
ROLE_NAME="voyageai-ai-host-role"
PROFILE_NAME="voyageai-ai-host-profile"
PARAM_PREFIX="/voyageai/ai-host"

echo "=== VoyageAI AI host (EC2) setup ==="

EXISTING_INSTANCE_ID=$(aws ssm get-parameter --name "$PARAM_PREFIX/instance-id" --region "$AWS_REGION" \
  --query 'Parameter.Value' --output text 2>/dev/null || true)
if [ -n "$EXISTING_INSTANCE_ID" ] && [ "$EXISTING_INSTANCE_ID" != "None" ]; then
  STATE=$(aws ec2 describe-instances --instance-ids "$EXISTING_INSTANCE_ID" --region "$AWS_REGION" \
    --query 'Reservations[0].Instances[0].State.Name' --output text 2>/dev/null || echo "not-found")
  if [ "$STATE" != "not-found" ] && [ "$STATE" != "terminated" ]; then
    echo "An AI host instance is already tracked ($EXISTING_INSTANCE_ID, state: $STATE)."
    echo "Terminate it first (aws ec2 terminate-instances --instance-ids $EXISTING_INSTANCE_ID) if you want a fresh one."
    exit 0
  fi
fi

VPC_ID=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --region "$AWS_REGION" \
  --query 'Vpcs[0].VpcId' --output text)
SUBNET_ID=$(aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC_ID" --region "$AWS_REGION" \
  --query 'Subnets[0].SubnetId' --output text)
TASK_SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values=voyageai-task-sg \
  --region "$AWS_REGION" --query 'SecurityGroups[0].GroupId' --output text)

if [ -z "$TASK_SG" ] || [ "$TASK_SG" = "None" ]; then
  echo "voyageai-task-sg not found - run the one-time ECS setup in aws-setup-guide.md first." >&2
  exit 1
fi

# Step 1: security group - inbound ONLY from the ECS task security group, never public.
AI_SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values="$SG_NAME" \
  --region "$AWS_REGION" --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)
if [ -z "$AI_SG" ] || [ "$AI_SG" = "None" ]; then
  AI_SG=$(aws ec2 create-security-group --group-name "$SG_NAME" \
    --description "MCP/RAG/Multi-Agent/Ollama - inbound from ECS tasks only" \
    --vpc-id "$VPC_ID" --region "$AWS_REGION" --query GroupId --output text)
  for port in 7001 7002 7004 11434; do
    aws ec2 authorize-security-group-ingress --group-id "$AI_SG" \
      --protocol tcp --port "$port" --source-group "$TASK_SG" --region "$AWS_REGION"
  done
  echo "Created $SG_NAME ($AI_SG), inbound 7001/7002/7004/11434 from $TASK_SG only."
else
  echo "$SG_NAME already exists ($AI_SG)."
fi

# MCP/RAG read the feature databases directly (BUDGET_DB_URL etc., see tools.py /
# rag_pipeline.py) - the AI host isn't a member of voyageai-task-sg, so without this it could
# resolve each database's Service Connect DNS name but never reach it on the wire.
for port in 6000 6001 6002 6003 6004 6005; do
  aws ec2 authorize-security-group-ingress --group-id "$TASK_SG" \
    --protocol tcp --port "$port" --source-group "$AI_SG" --region "$AWS_REGION" 2>/dev/null || true
done
echo "Ensured $TASK_SG allows inbound 6000-6005 from $SG_NAME (feature databases, read by MCP/RAG)."

# Step 2: IAM role - SSM (for ai-ec2-deploy.sh's Run Command, no SSH needed) + read-only on
# this project's own code bundle in S3. No other AWS permissions.
BUCKET="voyageai-ai-bundle-$(aws sts get-caller-identity --query Account --output text)"
if ! aws s3api head-bucket --bucket "$BUCKET" --region "$AWS_REGION" 2>/dev/null; then
  aws s3api create-bucket --bucket "$BUCKET" --region "$AWS_REGION" \
    --create-bucket-configuration LocationConstraint="$AWS_REGION"
  aws s3api put-public-access-block --bucket "$BUCKET" --region "$AWS_REGION" \
    --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  echo "Created private S3 bucket $BUCKET for the code bundle."
else
  echo "S3 bucket $BUCKET already exists."
fi

if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE_NAME" --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]
  }'
  aws iam attach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
  aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name "read-code-bundle" --policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [{\"Effect\": \"Allow\", \"Action\": \"s3:GetObject\", \"Resource\": \"arn:aws:s3:::${BUCKET}/*\"}]
  }"
  echo "Created IAM role $ROLE_NAME (SSM + read-only on $BUCKET)."
else
  echo "IAM role $ROLE_NAME already exists."
fi

if ! aws iam get-instance-profile --instance-profile-name "$PROFILE_NAME" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$PROFILE_NAME"
  aws iam add-role-to-instance-profile --instance-profile-name "$PROFILE_NAME" --role-name "$ROLE_NAME"
  echo "Created instance profile $PROFILE_NAME."
  echo "Waiting for the instance profile to propagate..."
  sleep 15
else
  echo "Instance profile $PROFILE_NAME already exists."
fi

# Step 3: launch the instance. User-data installs Ollama + pulls the model + installs Python -
# the application code itself is deployed separately by ai-ec2-deploy.sh.
USER_DATA=$(cat <<EOF
#!/bin/bash
set -e
dnf install -y python3.11 python3.11-pip git
curl -fsSL https://ollama.com/install.sh | sh
systemctl enable --now ollama
# Ollama needs a moment after first start before it will accept pull requests.
for i in \$(seq 1 30); do curl -sf http://localhost:11434/ >/dev/null && break; sleep 2; done
ollama pull ${OLLAMA_MODEL}
mkdir -p /opt/voyageai
EOF
)

INSTANCE_ID=$(aws ec2 run-instances \
  --image-id "resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64" \
  --instance-type "$INSTANCE_TYPE" \
  --subnet-id "$SUBNET_ID" \
  --security-group-ids "$AI_SG" \
  --iam-instance-profile Name="$PROFILE_NAME" \
  --associate-public-ip-address \
  --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=30,VolumeType=gp3}' \
  --user-data "$USER_DATA" \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=voyageai-ai-host}]' \
  --region "$AWS_REGION" \
  --query 'Instances[0].InstanceId' --output text)

echo "Launched $INSTANCE_ID ($INSTANCE_TYPE). Waiting for it to enter 'running'..."
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID" --region "$AWS_REGION"

PRIVATE_IP=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" --region "$AWS_REGION" \
  --query 'Reservations[0].Instances[0].PrivateIpAddress' --output text)

aws ssm put-parameter --name "$PARAM_PREFIX/instance-id" --value "$INSTANCE_ID" --type String --overwrite --region "$AWS_REGION"
aws ssm put-parameter --name "$PARAM_PREFIX/private-ip" --value "$PRIVATE_IP" --type String --overwrite --region "$AWS_REGION"
aws ssm put-parameter --name "$PARAM_PREFIX/bundle-bucket" --value "$BUCKET" --type String --overwrite --region "$AWS_REGION"

echo "=== AI host setup complete ==="
echo "Instance: $INSTANCE_ID   Private IP: $PRIVATE_IP"
echo "Ollama is installing and pulling ${OLLAMA_MODEL} in the background (user-data) - this can"
echo "take several minutes. Check with: aws ssm start-session --target $INSTANCE_ID, then"
echo "'systemctl status ollama' and 'ollama list'."
echo ""
echo "Next: run scripts/deploy/aws/ai-ec2-deploy.sh to put MCP/RAG/Multi-Agent server code on it."
