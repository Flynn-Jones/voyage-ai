# AWS CI/CD guide: how the pipeline works, and how to push an update

Covers `.github/workflows/cloud-deployment.yml` — the automated build → push → redeploy →
validate pipeline for the `voyageai-cluster` ECS Fargate deployment described in
[`cloud-architecture.md`](cloud-architecture.md). For the one-time manual provisioning steps
(ECR repos, the cluster, the ALB, security groups) see [`aws-setup-guide.md`](aws-setup-guide.md)
instead — this workflow only **redeploys code to infrastructure that already exists**.

## One-time setup (per AWS account / GitHub repo pairing)

Run once, locally, before the workflow can authenticate to AWS:

```bash
bash scripts/deploy/aws/gh-oidc-setup.sh
```

This creates:
- A GitHub OIDC identity provider in the AWS account (shared by every repo/role in that account).
- An IAM role (`voyageai-github-actions`) trusting only this exact repo and the branch you're
  currently checked out on — **no AWS access keys are stored anywhere**, in GitHub or otherwise.
- A least-privilege inline policy on that role: ECR push, ECS register/update/describe, read-only
  ALB access for the smoke test, and `iam:PassRole`/`iam:GetRole` scoped to just the
  `ecsTaskExecutionRole`. It deliberately cannot create or delete the cluster, ALB, security
  groups, or ECR repos — those stay one-time, human-run operations.
- The `AWS_ROLE_ARN` GitHub Actions secret the workflow reads.

The script is safe to re-run — it re-syncs the trust policy and permissions idempotently. **Re-run
it whenever you change which branch dispatches the workflow**, since the trust policy only trusts
the branch that was checked out the last time the script ran.

## Pushing a new update

This is the day-to-day loop once the one-time setup above is done:

1. Make your code change in whichever service(s), as normal.
2. Commit and push to the branch the OIDC role trusts (currently `aws-integration` — check with
   `git branch --show-current` before pushing if unsure).
3. Trigger the deploy:
   ```bash
   gh workflow run cloud-deployment.yml --ref <branch>
   gh run watch
   ```
   Or from the GitHub UI: **Actions → cloud-deployment → Run workflow**.
4. Expect ~15–20 minutes — it rebuilds and pushes all 18 images from scratch every run (no layer
   cache across runs today; see "Possible improvements" below).
5. When it finishes, download the evidence:
   ```bash
   gh run download <run-id> --name cloud-deployment-evidence --dir /tmp/cd-evidence
   ```
   This contains `smoke-test-output-<UTC>.txt`, `stack-outputs-<UTC>.json`, and
   `cloud-deployment-report.md` — the exact files
   `ai-services/agentic_loop`'s `cloud-deployment-review` mode reads.
6. Run the review and record a human decision:
   ```bash
   cd .. && ai-services/agentic_loop/.venv/bin/python ai-services/agentic_loop/app_main.py \
     --mode cloud-deployment-review --decision approve|approve-with-conditions|reject \
     --decided-by "<your name>" [--conditions "..."]
   ```
   The report is written to `docs/release-2/agentic-loop/` either way — the decision is still
   recorded as `PENDING (required)` if you omit `--decision`.

### What the workflow actually does

| Step | Purpose |
|---|---|
| Configure AWS credentials (OIDC) | Assumes `voyageai-github-actions` via `aws-actions/configure-aws-credentials@v4` — no stored keys |
| Build and push all 18 service images | Reads `scripts/deploy/aws/services.yml`'s `dockerfile` + `context` per service, tags with the commit SHA, pushes to ECR |
| Register updated task definitions | `scripts/deploy/aws/taskdefs.py` — one new revision per service, referencing the just-pushed image |
| Roll out the new revisions | `aws ecs update-service --force-new-deployment` for all 18 services, then waits for `services-stable` |
| Validate deployment and generate evidence | `scripts/deploy/aws/smoke-test.sh` — frontend/CRUD/AI-disabled checks against the live ALB |
| Upload deployment evidence | Attaches `docs/release-2/cloud/*` as the `cloud-deployment-evidence` artifact (`if: always()`, so you get evidence even on a failed run) |

### What it does *not* do

- Doesn't create or delete the ECR repos, the ECS cluster, the ALB, target groups, listeners,
  security groups, or the Service Connect namespace — those are one-time steps in
  [`aws-setup-guide.md`](aws-setup-guide.md), run manually.
- Doesn't change the fleet shape (adding/removing a service) — that means editing
  `scripts/deploy/aws/services.yml` and the one-time ALB/service-creation scripts
  (`alb.py`, `services.py`) by hand first.
- Doesn't tear anything down. There is currently no automated cleanup script — teardown is the
  manual CLI sequence in `aws-setup-guide.md`'s final section.

## Re-running the smoke test without a full redeploy

If you just want fresh evidence against whatever is currently live (no rebuild):

```bash
bash scripts/deploy/aws/smoke-test.sh
```

## Troubleshooting: real incidents from standing this up

These are genuine failures hit while building this pipeline, kept here because the error messages
are misleading enough that they're worth recognizing on sight rather than re-debugging from
scratch.

| Symptom | Real cause | Fix |
|---|---|---|
| `Could not assume role with OIDC: Not authorized to perform sts:AssumeRoleWithWebIdentity`, persists across the action's internal retries (not transient) | The repo was created/renamed/transferred after 2026-07-15 — GitHub issues the OIDC `sub` claim in the newer immutable form `repo:OWNER@OWNER_ID/REPO@REPO_ID:ref:...`, not the plain `repo:OWNER/REPO:ref:...` form. A trust policy written with the plain form can never match. | `gh-oidc-setup.sh` now resolves `OWNER_ID`/`REPO_ID` via `gh api repos/<slug>` and builds the subject with them. If you see this error on a *new* repo, suspect this first. |
| `docker build` fails with `COPY <dir>: not found`, but only for some services | Build context assumed to be the Dockerfile's own directory (`os.path.dirname(dockerfile)`), but several Dockerfiles (`shared/*`, `budget-backend`) `COPY <tier> ./<tier>`, meaning they need their **group root** as context, not their tier directory. Context isn't derivable from the dockerfile path at all. | `services.yml` now carries an explicit `context` field per service, verified against each Dockerfile's actual `COPY` lines — never re-derive it. |
| `ecs register-task-definition` fails for every service with `Fargate requires task definition to have execution role ARN`, even though the script "ran" | `aws iam get-role --role-name ecsTaskExecutionRole` failed with `AccessDenied` (the deploy role was missing `iam:GetRole`) — but it was called inside `export X=$(...)`, and bash's `set -e` does **not** catch a failing command substitution used in an assignment. `EXEC_ROLE_ARN` silently became an empty string. | Added `iam:GetRole` to the deploy role's policy, and `taskdefs.py` now validates `EXEC_ROLE_ARN` looks like a real role ARN before using it, failing loudly instead of producing a broken task definition. |

The general lesson across all three: a script that "completes" with exit code 0 (or an action that
retries silently) is not proof the step actually did what it claims — check the actual resource
state, not just the log's happy path.

## Possible improvements (not done yet)

- No Docker layer caching between runs (e.g. `docker/build-push-action` with GitHub Actions cache,
  or ECR-based caching) — every run rebuilds all 18 images from scratch, ~15 min of the ~15–20 min
  total.
- No rollback step if `services-stable` times out — a stuck deployment currently needs manual
  `aws ecs` intervention.
- The `push: branches: [main]` trigger is commented out (matches the Lab 11 pattern this was built
  from) — deliberate, so merges to `main` don't auto-deploy until the team decides that's wanted.
