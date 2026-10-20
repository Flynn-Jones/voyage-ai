# AWS credentials setup for the cloud deployment

There are two supported paths. Pick **one**.

| | **A. AWS Academy Learner Lab** | **B. Normal AWS account (OIDC)** |
|---|---|---|
| Who | Through the tutor / course | A teammate's own account |
| IAM roles | Cannot be created; the deployment uses the existing **`LabRole`** | Created by the stacks; GitHub signs in with **OIDC** (no stored keys) |
| GitHub secrets | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` | `AWS_ROLE_ARN` |
| `USE_LAB_ROLE` variable | `true` | `false` |
| Region | `us-east-1` (Learner Lab allows us-east-1 / us-west-2) | `us-east-1` default, any region via `AWS_REGION` |
| Key lifetime | **About 4 hours.** Refresh the 3 secrets at the start of every lab session. | No keys. Tokens are minted per workflow run. |

Never commit keys, paste them in issues or chat, or put them in `.env` files that get pushed. The pre-commit
detect-secrets hook blocks most of these, but don't rely on it.

---

## A. AWS Academy Learner Lab

### A1. Start the lab and get the keys (every session)
1. AWS Academy → your course → **Modules → Launch AWS Academy Learner Lab** → **Start Lab**. Wait until the
   dot next to "AWS" turns **green**. Sessions last about 4 hours.
2. Click **AWS Details** → **AWS CLI: Show**. You'll see a block like:
   ```
   [default]
   aws_access_key_id=ASIA...
   aws_secret_access_key=...
   aws_session_token=IQoJb3JpZ2...
   ```
3. Paste that block into `~/.aws/credentials` on your machine (replacing any old `[default]`), and set the region:
   ```bash
   aws configure set region us-east-1
   aws sts get-caller-identity        # Arn should contain assumed-role/voclabs/...
   ```

### A2. One-time checks (read-only)
```bash
aws iam get-role --role-name LabRole --query Role.Arn --output text   # must print arn:aws:iam::<acct>:role/LabRole
aws servicediscovery list-namespaces --max-results 1                   # Service Connect needs Cloud Map
aws ecs list-clusters                                                  # ECS is allowed
```
If any of these fails with `AccessDenied`, stop and tell the team. Learner Lab can't run this design without
those services.

### A3. GitHub secrets and variables (repo → Settings → Secrets and variables → Actions)
**Secrets**, refreshed every session (the GitHub CLI reads them from your `~/.aws/credentials` without
printing them):
```bash
gh secret set AWS_ACCESS_KEY_ID     --body "$(aws configure get aws_access_key_id)"
gh secret set AWS_SECRET_ACCESS_KEY --body "$(aws configure get aws_secret_access_key)"
gh secret set AWS_SESSION_TOKEN     --body "$(aws configure get aws_session_token)"
```
Do **not** set `AWS_ROLE_ARN` on this path. If it is set, the workflow tries OIDC, which Learner Lab blocks.

**Variables**, set once:
```bash
gh variable set AWS_REGION        --body us-east-1
gh variable set USE_LAB_ROLE      --body true
gh variable set STACK_PREFIX      --body voyage-ai
gh variable set CLOUD_AUTO_DEPLOY --body false
gh variable set GATE_MODE         --body latest-main
```

### A4. Refresh routine (about every 4 hours)
Start Lab → AWS Details → paste the new block into `~/.aws/credentials` → re-run the three `gh secret set`
commands in A3. A workflow that starts with expired keys fails at "AWS credentials" with `ExpiredToken`.
Refresh, then **Re-run failed jobs**.

### A5. Budget
Learner Lab accounts usually can't open Billing or AWS Budgets. Watch the **budget figure on the lab page**
instead. The stack costs about **$8.60/day**, and **ending the lab session does not stop the load balancer and
Fargate tasks from charging.** Run `scripts/deploy/aws/teardown.sh` (or the workflow with action `teardown`)
before you close the lab.

---

## B. Normal AWS account with GitHub OIDC

### B1. Sign in locally (admin, one time)
Use IAM Identity Center (`aws configure sso`, then `aws sso login`) or an admin profile. Confirm with
`aws sts get-caller-identity`.

### B2. Billing alarm first
```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
aws budgets create-budget --account-id "$ACCOUNT" \
  --budget '{"BudgetName":"voyage-ai-monthly","BudgetLimit":{"Amount":"20","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}' \
  --notifications-with-subscribers '[
    {"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":50,"ThresholdType":"PERCENTAGE"},
     "Subscribers":[{"SubscriptionType":"EMAIL","Address":"you@example.com"}]},
    {"Notification":{"NotificationType":"FORECASTED","ComparisonOperator":"GREATER_THAN","Threshold":100,"ThresholdType":"PERCENTAGE"},
     "Subscribers":[{"SubscriptionType":"EMAIL","Address":"you@example.com"}]}]'
```
Replace the email address. You can also do this in the console: **Billing → Budgets → Create budget →
Monthly cost budget**.

### B3. Create the GitHub OIDC role (one time)
```bash
aws cloudformation deploy --region us-east-1 \
  --stack-name voyage-ai-github-oidc \
  --template-file scripts/deploy/aws/bootstrap-oidc.yml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides GitHubOrg=Flynn-Jones GitHubRepo=voyage-ai
# If it fails because the provider already exists, add: CreateOidcProvider=false
aws cloudformation describe-stacks --stack-name voyage-ai-github-oidc \
  --query "Stacks[0].Outputs[?OutputKey=='DeployRoleArn'].OutputValue" --output text
```
The role trusts **only** `repo:Flynn-Jones/voyage-ai:ref:refs/heads/main`, which covers runs on main and
manual dispatches from main.

### B4. GitHub secret and variables
```bash
gh secret set AWS_ROLE_ARN --body "<DeployRoleArn from B3>"
gh variable set AWS_REGION        --body us-east-1
gh variable set USE_LAB_ROLE      --body false
gh variable set STACK_PREFIX      --body voyage-ai
gh variable set CLOUD_AUTO_DEPLOY --body false
gh variable set GATE_MODE         --body latest-main
```
Fallback if OIDC isn't possible: set `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` from a dedicated IAM user
with the same policy as `bootstrap-oidc.yml`, and leave `AWS_ROLE_ARN` unset. Rotate those keys after the release.

---

## Running a deployment

- **From GitHub (anyone with write access):** Actions → **cloud-deployment** → **Run workflow** → action
  `deploy`. The jobs run gate → validate → ECR push → deploy → smoke. Artifacts: `gate-table`,
  `deployment-outputs` (ALB URL, stack outputs) and `smoke-test-output`.
- **From a laptop:** `scripts/deploy/aws/deploy.sh --dry-run` first, then `deploy.sh` and `smoke-test.sh`.
- **Stop paying:** Run workflow → action `teardown`, or `scripts/deploy/aws/teardown.sh`.

### Gate mode (default `latest-main`)
The gate deploys only when **student-1..5 and integration-ci** are all green. There are two modes:

| Mode | Rule | When |
|---|---|---|
| **`latest-main`** (default for auto-deploys and the criterion-6 evidence run) | A workflow's run **for this commit** must be green. If its path filter skipped this commit, its **most recent completed run on main** must be green instead. | Now |
| `strict` | Every workflow must have a green run for **exactly this commit**. | Once every `student-N.yml` runs on every push to main (student-changes.md #4) |

**Why `latest-main` for now.** Each `student-N.yml` is path-filtered, so a commit touching one feature has no
run for the other four, and `strict` could never pass. `latest-main` still verifies every feature: each one's
most recent green run on main is checked, and a red latest run blocks the deploy. The gate table records, per
workflow, the **run id, SHA, conclusion and Source**: `current SHA` (ran for this commit) or
`latest green on main` (skipped by its path filter). The evidence therefore shows exactly which result each
feature was trusted on. integration-ci has no path filter, so it is always checked on the current SHA.

Choose `strict` per dispatch with the `gate_mode` input, or permanently with `gh variable set GATE_MODE
--body strict` once student change #4 is done. `skip_gate` (dispatch only) deploys anyway, still logs the
table, and is for emergencies only.

## Criterion 6 evidence (one controlled auto-deploy)
1. Credentials set up (A or B). `GATE_MODE` left at `latest-main`.
2. `gh variable set CLOUD_AUTO_DEPLOY --body true`
3. Merge a PR to main. Wait for student-N and integration-ci, then **cloud-deployment** starts on its own:
   gate → validate → ECR push → deploy → smoke.
4. Capture: the run URL, the job summary (gate table and ALB URL), and the three artifacts. Copy
   `smoke-test-output-*.txt` and `stack-outputs-*.json` into `docs/release-2/cloud/`, then fill in
   `cloud-deployment-report.md`.
5. `gh variable set CLOUD_AUTO_DEPLOY --body false`
6. Run the agentic-loop review and record the human decision:
   `python ai-services/agentic_loop/app_main.py --mode cloud-deployment-review --decision ...`
7. **Tear down**, unless the stack is needed for a demo.
