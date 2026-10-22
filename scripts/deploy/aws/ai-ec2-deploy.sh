#!/bin/bash
set -euo pipefail

# Repeatable: packages ai-services/{mcp-server,rag-server,multi-agent-server} and pushes
# them onto the AI host EC2 instance provisioned by ai-ec2-setup.sh, via SSM Run Command -
# no SSH key, no public inbound port needed. Installs each service's requirements.txt into
# a shared venv, writes systemd units, and restarts all three.
#
# Run this any time the ai-services code changes and needs to go live on the AI host.
# Safe to re-run - it always overwrites the code and restarts the services.
#
# Run from the voyage-ai repo root.

AWS_REGION="${AWS_REGION:-ap-southeast-2}"
PARAM_PREFIX="/voyageai/ai-host"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

echo "=== VoyageAI AI host deploy ==="

INSTANCE_ID=$(aws ssm get-parameter --name "$PARAM_PREFIX/instance-id" --region "$AWS_REGION" --query 'Parameter.Value' --output text)
BUCKET=$(aws ssm get-parameter --name "$PARAM_PREFIX/bundle-bucket" --region "$AWS_REGION" --query 'Parameter.Value' --output text)

if [ -z "$INSTANCE_ID" ] || [ "$INSTANCE_ID" = "None" ]; then
  echo "No AI host instance tracked - run ai-ec2-setup.sh first." >&2
  exit 1
fi

STATE=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" --region "$AWS_REGION" \
  --query 'Reservations[0].Instances[0].State.Name' --output text)
if [ "$STATE" != "running" ]; then
  echo "Instance $INSTANCE_ID is '$STATE', not 'running' - start it first: aws ec2 start-instances --instance-ids $INSTANCE_ID" >&2
  exit 1
fi

# Step 1: package the three services (code only - no venv/__pycache__/local gitignored
# artefacts, which the AI host rebuilds for itself).
BUNDLE="/tmp/voyageai-ai-bundle.tar.gz"
echo "Packaging mcp-server, rag-server, multi-agent-server..."
tar -czf "$BUNDLE" -C "$REPO_ROOT/ai-services" \
  --exclude='__pycache__' --exclude='.pytest_cache' --exclude='*.pyc' \
  --exclude='chroma' --exclude='corpus' --exclude='logs' --exclude='runs.db' \
  --exclude='rag-audit.jsonl' --exclude='mcp-audit.jsonl' \
  mcp-server rag-server multi-agent-server

echo "Uploading to s3://$BUCKET/bundle.tar.gz..."
aws s3 cp "$BUNDLE" "s3://$BUCKET/bundle.tar.gz" --region "$AWS_REGION" --only-show-errors
rm -f "$BUNDLE"

# Step 2: build the remote install/restart script. Database URLs point at the real
# Service Connect DNS names (see services.yml) - "localhost" only resolves services that
# are actually on this box (the four AI services + Ollama).
REMOTE_SCRIPT=$(cat <<'REMOTE'
set -e
aws s3 cp "s3://__BUCKET__/bundle.tar.gz" /tmp/bundle.tar.gz --region __REGION__
rm -rf /opt/voyageai/mcp-server /opt/voyageai/rag-server /opt/voyageai/multi-agent-server
mkdir -p /opt/voyageai
tar -xzf /tmp/bundle.tar.gz -C /opt/voyageai

if [ ! -d /opt/voyageai/venv ]; then
  python3.11 -m venv /opt/voyageai/venv
fi
/opt/voyageai/venv/bin/pip install --quiet --upgrade pip
/opt/voyageai/venv/bin/pip install --quiet -r /opt/voyageai/mcp-server/requirements.txt
/opt/voyageai/venv/bin/pip install --quiet -r /opt/voyageai/rag-server/requirements.txt
/opt/voyageai/venv/bin/pip install --quiet -r /opt/voyageai/multi-agent-server/requirements.txt

COMMON_ENV="Environment=OLLAMA_BASE_URL=http://localhost:11434
Environment=OLLAMA_MODEL=qwen2.5:7b
Environment=BUDGET_DB_URL=http://budget-database.voyageai.local:6004
Environment=ACCOMMODATION_DB_URL=http://accommodation-database.voyageai.local:6002
Environment=DESTINATION_DB_URL=http://destination-database.voyageai.local:6001
Environment=ACTIVITY_DB_URL=http://activity-database.voyageai.local:6003
Environment=ITINERARY_DB_URL=http://itinerary-db.voyageai.local:6005"

cat > /etc/systemd/system/voyageai-mcp.service <<EOF
[Unit]
Description=VoyageAI shared MCP server
After=network.target ollama.service
[Service]
WorkingDirectory=/opt/voyageai/mcp-server
ExecStart=/opt/voyageai/venv/bin/python mcp_http_server.py
Environment=PORT=7001
$COMMON_ENV
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/voyageai-rag.service <<EOF
[Unit]
Description=VoyageAI shared RAG server
After=network.target ollama.service
[Service]
WorkingDirectory=/opt/voyageai/rag-server
ExecStart=/opt/voyageai/venv/bin/python rag_http_server.py
Environment=PORT=7002
$COMMON_ENV
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/voyageai-multi-agent.service <<EOF
[Unit]
Description=VoyageAI shared Multi-Agent server
After=network.target ollama.service voyageai-mcp.service voyageai-rag.service
[Service]
WorkingDirectory=/opt/voyageai/multi-agent-server
ExecStart=/opt/voyageai/venv/bin/python server.py
Environment=MULTI_AGENT_PORT=7004
Environment=MCP_SERVICE_URL=http://localhost:7001/mcp
Environment=RAG_SERVICE_URL=http://localhost:7002
$COMMON_ENV
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now voyageai-mcp voyageai-rag voyageai-multi-agent
systemctl restart voyageai-mcp voyageai-rag voyageai-multi-agent
sleep 3
systemctl --no-pager status voyageai-mcp voyageai-rag voyageai-multi-agent | head -60
REMOTE
)
REMOTE_SCRIPT="${REMOTE_SCRIPT//__BUCKET__/$BUCKET}"
REMOTE_SCRIPT="${REMOTE_SCRIPT//__REGION__/$AWS_REGION}"

# Step 3: ship it via SSM Run Command (no SSH, matches "no public inbound" throughout).
PARAMS_FILE="/tmp/voyageai-ssm-params.json"
REMOTE_SCRIPT="$REMOTE_SCRIPT" python3 - "$PARAMS_FILE" <<'PY'
import json
import os
import sys

script = os.environ["REMOTE_SCRIPT"]
with open(sys.argv[1], "w") as f:
    json.dump({"commands": script.splitlines()}, f)
PY

echo "Sending deploy command to $INSTANCE_ID via SSM..."
COMMAND_ID=$(aws ssm send-command \
  --instance-ids "$INSTANCE_ID" \
  --document-name "AWS-RunShellScript" \
  --parameters "file://$PARAMS_FILE" \
  --region "$AWS_REGION" \
  --query 'Command.CommandId' --output text)

echo "Command $COMMAND_ID sent. Waiting for it to finish..."
for i in $(seq 1 60); do
  STATUS=$(aws ssm get-command-invocation --command-id "$COMMAND_ID" --instance-id "$INSTANCE_ID" \
    --region "$AWS_REGION" --query 'Status' --output text 2>/dev/null || echo "Pending")
  if [ "$STATUS" != "Pending" ] && [ "$STATUS" != "InProgress" ]; then
    break
  fi
  sleep 5
done

aws ssm get-command-invocation --command-id "$COMMAND_ID" --instance-id "$INSTANCE_ID" --region "$AWS_REGION" \
  --query '{Status:Status,StdOut:StandardOutputContent,StdErr:StandardErrorContent}' --output text

rm -f "$PARAMS_FILE"

if [ "$STATUS" != "Success" ]; then
  echo "Deploy command finished with status: $STATUS" >&2
  exit 1
fi

echo "=== Deploy complete ==="
