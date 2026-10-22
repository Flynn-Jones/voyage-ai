import json, os, subprocess, yaml

region = os.environ["AWS_REGION"]
account = os.environ["AWS_ACCOUNT_ID"]
sha = os.environ["GIT_SHA"]
role = os.environ["EXEC_ROLE_ARN"]

with open("scripts/deploy/aws/services.yml") as f:
    manifest = yaml.safe_load(f)

common_ai = manifest.get("common_disabled_ai_env", {})
port_override = {"accommodation-frontend": 3002, "accommodation-api": 5002, "accommodation-database": 6002}
# the manifest says API_URL, but these frontends read a different variable name
frontend_var = {
    "shared-frontend": "ACCESS_SERVICE_URL",
    "destination-frontend": "BACKEND_SERVICE_URL",
    "accommodation-frontend": "BACKEND_SERVICE_URL",
    "activity-frontend": "BACKEND_SERVICE_URL",
}

for s in manifest["services"]:
    env = dict(s.get("env") or {})
    if s["tier"] != "database":
        env = {**common_ai, **env}
    if s["name"] in frontend_var and "API_URL" in env:
        env[frontend_var[s["name"]]] = env.pop("API_URL")
    if s["name"] in port_override:
        env["PORT"] = str(port_override[s["name"]])
    container = {
        "name": s["name"],
        "image": f'{account}.dkr.ecr.{region}.amazonaws.com/{s["ecr_repo"]}:{sha}',
        "essential": True,
        "portMappings": [{"name": s["name"], "containerPort": s["container_port"], "protocol": "tcp"}],
        "environment": [{"name": k, "value": str(v)} for k, v in env.items()],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": f'/voyageai/{s["name"]}',
                "awslogs-region": region,
                "awslogs-stream-prefix": "ecs",
            },
        },
    }
    task_def = {
        "family": s["name"],
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "cpu": "256",
        "memory": "512",
        "runtimePlatform": {"cpuArchitecture": "X86_64", "operatingSystemFamily": "LINUX"},
        "executionRoleArn": role,
        "containerDefinitions": [container],
    }
    path = f'/tmp/taskdef-{s["name"]}.json'
    with open(path, "w") as out:
        json.dump(task_def, out)
    r = subprocess.run(
        ["aws", "ecs", "register-task-definition", "--cli-input-json", f"file://{path}",
         "--region", region, "--query", "taskDefinition.taskDefinitionArn", "--output", "text"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    print(("OK   " if r.returncode == 0 else "FAIL ") + s["name"], (r.stdout or r.stderr).strip())