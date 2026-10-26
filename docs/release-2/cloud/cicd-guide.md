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
3. Trigger the deploy with the full SHA of that branch's HEAD (the run refuses any other SHA):
   ```bash
   gh workflow run cloud-deployment.yml --ref <branch> -f sha=$(git rev-parse HEAD)
   gh run watch
   ```
   Or from the GitHub UI: **Actions → cloud-deployment → Run workflow**, pasting the full SHA.
4. Expect the gate (all five student workflows plus the security scan, in parallel) and then
   ~15–20 minutes of deploy — it rebuilds and pushes all 18 images from scratch every run (no layer
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

```
preflight ─┬─ security-scans ─────────┐
           ├─ student-1 (reusable) ───┤
           ├─ student-2 (reusable) ───┤
           ├─ student-3 (reusable) ───┼─> gate (always; 7/7 success) ─> deploy
           ├─ student-4 (reusable) ───┤
           └─ student-5 (reusable) ───┘
```

| Job / step | Purpose |
|---|---|
| `preflight` | Checks the `sha` input is a full SHA equal to the dispatched branch HEAD and the checked-out commit; validates the root Compose config; runs `scripts/deploy/aws/tests` |
| `security-scans` | `pre-commit run --all-files` with `.pre-commit-config.yaml` (artifact `cloud-deployment-pre-commit-scan`). Additional CI security validation — **not** proof that any developer ran their local hook |
| `student-1` … `student-5` | Each student's own workflow, called with `workflow_call` on the same SHA (endpoint suites, builds, Compose checks) |
| `gate` | Always runs; fails unless all seven jobs above concluded `success` (failed, skipped, cancelled or missing all block) |
| `deploy` → Build all 18 service images | Runs only after `gate` succeeds. Reads `scripts/deploy/aws/services.yml`'s `dockerfile` + `context` per service and builds locally, before any AWS access |
| Configure AWS credentials (OIDC) | Assumes `voyageai-github-actions` via `aws-actions/configure-aws-credentials@v4` — no stored keys; only `deploy` has `id-token: write` |
| Tag and push all 18 service images | Tags the images built above with the ECR URI and commit SHA, pushes to ECR |
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

## Capturing post-commit CI evidence for the final commit

The assessed evidence is the five **standalone** `student-N.yml` push runs on the final commit. A
successful cloud-deployment run separately proves the deployed SHA was validated, but its nested
runs are not a substitute for those five links. Do not commit evidence afterwards — that changes the
SHA. Keep it outside the repo.

```bash
SHA=$(git rev-parse HEAD); git fetch origin; test "$SHA" = "$(git rev-parse origin/main)" && echo "$SHA"
for n in 1 2 3 4 5; do
  gh run list --workflow student-$n.yml --commit "$SHA" --event push \
    --json databaseId,url,headSha,status,conclusion --limit 3
done
# then, per student N with run id ID:
gh run watch ID --exit-status
D=~/voyage-evidence/$SHA/student-N; mkdir -p "$D"
gh run view ID --json url,headSha,headBranch,event,conclusion,jobs > "$D/run.json"
gh run view ID --log > "$D/run.log"
gh run download ID --dir "$D/artifacts"
jq -e --arg s "$SHA" '.headSha==$s and .event=="push" and .conclusion=="success"' "$D/run.json"
grep -c '| PASS |' "$D"/artifacts/*/endpoint-tests.md   # expect 2
```

| Workflow | Artifacts |
|---|---|
| student-1 | `student-1-offline-test-results`, `student-1-live-test-results`, `student-1-report` |
| student-2 | `student-2-junit-unit-tests`, `student-2-junit-endpoint-tests` |
| student-3 | `student-3-pre-commit-scan`, `student-3-offline-test-results`, `student-3-live-test-results`, `student-3-report` |
| student-4 | `student-4-junit-unit-tests`, `student-4-junit-endpoint-tests` |
| student-5 | `student-5-endpoint-tests`, `student-5-report` |

Record per student: run URL, full `headSha`, conclusion and artifact names. A flaky failure can be
re-run with `gh run rerun ID --failed` (same SHA, same push event).

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
| Same `sts:AssumeRoleWithWebIdentity` error, but the subject format is already correct and the deploy previously worked | The trust policy pins **one branch**, and the run was dispatched from a different one. `gh-oidc-setup.sh` used to derive the branch from `git rev-parse --abbrev-ref HEAD`, so whoever ran it last silently re-pinned the shared role to their feature branch — deploys from `main` then failed while deploys from that branch kept working. The images all build first, so the failure lands late, at "Configure AWS credentials", *before* any push or rollout (nothing is half-deployed). | Check the live policy: `aws iam get-role --role-name voyageai-github-actions --query 'Role.AssumeRolePolicyDocument'`, and compare its `:ref:refs/heads/<branch>` to the ref you dispatched. The script now defaults to `main` and takes the branch as an explicit argument instead of reading HEAD. |
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
