"""Offline tests for scripts/deploy/aws/provision.sh.

`aws` is replaced by a stub on PATH that records every call, so nothing touches a real
account. provision.sh is the restore path for a torn-down deployment, so the properties
that matter are: a dry run changes nothing, an apply creates exactly the resources
cloud-deployment.yml cannot create for itself, it is idempotent, and it never reports
success while having silently skipped the manifest-driven steps.

Run from the repo root: .venv/bin/python -m pytest scripts/deploy/aws/tests
"""
import json
import os
import subprocess
import sys

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "provision.sh")

# Stub aws: STUB_RULES is an ordered list of {"match", "stdout", "exit"}; first match on the
# joined argv wins. Everything else succeeds silently. Calls are appended to STUB_LOG.
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

VPC = "vpc-test"
SUBNETS = "subnet-a\tsubnet-b"

# A completely empty account: every lookup finds nothing.
EMPTY = [
    {"match": "ec2 describe-vpcs", "stdout": VPC + "\n"},
    {"match": "ec2 describe-subnets", "stdout": SUBNETS + "\n"},
    {"match": "ecr describe-repositories", "exit": 254},          # absent
    {"match": "logs describe-log-groups", "stdout": "\n"},        # none yet
    {"match": "ec2 describe-security-groups", "stdout": "None\n"},
    {"match": "ec2 create-security-group", "stdout": "sg-new\n"},
    {"match": "ecs describe-clusters", "stdout": "None\n"},
    {"match": "servicediscovery list-namespaces", "stdout": "\n"},
    # The ALB is reported present on purpose: creating it shells out to alb.py, which
    # expects real boto-shaped JSON this stub cannot provide. ALB/target-group/listener
    # planning is asserted through the dry run instead (see NO_ALB below).
    {"match": "elbv2 describe-load-balancers", "stdout": "arn:lb/alb\n"},
]

# Same, but with no ALB - only ever used for a DRY RUN, which never invokes alb.py.
NO_ALB = [{"match": "elbv2 describe-load-balancers", "stdout": "None\n"},
          *[r for r in EMPTY if "elbv2" not in r["match"]]]

# Everything already there (the state provision.sh saw against the live account).
FULL = [
    {"match": "ec2 describe-vpcs", "stdout": VPC + "\n"},
    {"match": "ec2 describe-subnets", "stdout": SUBNETS + "\n"},
    {"match": "ecr describe-repositories", "exit": 0},
    # note the TAB separators: `--output text` really does emit them
    {"match": "logs describe-log-groups",
     "stdout": "\t".join(f"/voyageai/{n}" for n in (
         "shared-frontend", "shared-api", "shared-database",
         "destination-frontend", "destination-api", "destination-database",
         "accommodation-frontend", "accommodation-api", "accommodation-database",
         "activity-frontend", "activity-api", "activity-database",
         "budget-fe", "budget-backend", "budget-database",
         "itinerary-fe", "itinerary-be", "itinerary-db")) + "\n"},
    {"match": "ec2 describe-security-groups", "stdout": "sg-existing\n"},
    {"match": "ecs describe-clusters", "stdout": "ACTIVE\n"},
    {"match": "servicediscovery list-namespaces", "stdout": "ns-existing\n"},
    {"match": "elbv2 describe-load-balancers", "stdout": "arn:lb/alb\n"},
]

MUTATIONS = ("create-repository", "create-log-group", "create-security-group",
             "authorize-security-group-ingress", "create-cluster",
             "create-private-dns-namespace")


def run_provision(tmp_path, *args, rules=EMPTY):
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
        "PYTHON": sys.executable,   # the test interpreter has PyYAML
        # the namespace poll is a real 5-minute wait against AWS; collapse it offline
        "PROVISION_NS_WAIT_TRIES": "1",
        "PROVISION_NS_WAIT_SLEEP": "0",
    }
    proc = subprocess.run(["/bin/bash", SCRIPT, *args], env=env,  # noqa: S603 - fixed argv, stubbed PATH
                          capture_output=True, text=True, timeout=120)
    return proc, log.read_text().splitlines()


def mutating(calls):
    return [c for c in calls if any(m in c for m in MUTATIONS)]


# ---- dry run is the default -------------------------------------------------

def test_dry_run_is_the_default_and_creates_nothing(tmp_path):
    proc, calls = run_provision(tmp_path)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert mutating(calls) == []
    assert "DRY RUN" in proc.stdout and "Re-run with --yes" in proc.stdout


def test_dry_run_still_looks_things_up_so_the_plan_is_real(tmp_path):
    proc, calls = run_provision(tmp_path)

    assert any("ecr describe-repositories" in c for c in calls)
    assert any("ec2 describe-vpcs" in c for c in calls)
    assert "DRYRUN create ecr repository voyageai/shared-frontend" in proc.stdout


# ---- the three things nothing else recreates --------------------------------

def test_apply_creates_all_18_ecr_repositories(tmp_path):
    """`docker push` does not create a repository - cloud-deployment.yml would fail."""
    _, calls = run_provision(tmp_path, "--yes")

    repos = [c.split("--repository-name ")[1].split()[0]
             for c in calls if "ecr create-repository" in c]
    assert len(repos) == 18
    assert "voyageai/shared-frontend" in repos and "voyageai/itinerary-db" in repos
    assert all(r.startswith("voyageai/") for r in repos)


def test_apply_creates_a_log_group_for_every_service(tmp_path):
    """taskdefs.py sets no awslogs-create-group and the exec role cannot create one, so a
    missing group means the task never starts."""
    _, calls = run_provision(tmp_path, "--yes")

    groups = [c.split("--log-group-name ")[1].split()[0]
              for c in calls if "logs create-log-group" in c]
    assert len(groups) == 18
    assert "/voyageai/budget-backend" in groups  # service name, not the ecr repo name
    assert all(g.startswith("/voyageai/") for g in groups)


def test_apply_creates_the_cluster_namespace_and_security_groups(tmp_path):
    _, calls = run_provision(tmp_path, "--yes")
    joined = "\n".join(calls)

    assert "ecs create-cluster --cluster-name voyageai-cluster" in joined
    assert "servicediscovery create-private-dns-namespace --name voyageai.local" in joined
    assert "ec2 create-security-group --group-name voyageai-alb-sg" in joined
    assert "ec2 create-security-group --group-name voyageai-task-sg" in joined
    # the mesh rule every Service Connect task depends on
    assert "--port 0-65535 --source-group" in joined


def test_frontend_ports_are_opened_on_the_alb_group(tmp_path):
    _, calls = run_provision(tmp_path, "--yes")

    opened = {c.split("--port ")[1].split()[0] for c in calls
              if "authorize-security-group-ingress" in c and "0.0.0.0/0" in c}
    assert opened == {"3000", "3001", "3002", "3003", "3004", "3005"}


def test_dry_run_plans_the_alb_with_a_listener_per_frontend(tmp_path):
    proc, calls = run_provision(tmp_path, rules=NO_ALB)

    assert "DRYRUN create voyageai-alb" in proc.stdout
    for name, cport, albport in (("shared-frontend", 3000, 3000),
                                 ("itinerary-fe", 80, 3005)):
        assert f"{name}  ALB :{albport} -> container :{cport}" in proc.stdout
    assert not any("elbv2 create" in c for c in calls)


# ---- idempotence ------------------------------------------------------------

def test_nothing_is_recreated_when_everything_exists(tmp_path):
    proc, calls = run_provision(tmp_path, "--yes", rules=FULL)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert mutating(calls) == [], "a re-run must not recreate existing resources"
    assert "0 created" in proc.stdout


def test_existing_log_groups_are_detected_despite_tab_separated_output(tmp_path):
    """`--output text` separates with TABS; a space-only match reports every group missing."""
    proc, calls = run_provision(tmp_path, "--yes", rules=FULL)

    assert not any("logs create-log-group" in c for c in calls)
    assert "exists  /voyageai/itinerary-db" in proc.stdout


# ---- failure handling -------------------------------------------------------

def test_a_missing_pyyaml_is_fatal_not_a_silent_skip(tmp_path):
    """The manifest loops run inside $(...), where `set -e` cannot see a failure - without
    the explicit guard the ECR and log-group steps would be skipped and still report OK."""
    proc, calls = run_provision(tmp_path, "--yes", rules=EMPTY)
    assert proc.returncode == 0  # sanity: the normal path works

    stubs = tmp_path / "nopy"
    stubs.mkdir()
    (stubs / "python-noyaml").write_text("#!/bin/sh\nexit 1\n")
    (stubs / "python-noyaml").chmod(0o755)
    env = {**os.environ, "PYTHON": str(stubs / "python-noyaml"),
           "STUB_LOG": str(tmp_path / "x.log"), "AWS_REGION": "ap-southeast-2"}
    (tmp_path / "x.log").write_text("")
    proc2 = subprocess.run(["/bin/bash", SCRIPT, "--yes"], env=env,  # noqa: S603 - fixed argv
                           capture_output=True, text=True, timeout=60)

    assert proc2.returncode == 1
    assert "FATAL" in proc2.stderr and "PyYAML" in proc2.stderr
    assert "create" not in proc2.stdout.lower() or "DRYRUN" not in proc2.stdout
    del calls


def test_no_default_vpc_stops_the_run(tmp_path):
    rules = [{"match": "ec2 describe-vpcs", "stdout": "None\n"}, *EMPTY[1:]]
    proc, _ = run_provision(tmp_path, "--yes", rules=rules)

    assert proc.returncode == 1
    assert "no default VPC" in proc.stderr


def test_a_create_failure_is_reported_and_exits_nonzero(tmp_path):
    rules = [{"match": "ecs create-cluster", "stderr": "AccessDeniedException", "exit": 254}, *EMPTY]
    proc, _ = run_provision(tmp_path, "--yes", rules=rules)

    assert proc.returncode == 1
    assert "FAILED  ecs cluster voyageai-cluster" in proc.stdout
    assert "idempotent" in proc.stdout or "idempotent" in proc.stderr


def test_an_already_exists_race_is_not_a_failure(tmp_path):
    rules = [{"match": "ecr create-repository", "stderr": "RepositoryAlreadyExistsException",
              "exit": 254}, *EMPTY]
    proc, _ = run_provision(tmp_path, "--yes", rules=rules)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "EXISTS" in proc.stdout


# ---- CLI --------------------------------------------------------------------

def test_unknown_option_exits_two(tmp_path):
    proc, calls = run_provision(tmp_path, "--delete-everything")
    assert proc.returncode == 2
    assert calls == []


def test_summary_names_the_remaining_manual_steps(tmp_path):
    """Provision stops before images/taskdefs/services - it must say what still has to run,
    or a restorer is left with infrastructure and no running fleet."""
    proc, _ = run_provision(tmp_path, "--yes")

    for expected in ("taskdefs.py", "services.py", "ai-ec2-setup.sh",
                     "ai-db-nlb-setup.sh", "ai-ec2-deploy.sh", "cloud-deployment.yml"):
        assert expected in proc.stdout, f"restore summary never mentions {expected}"


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_exits_zero_without_calling_aws(tmp_path, flag):
    proc, calls = run_provision(tmp_path, flag)
    assert proc.returncode == 0
    assert calls == []
    assert "--yes" in proc.stdout
