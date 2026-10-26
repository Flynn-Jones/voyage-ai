# AWS setup guide: VoyageAI whole-stack deployment

Step-by-step AWS CLI walkthrough for standing up every VoyageAI service group (`shared`,
`student-1`, `accommodation-service`, `activity-service`, `budget-service`, `itinerary-service`) on
ECS Fargate behind one ALB, per [`cloud-architecture.md`](cloud-architecture.md). No Terraform/CDK —
every resource is created with a plain `aws` CLI call, driven by the manifest at
[`scripts/deploy/aws/services.yml`](../../../scripts/deploy/aws/services.yml) so each step is shown
once and applied to all 18 containers.

**Nobody has run this yet.** It requires a real AWS account, incurs real cost (ALB + Fargate tasks +
CloudWatch Logs, roughly a few dollars/day while running), and needs credentials no agent should hold.
Run it as the team, not via automation, and tear it down (§12) when you're done with it.

## Prerequisites

- AWS CLI v2, configured (`aws configure`) with an IAM user/role that can create ECR/ECS/ELB/IAM
  resources.
- Docker installed and running locally (to build/push images).
- Python 3 with `pyyaml` (`pip install pyyaml`) — used below to drive the per-service loops off
  `services.yml` instead of retyping 18 near-identical commands.
- Decide the region once and export it everywhere:

  ```bash
  export AWS_REGION=ap-southeast-2        # matches aws_region in services.yml - edit both if you change it
  export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
  cd voyage-ai   # repo root - all paths below are relative to here
  ```

- A small helper to flatten `services.yml` into TSV for the loops in every step below:

  ```bash
  services_tsv() {
    python3 - <<'PY'
import yaml
with open("scripts/deploy/aws/services.yml") as f:
    data = yaml.safe_load(f)
for s in data["services"]:
    print("\t".join([
        s["name"], s["group"], s["tier"], s["ecr_repo"], s["dockerfile"],
        str(s["container_port"]), str(s.get("alb_listener_port", "")),
        s["service_connect_name"],
    ]))
PY
  }
  ```

## 1. Create the 18 ECR repositories

```bash
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

services_tsv | while IFS=$'\t' read -r name group tier repo dockerfile cport albport scname; do
  aws ecr describe-repositories --repository-names "$repo" --region "$AWS_REGION" >/dev/null 2>&1 \
    || aws ecr create-repository --repository-name "$repo" --region "$AWS_REGION"
done
```

## 2. Build and push every image

Tags with the current commit SHA so each deploy is traceable back to the code that produced it (the
same intent as the `tag = git SHA` step already documented for Budget alone).

```bash
GIT_SHA=$(git rev-parse --short HEAD)

services_tsv | while IFS=$'\t' read -r name group tier repo dockerfile cport albport scname; do
  uri="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$repo:$GIT_SHA"
  docker build -f "$dockerfile" -t "$uri" "$(dirname "$dockerfile" | xargs dirname)"
  docker push "$uri"
done
```

`dirname "$dockerfile" | xargs dirname` gives each tier's service-group root (e.g.
`budget-service/api/Dockerfile` → build context `budget-service`) — check each Dockerfile's `COPY`
paths against this before running at scale; if a Dockerfile expects a narrower context, build that one
service by hand instead of in the loop.

## 3. Networking: default VPC + two security groups

Simplest and cheapest option for a class project — Fargate tasks get public IPs directly rather than
paying for a NAT Gateway. (Harden later with private subnets + NAT if this becomes a long-lived
deployment.)

```bash
VPC_ID=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true \
  --query 'Vpcs[0].VpcId' --output text --region "$AWS_REGION")
SUBNET_IDS=$(aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC_ID" \
  --query 'Subnets[].SubnetId' --output text --region "$AWS_REGION")

ALB_SG=$(aws ec2 create-security-group --group-name voyageai-alb-sg \
  --description "ALB ingress for VoyageAI frontends" --vpc-id "$VPC_ID" \
  --query GroupId --output text --region "$AWS_REGION")
for port in 3000 3001 3002 3003 3004 3005; do
  aws ec2 authorize-security-group-ingress --group-id "$ALB_SG" \
    --protocol tcp --port "$port" --cidr 0.0.0.0/0 --region "$AWS_REGION"
done

TASK_SG=$(aws ec2 create-security-group --group-name voyageai-task-sg \
  --description "Intra-cluster traffic for VoyageAI tasks" --vpc-id "$VPC_ID" \
  --query GroupId --output text --region "$AWS_REGION")
# frontends reachable from the ALB only
for port in 3000 3001 3002 3003 3004 80; do
  aws ec2 authorize-security-group-ingress --group-id "$TASK_SG" \
    --protocol tcp --port "$port" --source-group "$ALB_SG" --region "$AWS_REGION"
done
# every service reachable from every other service inside the cluster (Service Connect)
aws ec2 authorize-security-group-ingress --group-id "$TASK_SG" \
  --protocol tcp --port 0-65535 --source-group "$TASK_SG" --region "$AWS_REGION"
```

## 4. ECS cluster + Service Connect namespace

```bash
aws ecs create-cluster --cluster-name voyageai-cluster --region "$AWS_REGION"

aws servicediscovery create-private-dns-namespace \
  --name voyageai.local --vpc "$VPC_ID" --region "$AWS_REGION"
```

## 5. Task execution role

Reuse the AWS-managed role if your account already has it; otherwise create it once (shared by all 18
task definitions — no task needs any permission beyond pulling from ECR and writing logs):

```bash
aws iam get-role --role-name ecsTaskExecutionRole >/dev/null 2>&1 || {
  aws iam create-role --role-name ecsTaskExecutionRole --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Principal": {"Service": "ecs-tasks.amazonaws.com"}, "Action": "sts:AssumeRole"}]
  }'
  aws iam attach-role-policy --role-name ecsTaskExecutionRole \
    --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
}
EXEC_ROLE_ARN=$(aws iam get-role --role-name ecsTaskExecutionRole --query Role.Arn --output text)
```

## 6. Register the 18 task definitions

Env vars (including the forced-off AI flags) come straight from `services.yml`. This script builds each
container definition's JSON and registers it:

```bash
python3 - <<'PY'
import json, os, subprocess, yaml

region = os.environ["AWS_REGION"]
account = os.environ["AWS_ACCOUNT_ID"]
sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode().strip()
exec_role = os.environ["EXEC_ROLE_ARN"]

with open("scripts/deploy/aws/services.yml") as f:
    manifest = yaml.safe_load(f)

common_ai = manifest.get("common_disabled_ai_env", {})

for s in manifest["services"]:
    env = {**common_ai, **s.get("env", {})} if s["tier"] != "database" else s.get("env", {})
    container = {
        "name": s["name"],
        "image": f'{account}.dkr.ecr.{region}.amazonaws.com/{s["ecr_repo"]}:{sha}',
        "portMappings": [{"containerPort": s["container_port"], "protocol": "tcp"}],
        "environment": [{"name": k, "value": v} for k, v in env.items()],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": f'/voyageai/{s["name"]}',
                "awslogs-region": region,
                "awslogs-stream-prefix": "ecs",
                "awslogs-create-group": "true",
            },
        },
    }
    task_def = {
        "family": s["name"],
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "cpu": "256",
        "memory": "512",
        "executionRoleArn": exec_role,
        "containerDefinitions": [container],
    }
    with open(f'/tmp/taskdef-{s["name"]}.json', "w") as out:
        json.dump(task_def, out)
    subprocess.run([
        "aws", "ecs", "register-task-definition",
        "--cli-input-json", f'file:///tmp/taskdef-{s["name"]}.json',
        "--region", region,
    ], check=True)
PY
```

(`environment` is skipped for database tiers that declare no env vars, and populated with the AI flags
only on frontend/api containers — matching that only api-tier code ever calls MCP/RAG/Multi-Agent.)

## 7. ALB, six target groups, six listeners

One listener per frontend's external port, each pointing at that frontend's own target group — mirrors
the local port convention (3000–3005) directly, so nothing in `nav_service`'s port assumptions needs to
change.

```bash
ALB_ARN=$(aws elbv2 create-load-balancer --name voyageai-alb \
  --subnets $SUBNET_IDS --security-groups "$ALB_SG" --scheme internet-facing \
  --query 'LoadBalancers[0].LoadBalancerArn' --output text --region "$AWS_REGION")

services_tsv | awk -F'\t' '$7 != ""' | while IFS=$'\t' read -r name group tier repo dockerfile cport albport scname; do
  TG_ARN=$(aws elbv2 create-target-group --name "tg-$name" --protocol HTTP --port "$cport" \
    --vpc-id "$VPC_ID" --target-type ip --health-check-path /health \
    --query 'TargetGroups[0].TargetGroupArn' --output text --region "$AWS_REGION")
  aws elbv2 create-listener --load-balancer-arn "$ALB_ARN" --protocol HTTP --port "$albport" \
    --default-actions Type=forward,TargetGroupArn="$TG_ARN" --region "$AWS_REGION"
  echo "$name tg_arn=$TG_ARN" >> /tmp/voyageai-target-groups.txt
done
```

(`$7 != ""` keeps only rows with an `alb_listener_port` set — the six frontends.)

## 8. Create the 18 ECS services

Frontends attach to their target group from step 7; every service (frontend, api, database) registers
with Service Connect under the discovery name from `services.yml` — this is what lets `budget-fe`'s
hardcoded `budget-backend:5004` and `itinerary-fe`'s hardcoded `itinerary-be:5005` keep resolving.

```bash
services_tsv | while IFS=$'\t' read -r name group tier repo dockerfile cport albport scname; do
  lb_args=()
  if [ -n "$albport" ]; then
    tg_arn=$(grep "^$name " /tmp/voyageai-target-groups.txt | awk '{print $2}' | cut -d= -f2)
    lb_args=(--load-balancers "targetGroupArn=$tg_arn,containerName=$name,containerPort=$cport")
  fi
  aws ecs create-service \
    --cluster voyageai-cluster \
    --service-name "$name" \
    --task-definition "$name" \
    --desired-count 1 \
    --launch-type FARGATE \
    --network-configuration "awsvpcConfiguration={subnets=[$(echo $SUBNET_IDS | tr ' ' ',')],securityGroups=[$TASK_SG],assignPublicIp=ENABLED}" \
    --service-connect-configuration "enabled=true,namespace=voyageai.local,services=[{portName=$name,discoveryName=$scname,clientAliases=[{port=$cport,dnsName=$scname}]}]" \
    "${lb_args[@]}" \
    --region "$AWS_REGION"
done
```

Give the cluster a few minutes to reach `steady state` (`aws ecs describe-services --cluster
voyageai-cluster --services <name> --region "$AWS_REGION"`) before smoke-testing.

## 9. Smoke test

```bash
ALB_DNS=$(aws elbv2 describe-load-balancers --load-balancer-arns "$ALB_ARN" \
  --query 'LoadBalancers[0].DNSName' --output text --region "$AWS_REGION")
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="docs/release-2/cloud/smoke-test-output-$TS.txt"

for port in 3000 3001 3002 3003 3004 3005; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://$ALB_DNS:$port/")
  if [ "$code" = "200" ]; then
    echo "PASS frontend:$port http_$code" >> "$OUT"
  else
    echo "FAIL frontend:$port http_$code" >> "$OUT"
  fi
done

# One seeded read per backend, e.g. for budget (repeat per service, adjust path/pattern).
# This proves a READ only, not CRUD - the full lifecycle is recorded separately, see below.
curl -s "http://$ALB_DNS:3004/api/expenses" -o /tmp/read.json
if grep -q 'data-expense-id' /tmp/read.json; then
  echo "PASS read:budget-service seeded expense rows returned" >> "$OUT"
else
  echo "FAIL read:budget-service no seeded expense rows" >> "$OUT"
fi

# AI routes must report disabled on every feature that has one, e.g.:
code=$(curl -s -o /dev/null -w '%{http_code}' "http://$ALB_DNS:3004/api/budget/mcp/status")
if [ "$code" = "403" ]; then
  echo "PASS ai-disabled:budget-service http_403" >> "$OUT"
else
  echo "FAIL ai-disabled:budget-service http_$code" >> "$OUT"
fi
```

Repeat the read and AI-disabled checks for `shared`, `student-1`, `accommodation-service`,
`activity-service`, `itinerary-service` against their own ALB ports/routes, appending to the same
`$OUT` file. Keep every line to the exact contract
`ai-services/agentic_loop/README.md` expects: `PASS|FAIL frontend:<name>|read:<service>|ai-disabled:<service> <detail>`.

`read:` lines are not CRUD evidence. After live verification, the AWS teammate runs a complete tagged
create, read, update and delete on each of the five features against this ALB and records it in
`docs/release-2/cloud/crud-lifecycle-evidence-<UTC>.json` (format `voyageai-crud-lifecycle/1`, documented at the
top of `ai-services/agentic_loop/collectors/cloud_collector.py`):

```json
{"format": "voyageai-crud-lifecycle/1", "verified_at": "<UTC ISO, e.g. 2026-10-26T01:23:45Z>",
 "verified_by": "<name>", "alb_url": "http://<ALB DNS name>",
 "features": {"destination-service": {"tag": "r2-crud-<UTC>", "create": "PASS", "read": "PASS",
                                      "update": "PASS", "delete": "PASS", "notes": ""}}}
```

Include all five `-service` features. The review reports a GAP, never a pass, when this file is missing, its
`alb_url` differs from the stack output, `verified_at` is older than the smoke run, or any step lacks an explicit PASS.

## 10. Record stack outputs

```bash
aws ecs list-services --cluster voyageai-cluster --region "$AWS_REGION" \
  --query 'serviceArns' > /tmp/service-arns.json

python3 - <<PY
import json
with open("/tmp/service-arns.json") as f:
    service_arns = json.load(f)
out = {
    "alb_url": "http://$ALB_DNS",
    "alb_arn": "$ALB_ARN",
    "cluster_arn": "arn:aws:ecs:$AWS_REGION:$AWS_ACCOUNT_ID:cluster/voyageai-cluster",
    "service_arns": service_arns,
    "generated": "$TS",
}
with open("docs/release-2/cloud/stack-outputs-$TS.json", "w") as f:
    json.dump(out, f, indent=2)
PY
```

Use the same `$TS` as the smoke output in §9: the review pairs `stack-outputs-<TS>.json` with the
`smoke-test-output-<TS>.txt` it selects (newest), and reads `alb_url` from it. (`smoke-test.sh` writes both
files this way.)

## 11. Verify a full CRUD lifecycle

§9's `read:` checks only prove the seeded data can be **read**. The review additionally wants a
complete tagged create → read → update → delete per feature, which `crud-lifecycle.py` performs for
real over the ALB and records:

```bash
python3 scripts/deploy/aws/crud-lifecycle.py \
  --alb-url "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["alb_url"])' \
                docs/release-2/cloud/stack-outputs-<run>.json)" \
  --verified-by "<your name>"
```

Pass the ALB URL **from the smoke run's own `stack-outputs-<run>.json`** — the review only accepts
the evidence when its `alb_url` matches that file and its `verified_at` is not older than the smoke
run. Each record it creates is tagged `r2-crud-<UTC>` and deleted again, and a `delete` only counts
as PASS once a follow-up read returns 404, so a successful run leaves the deployment as it found it.
It writes `docs/release-2/cloud/crud-lifecycle-evidence-<UTC>.json` and exits non-zero if any
feature's lifecycle failed. `--dry-run` prints the plan without touching anything.

Offline tests for it (no account needed): `python -m pytest scripts/deploy/aws/tests/test_crud_lifecycle.py`

## 12. Write the cloud deployment report

`smoke-test.sh` already generates `docs/release-2/cloud/cloud-deployment-report.md` (and will not
overwrite one whose Release Decision has been filled in — it writes
`cloud-deployment-report-<run>.md` beside it instead). Check it records: the ALB DNS name, which of
the 18 services are steady-state, the smoke result file from §9, and the stack outputs from §10.

On AI: as of Phase 2 the four flags are **not** `false` everywhere. `common_disabled_ai_env` is
still the default, but the five feature APIs override `MCP`/`RAG`/`MULTI_AGENT`/`AI_ENABLED` to
`"true"` and talk to the EC2 AI host in §3 — only `shared-api` and the frontends stay off. The
`ai-mode:` smoke checks confirm each of the five returns a real AI-host-generated answer, and the
review's `ai_flags` check reports what is configured rather than asserting it is off.

Then grade the run:

```bash
cd .. && python ai-services/agentic_loop/app_main.py \
  --mode cloud-deployment-review --decision approve|approve-with-conditions|reject
```

## 13. Teardown (do this when you're done — this costs money while it runs)

Run `scripts/deploy/aws/teardown.sh`. It removes everything in §1–§12 plus the Phase 2 AI
host, in reverse order of creation, and is safe to re-run:

```bash
./scripts/deploy/aws/teardown.sh          # dry run (default): prints the plan, changes nothing
./scripts/deploy/aws/teardown.sh --yes    # actually delete
```

Every resource is **discovered from AWS** and matched against this project's own names
(`voyageai-cluster`, `voyageai-alb`, `voyageai-db-nlb`, the `voyageai/` ECR repos, the
`/voyageai/` log groups and SSM parameters, the four `voyageai-*-sg` groups, the
`Name=voyageai-ai-host` instance), so a stale `services.yml` cannot orphan a resource and
nothing outside the project can be matched by accident. Target groups are collected from
the load balancer they are attached to, never by name pattern.

It handles the ordering that makes a hand-run teardown fail: services are scaled to zero
and **waited** to drain before the security groups they pin can go, listeners are deleted
before their load balancer and the load balancer before its target groups, and the AI
host is terminated and waited on before `voyageai-ai-host-sg`. The security groups
cross-reference each other, so deletion is retried a few passes.

Useful flags:

| flag | effect |
| --- | --- |
| `--dry-run` | the default; print the plan and exit 0 |
| `--yes` | apply the deletions |
| `--keep-ecr` | keep the container images (they do cost storage) |
| `--keep-s3` | keep the `voyageai-ai-bundle-<account>` code bundle |
| `--keep-iam` | keep the AI host role and instance profile |
| `--keep-ai-host` | leave the EC2 AI host running (it is the largest hourly cost) |
| `--include-oidc` | also delete the `voyageai-github-actions` deploy role |
| `--region <r>` | override `AWS_REGION` (default `ap-southeast-2`) |

Never touched: the default VPC and its subnets, `ecsTaskExecutionRole`, and — unless
`--include-oidc` — the GitHub OIDC provider and the `voyageai-github-actions` role, which
cost nothing and whose removal would break every teammate's CI deploy.

Offline tests for the script (stubbed `aws`, no account needed):

```bash
python -m pytest scripts/deploy/aws/tests/test_teardown.py
```
