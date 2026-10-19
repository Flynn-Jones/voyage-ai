"""Single source of truth for the AWS ECS Fargate deployment.

Every image, ECS service, port, environment variable, Service Connect name and
ALB listener lives here. The CloudFormation templates `ecr.yml` and
`services.yml` are GENERATED from this file (do not hand-edit them):

    python scripts/deploy/aws/manifest.py generate     # rewrite ecr.yml + services.yml (needs PyYAML)
    python scripts/deploy/aws/manifest.py check        # exit 1 if they are out of date
    python scripts/deploy/aws/manifest.py images       # "<image> <compose-service|->" lines for deploy.sh
    python scripts/deploy/aws/manifest.py services     # ECS service names (for waits / smoke / teardown)
    python scripts/deploy/aws/manifest.py listeners    # "<alb-port> <frontend>" lines for smoke-test.sh

Names and ports mirror the root docker-compose.yml so containers find each other
under the SAME hostname:port in the cloud (Service Connect) as they do locally.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Release 2 rule: AI-Mode, MCP, RAG and the Multi-Agent Server are disabled in the cloud.
AI_OFF = {"MCP_ENABLED": "false", "RAG_ENABLED": "false", "MULTI_AGENT_ENABLED": "false", "AI_ENABLED": "false"}
# Any AI call a backend still makes fails fast on an unresolvable name instead of hanging
# on host.docker.internal (which does not exist on Fargate).
NO_AI_HOST = "http://ai-disabled.invalid"

# image name -> root docker-compose.yml service it is built from (None = built by deploy.sh itself)
IMAGES = {
    "shared-frontend": "shared-frontend",
    "shared-backend": "shared-backend",
    "shared-database": "shared-database",
    "destination-frontend": "destination-frontend",
    "destination-backend": "destination-backend",
    "destination-database": "destination-database",
    "accommodation-frontend": "accommodation-frontend",
    "accommodation-backend": "accommodation-backend",
    "accommodation-database": "accommodation-database",
    "activity-frontend": "activity-frontend",
    "activity-backend": "activity-backend",
    "activity-database": "activity-database",
    "budget-frontend": "budget-frontend",
    "budget-backend": "budget-backend",
    "budget-database": "budget-database",
    "itinerary-frontend": "itinerary-frontend",
    "itinerary-backend": "itinerary-backend",
    "itinerary-database": "itinerary-database",
    "smoke-runner": None,  # scripts/deploy/aws/smoke-runner/
}


def py_health(port, path):
    """Container health check without curl (the python images do not ship it)."""
    return f"python -c \"import urllib.request; urllib.request.urlopen('http://localhost:{port}{path}', timeout=3)\""


def container(image, port, env=None, health=None, server=None, depends_on=None, data=False):
    """server: Service Connect name other services use (compose service name), or None for client-only."""
    return {"image": image, "port": port, "env": env or {}, "health": health, "server": server,
            "depends_on": depends_on, "data": data}


def frontend(name, image, port, listeners, env=None, hc_path="/"):
    return {"name": name, "containers": [container(image, port, env)], "public": listeners,
            "target": (image, port), "hc_path": hc_path}


def internal(name, *containers, desired=1, cpu=None, memory=None):
    return {"name": name, "containers": list(containers), "public": [], "desired": desired,
            "cpu": cpu, "memory": memory}


SERVICES = [
    # ---------------- Shared (home page) ----------------
    frontend("shared-frontend", "shared-frontend", 3000, [80, 3000],
             {"ACCESS_SERVICE_URL": "http://shared-backend:5000"}),
    internal("shared-backend", container(
        "shared-backend", 5000, {"PORT": "5000", "DATABASE_SERVICE_URL": "http://shared-database:6000"},
        health=py_health(5000, "/health"), server="shared-backend")),
    internal("shared-database", container(
        "shared-database", 6000, {"DB_FILE": "/data/shared-db.sqlite"},
        health=py_health(6000, "/health"), server="shared-database", data=True)),

    # ---------------- Destination (student-1) ----------------
    frontend("destination-frontend", "destination-frontend", 3001, [3001], {
        "PORT": "3001", "BACKEND_SERVICE_URL": "http://destination-backend:5001",
        "MCP_REQUEST_TIMEOUT_SECONDS": "15", "RAG_REQUEST_TIMEOUT_SECONDS": "130"}, hc_path="/health"),
    internal("destination-backend", container("destination-backend", 5001, {
        "PORT": "5001", "DATABASE_SERVICE_URL": "http://destination-database:6001",
        "OLLAMA_BASE_URL": f"{NO_AI_HOST}:11434", "OLLAMA_MODEL": "qwen2.5:7b", "OLLAMA_TIMEOUT_SECONDS": "5",
        "MCP_SERVICE_URL": f"{NO_AI_HOST}:7001", "MCP_TIMEOUT_SECONDS": "5",
        "RAG_SERVICE_URL": f"{NO_AI_HOST}:7002", "RAG_TIMEOUT_SECONDS": "5", **AI_OFF},
        health=py_health(5001, "/health"), server="destination-backend")),
    internal("destination-database", container(
        "destination-database", 6001, {"PORT": "6001", "DB_FILE": "/data/destination-db.sqlite"},
        health=py_health(6001, "/health"), server="destination-database", data=True)),

    # ---------------- Accommodation ----------------
    frontend("accommodation-frontend", "accommodation-frontend", 3002, [3002], {
        "PORT": "3002", "BACKEND_SERVICE_URL": "http://accommodation-backend:5002"}, hc_path="/health"),
    internal("accommodation-backend", container("accommodation-backend", 5002, {
        "PORT": "5002", "DATABASE_SERVICE_URL": "http://accommodation-database:6002",
        "DESTINATION_SERVICE_URL": "http://destination-database:6001",
        "OLLAMA_HOST": f"{NO_AI_HOST}:11434", "OLLAMA_MODEL": "qwen2.5:7b",
        "MCP_SERVICE_URL": f"{NO_AI_HOST}:7001", "RAG_SERVICE_URL": f"{NO_AI_HOST}:7002", **AI_OFF},
        health=py_health(5002, "/health"), server="accommodation-backend")),
    internal("accommodation-database", container(
        "accommodation-database", 6002, {"PORT": "6002", "DB_FILE": "/data/accommodation-db.sqlite"},
        health=py_health(6002, "/health"), server="accommodation-database", data=True)),

    # ---------------- Activity ----------------
    # The frontend container listens on 3004; the ALB exposes it on 3003 like the root compose file.
    frontend("activity-frontend", "activity-frontend", 3004, [3003],
             {"BACKEND_SERVICE_URL": "http://activity-backend:5003"}, hc_path="/health"),
    # activity-backend opens the database's SQLite file directly (a data-path violation documented
    # in docker-compose.yml), so both containers run in ONE task sharing /data. No code change.
    internal("activity", container(
        "activity-database", 6003, {"ACTIVITY_DB_PATH": "/data/database-service.sqlite"},
        health=py_health(6003, "/health"), server="activity-database", data=True),
        container("activity-backend", 5003, {
            "DATABASE_SERVICE_URL": "http://activity-database:6003",
            "ACCOMMODATION_SERVICE_URL": "http://accommodation-database:6002",
            "ACTIVITY_DB_PATH": "/data/database-service.sqlite",
            "OLLAMA_URL": f"{NO_AI_HOST}:11434", "OLLAMA_MODEL": "qwen2.5:7b",
            "OLLAMA_INTENT_MODEL": "qwen2.5:7b",
            "MCP_SERVICE_URL": f"{NO_AI_HOST}:7001", "RAG_SERVICE_URL": f"{NO_AI_HOST}:7002", **AI_OFF},
            health=py_health(5003, "/health"), server="activity-backend",
            depends_on="activity-database", data=True),
        cpu=512, memory=1024),

    # ---------------- Budget ----------------
    frontend("budget-frontend", "budget-frontend", 80, [3004]),
    # budget-be and budget-db have no /health route yet (see student-changes.md); "/" and "/expenses" exist.
    internal("budget-backend", container("budget-backend", 5004, {
        "PORT": "5004", "DATABASE_SERVICE_URL": "http://budget-database:6004",
        "ACCOMMODATION_SERVICE_URL": "http://accommodation-database:6002",
        "OLLAMA_BASE_URL": f"{NO_AI_HOST}:11434", "OLLAMA_MODEL": "qwen2.5:7b",
        "MCP_SERVICE_URL": f"{NO_AI_HOST}:7001", "RAG_SERVICE_URL": f"{NO_AI_HOST}:7002", **AI_OFF},
        health=py_health(5004, "/"), server="budget-backend")),
    internal("budget-database", container(
        "budget-database", 6004, {"DB_FILE": "/data/budget-db.sqlite"},
        health=py_health(6004, "/expenses"), server="budget-database", data=True)),

    # ---------------- Itinerary ----------------
    frontend("itinerary-frontend", "itinerary-frontend", 80, [3005]),
    # itinerary-fe's nginx.conf hardcodes proxy_pass http://itinerary-be:5005 (compose network alias).
    # Service Connect registers that alias, which is the only name any client uses.
    internal("itinerary-backend", container("itinerary-backend", 5005, {
        "PORT": "5005", "DATABASE_SERVICE_URL": "http://itinerary-database:6005",
        "DESTINATION_SERVICE_URL": "http://destination-database:6001",
        "ACTIVITY_SERVICE_URL": "http://activity-database:6003",
        "OLLAMA_BASE_URL": f"{NO_AI_HOST}:11434", "OLLAMA_MODEL": "qwen2.5:7b", "OLLAMA_TIMEOUT_SECONDS": "5",
        "MCP_SERVICE_URL": f"{NO_AI_HOST}:7001", "MCP_TIMEOUT_SECONDS": "5",
        "RAG_SERVICE_URL": f"{NO_AI_HOST}:7002", "RAG_TIMEOUT_SECONDS": "5", **AI_OFF},
        health=py_health(5005, "/health"), server="itinerary-be")),
    internal("itinerary-database", container(
        "itinerary-database", 6005, {"PORT": "6005", "DB_FILE": "/data/itinerary-db.sqlite"},
        health=py_health(6005, "/health"), server="itinerary-database", data=True)),

    # ---------------- Smoke runner (in-VPC; scaled 0 -> 1 -> 0 by smoke-test.sh) ----------------
    internal("smoke-runner", container("smoke-runner", 8080, {"SMOKE_IDLE_SECONDS": "3600"}), desired=0),
]


# ------------------------------------------------------------------ helpers
def service_names():
    return [s["name"] for s in SERVICES]


def listeners():
    """[(alb_port, frontend_service_name)]"""
    return [(port, s["name"]) for s in SERVICES for port in s["public"]]


def cfn_id(name: str) -> str:
    return "".join(part.capitalize() for part in name.replace("_", "-").split("-"))


# ------------------------------------------------------------------ generation
def _ecr_template():
    resources = {}
    outputs = {}
    for image in IMAGES:
        rid = f"Repo{cfn_id(image)}"
        resources[rid] = {
            "Type": "AWS::ECR::Repository",
            "Properties": {
                "RepositoryName": {"Fn::Sub": f"${{StackPrefix}}/{image}"},
                "ImageScanningConfiguration": {"ScanOnPush": True},
                "ImageTagMutability": "MUTABLE",
                "EmptyOnDelete": True,
                "LifecyclePolicy": {"LifecyclePolicyText": (
                    '{"rules":[{"rulePriority":1,"description":"keep the last 10 images",'
                    '"selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":10},'
                    '"action":{"type":"expire"}}]}')},
            },
        }
        outputs[rid] = {"Value": {"Fn::GetAtt": [rid, "RepositoryUri"]}}
    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": "VoyageAI ECR repositories, one per image, scan on push (GENERATED by manifest.py)",
        "Parameters": {"StackPrefix": {"Type": "String", "Default": "voyage-ai",
                                       "AllowedPattern": "^[a-z][a-z0-9-]{1,30}$"}},
        "Resources": resources,
        "Outputs": outputs,
    }


def _imp(name):
    return {"Fn::ImportValue": {"Fn::Sub": f"${{StackPrefix}}-{name}"}}


def _container_def(c, prefix_log):
    env = [{"Name": k, "Value": v} for k, v in c["env"].items()]
    port_mapping = {"ContainerPort": c["port"], "Protocol": "tcp", "Name": c["image"]}
    if c["server"]:
        port_mapping["AppProtocol"] = "http"
    definition = {
        "Name": c["image"],
        "Image": {"Fn::Sub": f"${{AWS::AccountId}}.dkr.ecr.${{AWS::Region}}.amazonaws.com/${{StackPrefix}}/{c['image']}:${{ImageTag}}"},
        "Essential": True,
        "PortMappings": [port_mapping],
        "Environment": env,
        "LogConfiguration": {"LogDriver": "awslogs", "Options": {
            "awslogs-group": _imp("log-group"), "awslogs-region": {"Ref": "AWS::Region"},
            "awslogs-stream-prefix": prefix_log}},
    }
    if c["health"]:
        definition["HealthCheck"] = {"Command": ["CMD-SHELL", c["health"]], "Interval": 15, "Timeout": 5,
                                     "Retries": 5, "StartPeriod": 30}
    if c["data"]:
        definition["MountPoints"] = [{"SourceVolume": "data", "ContainerPath": "/data", "ReadOnly": False}]
    if c["depends_on"]:
        definition["DependsOn"] = [{"ContainerName": c["depends_on"], "Condition": "HEALTHY"}]
    return definition


def _services_template():
    params = {
        "StackPrefix": {"Type": "String", "Default": "voyage-ai", "AllowedPattern": "^[a-z][a-z0-9-]{1,30}$"},
        "ImageTag": {"Type": "String", "Description": "Image tag pushed by deploy.sh (git SHA)"},
        "TaskCpu": {"Type": "Number", "Default": 256, "AllowedValues": [256, 512, 1024]},
        "TaskMemory": {"Type": "Number", "Default": 512, "AllowedValues": [512, 1024, 2048]},
        "EnableEfs": {"Type": "String", "Default": "false", "AllowedValues": ["true", "false"],
                      "Description": "Persist SQLite on EFS (must match the cluster stack). Default: ephemeral, reseeded on start."},
    }
    resources = {}
    outputs = {}
    conditions = {"UseEfs": {"Fn::Equals": [{"Ref": "EnableEfs"}, "true"]}}

    for svc in SERVICES:
        sid = cfn_id(svc["name"])
        has_data = any(c["data"] for c in svc["containers"])
        volume = None
        if has_data:
            ap = f"AccessPoint{sid}"
            resources[ap] = {
                "Type": "AWS::EFS::AccessPoint", "Condition": "UseEfs",
                "Properties": {
                    "FileSystemId": _imp("efs-id"),
                    "PosixUser": {"Uid": "0", "Gid": "0"},
                    "RootDirectory": {"Path": f"/{svc['name']}",
                                      "CreationInfo": {"OwnerUid": "0", "OwnerGid": "0", "Permissions": "0755"}},
                },
            }
            volume = {"Fn::If": ["UseEfs",
                                 {"Name": "data", "EFSVolumeConfiguration": {
                                     "FilesystemId": _imp("efs-id"), "TransitEncryption": "ENABLED",
                                     "AuthorizationConfig": {"AccessPointId": {"Ref": ap}, "IAM": "DISABLED"}}},
                                 {"Name": "data"}]}

        task = f"Task{sid}"
        resources[task] = {
            "Type": "AWS::ECS::TaskDefinition",
            "Properties": {
                "Family": {"Fn::Sub": f"${{StackPrefix}}-{svc['name']}"},
                "RequiresCompatibilities": ["FARGATE"],
                "NetworkMode": "awsvpc",
                "Cpu": str(svc["cpu"]) if svc.get("cpu") else {"Ref": "TaskCpu"},
                "Memory": str(svc["memory"]) if svc.get("memory") else {"Ref": "TaskMemory"},
                "ExecutionRoleArn": _imp("execution-role-arn"),
                "TaskRoleArn": _imp("task-role-arn"),
                "RuntimePlatform": {"OperatingSystemFamily": "LINUX", "CpuArchitecture": "X86_64"},
                "ContainerDefinitions": [_container_def(c, svc["name"]) for c in svc["containers"]],
                **({"Volumes": [volume]} if volume else {}),
            },
        }

        sc_services = [{"PortName": c["image"], "DiscoveryName": c["server"],
                        "ClientAliases": [{"Port": c["port"], "DnsName": c["server"]}]}
                       for c in svc["containers"] if c["server"]]
        service = {
            "Type": "AWS::ECS::Service",
            "Properties": {
                "ServiceName": svc["name"],
                "Cluster": _imp("cluster-name"),
                "LaunchType": "FARGATE",
                "PlatformVersion": "LATEST",
                "TaskDefinition": {"Ref": task},
                "DesiredCount": svc.get("desired", 1),
                "DeploymentConfiguration": {"MinimumHealthyPercent": 0, "MaximumPercent": 100,
                                            "DeploymentCircuitBreaker": {"Enable": True, "Rollback": True}},
                "NetworkConfiguration": {"AwsvpcConfiguration": {
                    "AssignPublicIp": "ENABLED",  # public subnets, no NAT gateway (cost); SG allows ALB + self only
                    "Subnets": [_imp("public-subnet-1"), _imp("public-subnet-2")],
                    "SecurityGroups": [_imp("task-sg")]}},
                "ServiceConnectConfiguration": {
                    "Enabled": True,
                    "Namespace": _imp("namespace-arn"),
                    **({"Services": sc_services} if sc_services else {}),
                    "LogConfiguration": {"LogDriver": "awslogs", "Options": {
                        "awslogs-group": _imp("log-group"), "awslogs-region": {"Ref": "AWS::Region"},
                        "awslogs-stream-prefix": f"service-connect-{svc['name']}"}},
                },
                "EnableExecuteCommand": False,
                "PropagateTags": "SERVICE",
                "Tags": [{"Key": "project", "Value": {"Ref": "StackPrefix"}}],
            },
        }

        if svc["public"]:
            image, port = svc["target"]
            tg = f"TargetGroup{sid}"
            resources[tg] = {
                "Type": "AWS::ElasticLoadBalancingV2::TargetGroup",
                "Properties": {
                    # No Name: CloudFormation generates one (names are capped at 32 chars, too short for prefix+service).
                    "Port": port, "Protocol": "HTTP", "TargetType": "ip", "VpcId": _imp("vpc-id"),
                    "HealthCheckPath": svc["hc_path"], "HealthCheckIntervalSeconds": 15,
                    "HealthyThresholdCount": 2, "UnhealthyThresholdCount": 5, "Matcher": {"HttpCode": "200-399"},
                    "TargetGroupAttributes": [{"Key": "deregistration_delay.timeout_seconds", "Value": "10"}],
                },
            }
            listener_ids = []
            for alb_port in svc["public"]:
                lid = f"Listener{alb_port}"
                listener_ids.append(lid)
                resources[lid] = {
                    "Type": "AWS::ElasticLoadBalancingV2::Listener",
                    "Properties": {"LoadBalancerArn": _imp("alb-arn"), "Port": alb_port, "Protocol": "HTTP",
                                   "DefaultActions": [{"Type": "forward", "TargetGroupArn": {"Ref": tg}}]},
                }
            service["DependsOn"] = listener_ids
            service["Properties"]["LoadBalancers"] = [{"ContainerName": image, "ContainerPort": port,
                                                       "TargetGroupArn": {"Ref": tg}}]
            service["Properties"]["HealthCheckGracePeriodSeconds"] = 60

        resources[f"Service{sid}"] = service

    for svc in SERVICES:
        outputs[f"{cfn_id(svc['name'])}ServiceName"] = {"Value": {"Fn::GetAtt": [f"Service{cfn_id(svc['name'])}", "Name"]}}
    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": "VoyageAI ECS Fargate services, target groups and ALB listeners (GENERATED by manifest.py)",
        "Parameters": params,
        "Conditions": conditions,
        "Resources": resources,
        "Outputs": outputs,
    }


HEADER = ("# GENERATED by scripts/deploy/aws/manifest.py - do not edit by hand.\n"
          "# Change manifest.py, then run: python scripts/deploy/aws/manifest.py generate\n")


def render() -> dict:
    import yaml  # generation-time only dependency

    class NoAliasDumper(yaml.SafeDumper):
        def ignore_aliases(self, data):
            return True

    def dump(doc):
        return HEADER + yaml.dump(doc, Dumper=NoAliasDumper, sort_keys=False, width=120)

    return {"ecr.yml": dump(_ecr_template()), "services.yml": dump(_services_template())}


def main(argv) -> int:
    command = argv[1] if len(argv) > 1 else ""
    if command == "images":
        for image, compose_service in IMAGES.items():
            print(image, compose_service or "-")
    elif command == "services":
        print("\n".join(service_names()))
    elif command == "listeners":
        for port, name in listeners():
            print(port, name)
    elif command in ("generate", "check"):
        try:
            import yaml  # noqa: F401
        except ImportError:
            if command == "check":
                print("skipped: PyYAML not installed (integration-ci runs this check)", file=sys.stderr)
                return 0
            raise
        stale = []
        for filename, text in render().items():
            path = HERE / filename
            current = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.exists() else None
            if current != text:
                stale.append(filename)
                if command == "generate":
                    path.write_text(text, encoding="utf-8", newline="\n")
        if command == "check" and stale:
            print(f"out of date: {', '.join(stale)}; run: python scripts/deploy/aws/manifest.py generate",
                  file=sys.stderr)
            return 1
        print(("regenerated: " + ", ".join(stale)) if stale and command == "generate" else "up to date")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
