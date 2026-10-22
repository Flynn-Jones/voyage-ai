import json, os, subprocess

region = os.environ["AWS_REGION"]
account = os.environ["AWS_ACCOUNT_ID"]
sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
ecr = f"{account}.dkr.ecr.{region}.amazonaws.com"
D = subprocess.DEVNULL
print("Using tag", sha)

builds = [
    ("shared-frontend", "shared/frontend/Dockerfile", "shared", "voyageai/shared-frontend"),
    ("destination-frontend", "student-1/frontend/Dockerfile", "student-1/frontend", "voyageai/destination-frontend"),
    ("accommodation-frontend", "accommodation-service/frontend/Dockerfile", "accommodation-service/frontend", "voyageai/accommodation-frontend"),
    ("activity-frontend", "activity-service/frontend/Dockerfile", "activity-service/frontend", "voyageai/activity-frontend"),
    ("itinerary-fe", "itinerary-service/itinerary-fe/Dockerfile", "itinerary-service/itinerary-fe", "voyageai/itinerary-fe"),
]

def aws(*a):
    return subprocess.run(["aws", *a, "--region", region, "--output", "json"],
                          capture_output=True, text=True, stdin=D, check=True).stdout

pw = subprocess.run(["aws", "ecr", "get-login-password", "--region", region],
                    capture_output=True, text=True, stdin=D, check=True).stdout
subprocess.run(["docker", "login", "--username", "AWS", "--password-stdin", ecr],
               input=pw, text=True, check=True)

names = []
for name, dockerfile, ctx, repo in builds:
    uri = f"{ecr}/{repo}:{sha}"
    print("=== build", name, flush=True)
    subprocess.run(["docker", "build", "--platform", "linux/amd64", "-f", dockerfile, "-t", uri, ctx],
                   stdin=D, check=True)
    subprocess.run(["docker", "push", uri], stdin=D, check=True)
    td = json.loads(aws("ecs", "describe-task-definition", "--task-definition", name))["taskDefinition"]
    for k in ["taskDefinitionArn", "revision", "status", "requiresAttributes", "compatibilities",
              "registeredAt", "registeredBy", "deregisteredAt"]:
        td.pop(k, None)
    c = td["containerDefinitions"][0]
    c["image"] = uri
    json.dump(td, open(f"/tmp/td-{name}.json", "w"))
    arn = json.loads(aws("ecs", "register-task-definition", "--cli-input-json",
                         f"file:///tmp/td-{name}.json", "--query", "taskDefinition.taskDefinitionArn"))
    aws("ecs", "update-service", "--cluster", "voyageai-cluster", "--service", name,
        "--task-definition", arn, "--force-new-deployment")
    print("OK   rolled", name, "->", arn, flush=True)
    names.append(name)

print("waiting for services to stabilise...", flush=True)
subprocess.run(["aws", "ecs", "wait", "services-stable", "--cluster", "voyageai-cluster",
                "--services", *names, "--region", region], stdin=D)
print("DONE")