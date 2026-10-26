#!/bin/bash
set -e

# One-time, local-only: creates the AWS IAM OIDC identity provider + role that lets
# .github/workflows/cloud-deployment.yml authenticate to AWS with no stored access keys,
# then pushes the role ARN to GitHub Actions secrets.
#
# Mirrors Lab 11's gh-secrets-setup.sh (Azure OIDC app registration), adapted to AWS IAM.
# Re-syncs the trust policy's subject on every run (not just at creation), since the
# trusted branch is read from whatever branch is checked out when this script runs -
# re-run it from a different branch (or add a second statement to the role's trust
# policy) if the workflow should also be trustable from elsewhere.
#
# Repos created, renamed, or transferred after 2026-07-15 use GitHub's immutable OIDC
# subject format (owner/repo IDs instead of names: repo:OWNER@OWNER_ID/REPO@REPO_ID:ref:...)
# instead of the plain repo:OWNER/REPO:ref:... form - using the wrong one makes every
# AssumeRoleWithWebIdentity call fail with "Not authorized" with no further detail, so
# the IDs are fetched live from the GitHub API rather than assumed.

ROLE_NAME="voyageai-github-actions"
POLICY_NAME="voyageai-github-actions-deploy"
EXEC_ROLE_NAME="ecsTaskExecutionRole"

echo "=== VoyageAI GitHub OIDC Setup (AWS) ==="

if ! gh auth status >/dev/null 2>&1; then
    gh auth login
    echo "Logged in to GitHub."
fi

REPO_URL=$(git config --get remote.origin.url)
REPO_SLUG=$(echo "$REPO_URL" | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')
# The branch the deploy role will trust. Defaults to main - NOT the current branch:
# deriving it from HEAD silently re-pinned the shared role to whoever ran this last from a
# feature branch, which is exactly how a main dispatch came to fail with
# "Not authorized to perform sts:AssumeRoleWithWebIdentity". Override deliberately:
#   ./gh-oidc-setup.sh some-branch
BRANCH="${1:-main}"
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
EXEC_ROLE_ARN=$(aws iam get-role --role-name "$EXEC_ROLE_NAME" --query Role.Arn --output text)

OWNER_NAME=$(echo "$REPO_SLUG" | cut -d/ -f1)
REPO_NAME=$(echo "$REPO_SLUG" | cut -d/ -f2)
OWNER_ID=$(gh api "repos/${REPO_SLUG}" --jq '.owner.id')
REPO_ID=$(gh api "repos/${REPO_SLUG}" --jq '.id')
SUBJECT="repo:${OWNER_NAME}@${OWNER_ID}/${REPO_NAME}@${REPO_ID}:ref:refs/heads/${BRANCH}"

echo "Repo: $REPO_SLUG   Trusted branch: $BRANCH   Account: $AWS_ACCOUNT_ID"
if [ "$BRANCH" != "$(git rev-parse --abbrev-ref HEAD)" ]; then
    echo "NOTE: trusting '$BRANCH', not your checked-out branch $(git rev-parse --abbrev-ref HEAD) - cloud-deployment.yml must be dispatched from '$BRANCH'."
fi
echo "OIDC subject: $SUBJECT"

# Step 1: GitHub's OIDC identity provider (one per AWS account, shared by every repo/role)
OIDC_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"
if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$OIDC_ARN" >/dev/null 2>&1; then
    echo "GitHub OIDC provider already registered."
else
    aws iam create-open-id-connect-provider \
        --url "https://token.actions.githubusercontent.com" \
        --client-id-list "sts.amazonaws.com" \
        --thumbprint-list "6938fd4d98bab03faadb97b34396831e3780aea1"
    echo "Registered GitHub OIDC provider."
fi

# Step 2: the role the workflow assumes, trusting only this repo + branch
TRUST_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "$OIDC_ARN"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
      "StringLike": {"token.actions.githubusercontent.com:sub": "$SUBJECT"}
    }
  }]
}
EOF
)

if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
    aws iam update-assume-role-policy --role-name "$ROLE_NAME" --policy-document "$TRUST_POLICY"
    echo "Synced trust policy for existing role '$ROLE_NAME' (branch: $BRANCH)."
else
    aws iam create-role --role-name "$ROLE_NAME" --assume-role-policy-document "$TRUST_POLICY"
    echo "Created role '$ROLE_NAME'."
fi

# Step 3: least-privilege deploy policy - only what cloud-deployment.yml's steps use:
# push images, register/roll out task definitions, and read ALB/service state for the
# smoke test. Deliberately excludes anything that creates or deletes infrastructure
# (ECR repos, the cluster, the ALB, security groups) - those are one-time, human-run steps.
DEPLOY_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EcrAuth",
      "Effect": "Allow",
      "Action": "ecr:GetAuthorizationToken",
      "Resource": "*"
    },
    {
      "Sid": "EcrPushPull",
      "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage",
        "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage"
      ],
      "Resource": "arn:aws:ecr:*:${AWS_ACCOUNT_ID}:repository/voyageai/*"
    },
    {
      "Sid": "EcsDeploy",
      "Effect": "Allow",
      "Action": [
        "ecs:RegisterTaskDefinition", "ecs:DescribeTaskDefinition",
        "ecs:UpdateService", "ecs:DescribeServices", "ecs:ListServices"
      ],
      "Resource": "*"
    },
    {
      "Sid": "PassExecutionRoleOnly",
      "Effect": "Allow",
      "Action": ["iam:PassRole", "iam:GetRole"],
      "Resource": "$EXEC_ROLE_ARN"
    },
    {
      "Sid": "ReadAlbForSmokeTest",
      "Effect": "Allow",
      "Action": ["elasticloadbalancing:DescribeLoadBalancers", "elasticloadbalancing:DescribeTargetGroups",
                 "elasticloadbalancing:DescribeTargetHealth"],
      "Resource": "*"
    },
    {
      "Sid": "LogsForTaskDefs",
      "Effect": "Allow",
      "Action": ["logs:CreateLogGroup", "logs:DescribeLogGroups"],
      "Resource": "*"
    }
  ]
}
EOF
)

aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name "$POLICY_NAME" --policy-document "$DEPLOY_POLICY"
echo "Attached/updated inline deploy policy '$POLICY_NAME'."

ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text)
gh secret set AWS_ROLE_ARN --body "$ROLE_ARN"
echo "Pushed AWS_ROLE_ARN to GitHub Actions secrets."

echo "=== GitHub OIDC Setup Complete ==="
echo "Role: $ROLE_ARN"
echo "Trusted subject: $SUBJECT"
