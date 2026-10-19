# AWS ECS Fargate deployment (Release 2)

Deploys the **whole integrated app** (the shared home page plus each of the five features' frontend,
backend and database) to ECS Fargate behind one Application Load Balancer.
**MCP, RAG, the Multi-Agent Server and Ollama are not deployed.** Every backend runs with
`MCP_ENABLED`, `RAG_ENABLED`, `MULTI_AGENT_ENABLED` and `AI_ENABLED` set to `false`, and its Ollama/MCP/RAG
URLs point at `http://ai-disabled.invalid` so stray calls fail fast.

Start with **[docs/release-2/cloud/credentials-setup.md](../../../docs/release-2/cloud/credentials-setup.md)**
(Learner Lab or a normal account). The architecture is in
[docs/release-2/cloud/architecture.md](../../../docs/release-2/cloud/architecture.md).

## Files

| File | What |
|---|---|
| `network.yml` | VPC, 2 public subnets, IGW. ALB SG (80 + 3000–3005 from the internet), task SG (ALB + itself only). No NAT gateway. |
| `ecr.yml` | **Generated.** One repository per image (19), scan on push, keep the last 10 images, emptied on delete. |
| `cluster.yml` | ECS cluster, Service Connect namespace, CloudWatch log group (7 days), ALB, IAM roles **or** `LabRole`, optional EFS. |
| `services.yml` | **Generated.** One task definition + Fargate service per container (0.25 vCPU / 0.5 GB), target groups and port listeners. |
| `manifest.py` | Single source of truth for images, ports, env vars and Service Connect names. Regenerates the two files above. |
| `bootstrap-oidc.yml` | One-time, normal accounts only: GitHub OIDC provider + deploy role. |
| `deploy.sh`, `smoke-test.sh`, `teardown.sh`, `gate.sh` | Deploy, verify, delete, and the "all workflows green" check. They share `lib.sh`. |
| `smoke-runner/` | Tiny image that runs CRUD and AI-disabled checks **inside** the VPC. |

## Run it

```bash
export AWS_REGION=us-east-1 STACK_PREFIX=voyage-ai          # defaults
export USE_LAB_ROLE=true                                     # AWS Academy Learner Lab only

scripts/deploy/aws/deploy.sh --dry-run    # prints every command, calls nothing (no credentials needed)
scripts/deploy/aws/deploy.sh              # infra -> images (tag = git SHA) -> cluster + services -> wait stable
scripts/deploy/aws/smoke-test.sh          # writes docs/release-2/cloud/smoke-test-output-<UTC>.txt
scripts/deploy/aws/teardown.sh            # deletes everything (asks you to type the prefix; --yes to skip)
```

Parameters: `AWS_REGION`, `STACK_PREFIX`, `IMAGE_TAG` (default: git SHA), `USE_LAB_ROLE`, `ENABLE_EFS`.
`deploy.sh --phase infra|images|app` runs one stage (the GitHub workflow uses these).

**Cost:** about **$0.36/hour, about $8.60/day** in us-east-1. Fargate is 18 × 0.25 vCPU/0.5 GB ≈ $0.22/h,
the ALB about $0.03/h, and public IPv4 addresses 19 × $0.005/h; logs and ECR storage are cents. **Run
`teardown.sh` when you are done.** A deploy, smoke test and demo cost about $0.40.

## Routing: port-based listeners (no student code changes)

Every frontend serves at `/` with root-relative assets, so path-based routing (`/budget/...`) would break
them. The ALB mirrors the local ports instead:

| ALB port | Frontend | Container port |
|---|---|---|
| **80** and 3000 | shared home page | 3000 |
| 3001 | destination (student-1) | 3001 |
| 3002 | accommodation | 3002 |
| 3003 | activity | 3004 |
| 3004 | budget (nginx) | 80 |
| 3005 | itinerary (nginx) | 80 |

> **Some networks block ports 3001–3005** (university and corporate Wi-Fi, some VPNs). The home page on port
> 80 always works. If a feature does not load, try a phone hotspot or home network.

## Service discovery (Service Connect)

Each server registers under its **docker-compose name and port**, so the env vars from `docker-compose.yml`
work unchanged: `shared-backend:5000`, `shared-database:6000`, `destination-backend:5001`,
`destination-database:6001`, `accommodation-backend:5002`, `accommodation-database:6002`,
`activity-backend:5003`, `activity-database:6003`, `budget-backend:5004`, `budget-database:6004`,
`itinerary-database:6005`, and `itinerary-be:5005`. The last one is the compose alias that itinerary-fe's
nginx hardcodes, and the only name any client uses for the itinerary backend. Frontends are clients only.

**Activity's backend and database share one task.** activity-backend opens the database's SQLite file
directly (see `docker-compose.yml`), and separate Fargate tasks cannot share a disk.

## SQLite data

Each database container seeds itself on start (`init_db` in its CMD, or in `app.py`) into an ephemeral
`/data` volume. **Data resets on every redeploy or task restart.** That's fine for the release demo; don't
enter anything you need to keep. For persistence, set `ENABLE_EFS=true`: one encrypted EFS file system with
an access point per database, single writer each, about $0.30/GB-month. It's off by default.

## Changing the deployment

1. Edit `manifest.py`, for example to add an env var or change a port.
2. `pip install pyyaml && python scripts/deploy/aws/manifest.py generate`
3. `cfn-lint scripts/deploy/aws/*.yml && shellcheck -x scripts/deploy/aws/*.sh`
   (integration-ci also runs these and fails if the generated files are stale).

## Teardown order and leftovers

`teardown.sh` deletes services, then cluster (ALB, roles, EFS), then ECR (with images), then network, waiting
for each, and lists any stack left behind. CloudWatch logs are deleted with the cluster stack. Check
**Billing → Bills** the next day.
