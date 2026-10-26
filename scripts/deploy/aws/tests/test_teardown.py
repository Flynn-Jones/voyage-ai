"""Offline tests for scripts/deploy/aws/teardown.sh.

`aws` is replaced by a stub on PATH that records every invocation and answers lookups
from a rule list, so nothing touches a real account. The stub's default answer is
"succeeded, printed nothing", which is what an absent resource looks like to the
script's lookups, so ABSENT_ALL needs no rules at all.

Run from the repo root: .venv/bin/python -m pytest scripts/deploy/aws/tests
"""
import json
import os
import subprocess
import sys

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "teardown.sh")

# Stub aws: STUB_RULES is an ordered JSON list of {"match", "stdout", "exit"}; the first
# rule whose "match" appears in the joined argv wins. Every call is appended to STUB_LOG.
AWS_STUB = f"""#!{sys.executable}
import json, os, sys
line = " ".join(sys.argv[1:])
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(line + "\\n")
for rule in json.loads(os.environ.get("STUB_RULES", "[]")):
    if rule["match"] in line:
        sys.stdout.write(rule.get("stdout", ""))
        sys.stderr.write(rule.get("stderr", ""))
        sys.exit(rule.get("exit", 0))
sys.exit(0)
"""

SERVICES = ["shared-frontend", "budget-be"]

# Every project resource present and discoverable.
FULL = [
    {"match": "ecs list-services",
     "stdout": "\t".join(f"arn:aws:ecs:r:1:service/voyageai-cluster/{s}" for s in SERVICES)},
    *[{"match": f"ecs describe-services --cluster voyageai-cluster --services {s}",
       "stdout": f"arn:aws:ecs:r:1:task-definition/{s}:3"} for s in SERVICES],
    *[{"match": f"ecs list-task-definitions --family-prefix {s}",
       "stdout": f"arn:aws:ecs:r:1:task-definition/{s}:3"} for s in SERVICES],
    {"match": "elbv2 describe-load-balancers --names voyageai-alb", "stdout": "arn:lb/alb"},
    {"match": "elbv2 describe-load-balancers --names voyageai-db-nlb", "stdout": "arn:lb/nlb"},
    {"match": "elbv2 describe-target-groups --load-balancer-arn arn:lb/alb",
     "stdout": "arn:tg/tg-shared-frontend"},
    {"match": "elbv2 describe-target-groups --load-balancer-arn arn:lb/nlb",
     "stdout": "arn:tg/tg-budget-database"},
    {"match": "elbv2 describe-listeners --load-balancer-arn arn:lb/alb", "stdout": "arn:listener/alb-3000"},
    {"match": "elbv2 describe-listeners --load-balancer-arn arn:lb/nlb", "stdout": "arn:listener/nlb-6004"},
    {"match": "servicediscovery list-namespaces", "stdout": "ns-abc123"},
    {"match": "logs describe-log-groups",
     "stdout": "/voyageai/shared-frontend\t/voyageai/budget-be"},
    {"match": "ec2 describe-instances", "stdout": "i-aihost"},
    *[{"match": f"Name=group-name,Values={sg}", "stdout": f"sg-{sg}"}
      for sg in ("voyageai-task-sg", "voyageai-alb-sg", "voyageai-db-nlb-sg", "voyageai-ai-host-sg")],
    # One project repo and one that belongs to something else entirely.
    {"match": "ecr describe-repositories",
     "stdout": "voyageai/shared-frontend\tsomeone-elses-app"},
    {"match": "sts get-caller-identity", "stdout": "123456789012"},
    {"match": "s3api head-bucket", "exit": 0},
    {"match": "iam get-instance-profile", "exit": 0},
    {"match": "iam list-attached-role-policies",
     "stdout": "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"},
    {"match": "iam list-role-policies", "stdout": "read-code-bundle"},
    {"match": "iam get-role", "exit": 0},
    {"match": "ssm get-parameters-by-path",
     "stdout": "/voyageai/ai-host/instance-id\t/voyageai/db-nlb/dns-name"},
]

# Nothing exists: lookups print nothing, and the three exit-code probes say "absent".
ABSENT_ALL = [
    {"match": "s3api head-bucket", "exit": 254},
    {"match": "iam get-instance-profile", "exit": 254},
    {"match": "iam get-role", "exit": 254},
]

MUTATIONS = ("delete-", "deregister-", "terminate-instances", "update-service",
             "remove-role-from-instance-profile", "detach-role-policy", "s3 rm")


def run_teardown(tmp_path, *args, rules=FULL, extra_env=None):
    stubs = tmp_path / "stubs"
    stubs.mkdir(parents=True)
    (stubs / "aws").write_text(AWS_STUB)
    (stubs / "aws").chmod(0o755)
    log = tmp_path / "aws-calls.log"
    log.write_text("")
    env = {
        **os.environ,
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "STUB_RULES": json.dumps(rules),
        "STUB_LOG": str(log),
        "AWS_REGION": "ap-southeast-2",
        "TEARDOWN_RETRY_SLEEP": "0",
        **(extra_env or {}),
    }
    proc = subprocess.run(["/bin/bash", SCRIPT, *args], env=env,  # noqa: S603 - fixed argv, stubbed PATH
                          capture_output=True, text=True, timeout=120)
    return proc, log.read_text().splitlines()


def mutating(calls):
    return [c for c in calls if any(m in c for m in MUTATIONS)]


def index_of(calls, needle):
    """Index of the first call containing needle; -1 when it never happened."""
    return next((i for i, c in enumerate(calls) if needle in c), -1)


# ---- dry run is the default and changes nothing ------------------------------

def test_dry_run_is_the_default_and_makes_no_mutating_call(tmp_path):
    proc, calls = run_teardown(tmp_path)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert mutating(calls) == []
    assert "DRY RUN" in proc.stdout and "DELETED" not in proc.stdout
    assert "Re-run with --yes" in proc.stdout


def test_dry_run_still_discovers_so_the_plan_is_real(tmp_path):
    proc, calls = run_teardown(tmp_path)

    assert index_of(calls, "ecs list-services") >= 0        # lookups do run
    assert "DRYRUN ecs service shared-frontend" in proc.stdout
    assert "DRYRUN load balancer voyageai-alb" in proc.stdout
    assert "DRYRUN ec2 instance(s) i-aihost" in proc.stdout


def test_explicit_dry_run_flag_overrides_yes(tmp_path):
    proc, calls = run_teardown(tmp_path, "--yes", "--dry-run")

    assert proc.returncode == 0
    assert mutating(calls) == []


# ---- applying deletes everything, in a safe order ---------------------------

def test_apply_deletes_every_resource_class(tmp_path):
    proc, calls = run_teardown(tmp_path, "--yes")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    joined = "\n".join(calls)
    for expected in [
        "ecs update-service --cluster voyageai-cluster --service shared-frontend --desired-count 0",
        "ecs delete-service --cluster voyageai-cluster --service shared-frontend --force",
        "elbv2 delete-listener --listener-arn arn:listener/alb-3000",
        "elbv2 delete-load-balancer --load-balancer-arn arn:lb/alb",
        "elbv2 delete-target-group --target-group-arn arn:tg/tg-shared-frontend",
        "elbv2 delete-load-balancer --load-balancer-arn arn:lb/nlb",
        "ecs delete-cluster --cluster voyageai-cluster",
        "servicediscovery delete-namespace --id ns-abc123",
        "ecs deregister-task-definition --task-definition arn:aws:ecs:r:1:task-definition/shared-frontend:3",
        "logs delete-log-group --log-group-name /voyageai/shared-frontend",
        "ec2 terminate-instances --instance-ids i-aihost",
        "ec2 delete-security-group --group-id sg-voyageai-task-sg",
        "ecr delete-repository --repository-name voyageai/shared-frontend --force",
        "iam remove-role-from-instance-profile",
        "iam delete-role --role-name voyageai-ai-host-role",
        "s3 rm s3://voyageai-ai-bundle-123456789012 --recursive",
        "s3api delete-bucket --bucket voyageai-ai-bundle-123456789012",
        "ssm delete-parameter --name /voyageai/ai-host/instance-id",
    ]:
        assert expected in joined, f"missing: {expected}"
    assert "Teardown complete" in proc.stdout


def test_order_services_drain_before_load_balancers_and_groups_go(tmp_path):
    _, calls = run_teardown(tmp_path, "--yes")

    scale_to_zero = index_of(calls, "--desired-count 0")
    delete_service = index_of(calls, "ecs delete-service")
    wait_inactive = index_of(calls, "ecs wait services-inactive")
    delete_sg = index_of(calls, "ec2 delete-security-group")
    assert -1 < scale_to_zero < delete_service < wait_inactive < delete_sg


def test_order_listener_then_load_balancer_then_target_group(tmp_path):
    _, calls = run_teardown(tmp_path, "--yes")

    listener = index_of(calls, "elbv2 delete-listener --listener-arn arn:listener/alb-3000")
    lb = index_of(calls, "elbv2 delete-load-balancer --load-balancer-arn arn:lb/alb")
    wait = index_of(calls, "elbv2 wait load-balancers-deleted --load-balancer-arns arn:lb/alb")
    tg = index_of(calls, "elbv2 delete-target-group --target-group-arn arn:tg/tg-shared-frontend")
    assert -1 < listener < lb < wait < tg


def test_order_instance_terminated_before_its_security_group(tmp_path):
    # The AI host's ENI holds voyageai-ai-host-sg; deleting the group first always fails.
    _, calls = run_teardown(tmp_path, "--yes")

    terminate = index_of(calls, "ec2 terminate-instances")
    wait = index_of(calls, "ec2 wait instance-terminated")
    sg = index_of(calls, "ec2 delete-security-group --group-id sg-voyageai-ai-host-sg")
    assert -1 < terminate < wait < sg


def test_task_definition_families_come_from_the_services_not_a_pattern(tmp_path):
    _, calls = run_teardown(tmp_path, "--yes")

    # The family is read off each service before it is deleted, or it could not be found.
    describe = index_of(calls, "ecs describe-services --cluster voyageai-cluster --services shared-frontend")
    delete = index_of(calls, "ecs delete-service --cluster voyageai-cluster --service shared-frontend")
    assert -1 < describe < delete


# ---- scoping: nothing outside the project is ever touched -------------------

def test_only_project_prefixed_ecr_repositories_are_deleted(tmp_path):
    _, calls = run_teardown(tmp_path, "--yes")

    assert any("ecr delete-repository --repository-name voyageai/shared-frontend" in c for c in calls)
    assert not any("someone-elses-app" in c and "delete" in c for c in calls)


def test_the_oidc_provider_and_exec_role_are_never_deleted(tmp_path):
    _, calls = run_teardown(tmp_path, "--yes", "--include-oidc")

    assert not any("delete-open-id-connect-provider" in c for c in calls)
    assert not any("ecsTaskExecutionRole" in c and "delete" in c for c in calls)


def test_no_vpc_or_subnet_is_ever_deleted(tmp_path):
    _, calls = run_teardown(tmp_path, "--yes")

    assert not any(("delete-vpc" in c or "delete-subnet" in c) for c in calls)


# ---- idempotence ------------------------------------------------------------

def test_everything_absent_is_a_clean_success(tmp_path):
    proc, calls = run_teardown(tmp_path, "--yes", rules=ABSENT_ALL)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "none found" in proc.stdout
    assert "FAILED" not in proc.stdout
    assert not any("delete-service" in c for c in calls)        # nothing to scale or delete
    assert not any("terminate-instances" in c for c in calls)


def test_an_already_absent_resource_mid_run_is_not_a_failure(tmp_path):
    # The cluster vanished between discovery and deletion (a teammate ran teardown too).
    rules = [{"match": "ecs delete-cluster", "stderr": "ClusterNotFoundException: not found", "exit": 254}, *FULL]
    proc, _ = run_teardown(tmp_path, "--yes", rules=rules)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ABSENT  ecs cluster voyageai-cluster" in proc.stdout


def test_a_real_failure_exits_nonzero_but_still_finishes_the_rest(tmp_path):
    rules = [{"match": "logs delete-log-group", "stderr": "AccessDeniedException: nope", "exit": 254}, *FULL]
    proc, calls = run_teardown(tmp_path, "--yes", rules=rules)

    assert proc.returncode == 1
    assert "FAILED  log group /voyageai/shared-frontend" in proc.stdout
    # later phases still ran, so nothing billable is left behind by an early error
    assert any("ec2 terminate-instances" in c for c in calls)
    assert any("ssm delete-parameter" in c for c in calls)


def test_a_stuck_waiter_does_not_abort_the_teardown(tmp_path):
    rules = [{"match": "ecs wait services-inactive", "exit": 255}, *FULL]
    proc, calls = run_teardown(tmp_path, "--yes", rules=rules)

    assert "stopped waiting" in proc.stdout
    assert any("ec2 terminate-instances" in c for c in calls)
    assert proc.returncode == 0


# ---- security groups retry ---------------------------------------------------

def test_security_group_still_in_use_is_retried_then_reported(tmp_path):
    rules = [{"match": "ec2 delete-security-group --group-id sg-voyageai-task-sg",
              "stderr": "DependencyViolation", "exit": 254}, *FULL]
    proc, calls = run_teardown(tmp_path, "--yes", rules=rules)

    attempts = [c for c in calls if "delete-security-group --group-id sg-voyageai-task-sg" in c]
    assert len(attempts) == 3  # three passes, each clearing dependencies for the next
    assert proc.returncode == 1
    assert "voyageai-task-sg is still in use" in proc.stdout
    # the other groups were not held back by it
    assert any("delete-security-group --group-id sg-voyageai-alb-sg" in c for c in calls)


# ---- keep flags --------------------------------------------------------------

@pytest.mark.parametrize("flag, forbidden, note", [
    ("--keep-ecr", "ecr delete-repository", "images kept"),
    ("--keep-s3", "s3api delete-bucket", "left in place"),
    ("--keep-iam", "iam delete-role", "left in place"),
    ("--keep-ai-host", "ec2 terminate-instances", "left running"),
])
def test_keep_flags_skip_exactly_their_phase(tmp_path, flag, forbidden, note):
    proc, calls = run_teardown(tmp_path, "--yes", flag)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not any(forbidden in c for c in calls)
    assert note in proc.stdout
    assert any("ecs delete-service" in c for c in calls)  # the rest still ran


def test_github_actions_role_is_kept_unless_opted_in(tmp_path):
    proc, calls = run_teardown(tmp_path, "--yes")

    assert not any("voyageai-github-actions" in c and "delete" in c for c in calls)
    assert "Pass --include-oidc" in proc.stdout


def test_include_oidc_deletes_the_role_and_its_inline_policy(tmp_path):
    _, calls = run_teardown(tmp_path, "--yes", "--include-oidc")

    joined = "\n".join(calls)
    assert "iam delete-role-policy --role-name voyageai-github-actions --policy-name voyageai-github-actions-deploy" in joined
    assert "iam delete-role --role-name voyageai-github-actions" in joined


# ---- CLI surface -------------------------------------------------------------

def test_region_flag_is_passed_to_every_call(tmp_path):
    _, calls = run_teardown(tmp_path, "--yes", "--region", "us-east-1")

    regional = [c for c in calls if "--region" in c]
    assert regional and all("--region us-east-1" in c for c in regional)


def test_unknown_option_exits_two_without_calling_aws(tmp_path):
    proc, calls = run_teardown(tmp_path, "--delete-everything-now")

    assert proc.returncode == 2
    assert calls == []
    assert "unknown option" in proc.stderr


def test_help_exits_zero_without_calling_aws(tmp_path):
    proc, calls = run_teardown(tmp_path, "--help")

    assert proc.returncode == 0
    assert calls == []
    assert "--keep-ecr" in proc.stdout


# ---- more than ten services (the ecs waiter's limit) ------------------------

def test_services_are_waited_on_in_chunks_of_ten(tmp_path):
    names = [f"svc-{i:02d}" for i in range(12)]
    rules = [
        {"match": "ecs list-services",
         "stdout": "\t".join(f"arn:aws:ecs:r:1:service/voyageai-cluster/{n}" for n in names)},
        *FULL[1:],
    ]
    proc, calls = run_teardown(tmp_path, "--yes", rules=rules)

    waits = [c for c in calls if "ecs wait services-inactive" in c]
    assert len(waits) == 2                                  # 10 + 2, never one 12-service call
    assert len(waits[0].split("--services ")[1].split(" --region")[0].split()) == 10
    assert len(waits[1].split("--services ")[1].split(" --region")[0].split()) == 2
    assert proc.returncode == 0, proc.stdout + proc.stderr
