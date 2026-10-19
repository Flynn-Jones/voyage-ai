# Cloud deployment report (Release 2)

> **Status: NOT YET DEPLOYED.** Infrastructure is written and validated offline. No AWS resources exist yet;
> waiting for credentials (Learner Lab or a teammate's account). Sections marked _TBD_ are filled in after the
> first real run of `cloud-deployment.yml`.

## 1. What is deployed

| Item | Value |
|---|---|
| Platform | AWS ECS Fargate, 1 Application Load Balancer, Service Connect |
| Region / account type | _TBD_ (default `us-east-1`; Learner Lab **or** normal account with OIDC) |
| Stacks | `<prefix>-network`, `<prefix>-ecr`, `<prefix>-cluster`, `<prefix>-services` |
| Services | 18 Fargate services: shared + 5 features × (frontend, backend, database), activity backend + database in one task, smoke-runner (0 → 1 → 0) |
| Not deployed | MCP server, RAG server, Multi-Agent Server, Ollama |
| AI flags | `MCP_ENABLED`, `RAG_ENABLED`, `MULTI_AGENT_ENABLED`, `AI_ENABLED` = `false` on every backend |
| Data | SQLite reseeded on every start (ephemeral); EFS optional, off |
| Image tag | _TBD_ (git SHA) |
| ALB URL | _TBD_ (home on :80, frontends on :3000–:3005) |
| Estimated cost | about $0.36/h, about $8.60/day (us-east-1) |

## 2. Pre-deployment validation (done, offline)

| Check | Result |
|---|---|
| `cfn-lint` on network, ecr, cluster, services and bootstrap-oidc (us-east-1, us-west-2, ap-southeast-2) | **pass**, 0 findings |
| `shellcheck` on deploy, smoke-test, teardown, gate and lib | **pass** |
| `actionlint` on cloud-deployment, integration-ci and ai-services-ci | **pass** |
| `deploy.sh --dry-run` (normal account and `USE_LAB_ROLE=true`, alternate region/prefix) | **pass**: prints every command, no AWS calls |
| `smoke-test.sh --dry-run`, `teardown.sh --dry-run` | **pass** |
| `docker compose config` + `docker compose build` (all 18 images, the names deploy.sh pushes) | **pass** |
| Local rehearsal of the in-VPC smoke runner (MCP/RAG off) | frontends 6/6, CRUD 6/6, MCP/RAG disabled 10/10; **AI-Mode disabled 0/5** ([details](local-smoke-rehearsal-20261019.md); needs student change #2) |
| `gate.sh` against real GitHub runs for `0875f58` | strict: blocked (2 path-filtered, integration-ci failing); latest-main: blocked only by integration-ci (fixed by this PR) |
| `cloud-deployment-review` static check of `services.yml` | 4 AI flags `false` in 5/5 backends |

## 3. Deployment run (_TBD_)

- Workflow run: _TBD_ (link) · trigger: _TBD_ (workflow_run / workflow_dispatch) · gate mode: _TBD_
- Gate table: _TBD_ (paste from the `gate-table` artifact)
- Duration: gate _TBD_ · validate _TBD_ · ECR push _TBD_ · deploy _TBD_ · smoke _TBD_
- ECR scan-on-push findings: _TBD_ (`aws ecr describe-image-scan-findings`, critical/high counts per image)

## 4. Smoke test (_TBD_)

Paste the summary of `smoke-test-output-<UTC>.txt`:

| Kind | Pass | Fail | Notes |
|---|---|---|---|
| frontend (7 listeners) | _TBD_ | _TBD_ | |
| crud (6 backends) | _TBD_ | _TBD_ | |
| ai-disabled MCP/RAG (10) | _TBD_ | _TBD_ | |
| ai-disabled AI-Mode (5) | _TBD_ | _TBD_ | expected FAIL until student change #2 |

## 5. Agentic-loop review and human decision (_TBD_)

```bash
python ai-services/agentic_loop/app_main.py --mode cloud-deployment-review \
  --decision approve|approve-with-conditions|reject --conditions "..." --decided-by "<name>"
```
- Report: _TBD_ (`docs/release-2/agentic-loop/cloud-deployment-review-<UTC>.md`)
- Verdict: _TBD_ · Human decision: _TBD_ · Conditions: _TBD_

## 6. Teardown (_TBD_)

- `teardown.sh` / workflow `teardown` run: _TBD_ · leftover stacks: _TBD_ · total cost of the exercise: _TBD_
