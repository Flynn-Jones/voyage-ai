# VoyageAI cloud architecture (Release 2)

## AWS reference architecture

```mermaid
flowchart LR
    user([Browser]) -->|":80 home<br/>:3000-3005 frontends"| alb

    subgraph aws["AWS account · region us-east-1 (default)"]
        ecr[("ECR<br/>19 repos · scan on push<br/>tag = git SHA")]
        cw[("CloudWatch Logs<br/>/ecs/&lt;prefix&gt;")]

        subgraph vpc["VPC 10.40.0.0/16 · 2 public subnets · no NAT"]
            alb["Application Load Balancer<br/>SG: 80, 3000-3005"]

            subgraph ecs["ECS cluster (Fargate) · task SG: ALB + self only · Service Connect namespace"]
                direction TB
                subgraph fe["Frontends (ALB targets)"]
                    sf[shared-frontend :3000]
                    df[destination-frontend :3001]
                    af[accommodation-frontend :3002]
                    tf[activity-frontend :3004]
                    bf[budget-frontend :80]
                    itf[itinerary-frontend :80]
                end
                subgraph be["Backends (private)"]
                    sb[shared-backend :5000]
                    db[destination-backend :5001]
                    ab[accommodation-backend :5002]
                    bb[budget-backend :5004]
                    ib["itinerary-be :5005"]
                end
                subgraph dbs["Databases: SQLite, reseeded on start (EFS optional)"]
                    sd[shared-database :6000]
                    dd[destination-database :6001]
                    ad[accommodation-database :6002]
                    bd[budget-database :6004]
                    idb[itinerary-database :6005]
                end
                subgraph act["activity task (shared /data)"]
                    tb[activity-backend :5003]
                    td[activity-database :6003]
                end
                smoke["smoke-runner<br/>desired 0 → 1 during smoke test"]
            end
        end
    end

    alb --> sf & df & af & tf & bf & itf
    sf --> sb --> sd
    df --> db --> dd
    af --> ab --> ad
    ab --> dd
    tf --> tb --> td
    tb --> ad
    bb --> bd
    bb --> ad
    itf --> ib --> idb
    ib --> dd
    ib --> td
    smoke -.->|CRUD + AI-disabled checks| be
    ecs -.->|pull images| ecr
    ecs -.->|awslogs| cw

    notdeployed["NOT deployed: MCP 7001 · RAG 7002 · Multi-Agent 7004 · Ollama<br/>Backends run with MCP/RAG/MULTI_AGENT/AI_ENABLED=false"]
    style notdeployed fill:#fff3cd,stroke:#c9a227
```

- **One ALB with port listeners** mirrors the local ports. Frontends serve at `/`, so path routing would
  break them. Port 80 also serves the home page, because some networks block 3001–3005.
- **Service Connect** gives every server its docker-compose `hostname:port`, so no env var or student
  code changes are needed. Backends and databases are reachable only inside the task security group.
- **Public subnets without a NAT gateway** keep cost down. Tasks get public IPs only to pull images
  from ECR, and the security group admits traffic from the ALB and from other tasks only.
- **SQLite is ephemeral.** Each database reseeds on start. Optional EFS (`ENABLE_EFS=true`) persists it.
- **No AI in the cloud.** The four AI flags are `false` on every backend, and AI hosts resolve to
  `ai-disabled.invalid`.

## DevOps flow

```mermaid
flowchart LR
    dev([Developer]) --> pc["pre-commit<br/>ruff S · detect-secrets · pip-audit"]
    pc -->|git push / PR| gh[(GitHub)]
    gh --> s["student-1..5 workflows<br/>endpoint tests per feature"]
    gh --> ic["integration-ci<br/>compose config + build<br/>cfn-lint · shellcheck · dry run"]
    gh --> aic["ai-services-ci<br/>pytest MCP · RAG · Multi-Agent · agentic loop"]
    s & ic -->|workflow_run on main<br/>if CLOUD_AUTO_DEPLOY=true| cd
    manual([workflow_dispatch]) --> cd
    subgraph cd["cloud-deployment.yml"]
        direction LR
        gate["gate<br/>all 6 workflows green<br/>for this commit"] --> val["validate<br/>docker compose build"]
        val --> push["ECR push<br/>tag = git SHA"]
        push --> dep["deploy<br/>CloudFormation stacks<br/>wait services-stable"]
        dep --> smk["smoke test<br/>frontends · CRUD · AI disabled"]
    end
    dep --> awsx[(AWS ECS Fargate)]
    smk --> ev["docs/release-2/cloud/<br/>smoke output · stack outputs"]
    ev --> loop["agentic loop<br/>cloud-deployment-review<br/>+ human release decision"]
```

| Stage | Where | Evidence |
|---|---|---|
| Pre-commit security scans | developer machine (`pre-commit install`) | `docs/release-2/<who>/pre-commit-*.txt` |
| Feature endpoint tests | `student-1..5.yml` | JUnit artifacts per workflow |
| Integration build | `integration-ci.yml` | run log |
| Deploy gate | `cloud-deployment.yml` → `gate` | `gate-table` artifact + job summary |
| Deploy + smoke | `cloud-deployment.yml` | `deployment-outputs` and `smoke-test-output` artifacts |
| Release review | `app_main.py --mode cloud-deployment-review` | `docs/release-2/agentic-loop/cloud-deployment-review-*.md` |
