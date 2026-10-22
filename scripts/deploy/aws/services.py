import json, os, subprocess, sys, yaml

region = os.environ["AWS_REGION"]
subnets = os.environ["SUBNET_IDS"].split()
task_sg = os.environ["TASK_SG"]
cluster = "voyageai-cluster"

def run(*args):
    return subprocess.run(["aws", *args, "--region", region], capture_output=True,
                          text=True, stdin=subprocess.DEVNULL)

r = run("ecs", "describe-task-definition", "--task-definition", "shared-frontend",
        "--query", "taskDefinition.containerDefinitions[0].environment[].name", "--output", "json")
if "ACCESS_SERVICE_URL" not in r.stdout:
    sys.exit("Task definitions are still the old revision. Run: python3 ~/taskdefs.py")

alb = json.load(open(os.path.expanduser("~/alb.json")))
manifest = yaml.safe_load(open("scripts/deploy/aws/services.yml"))
short_alias = {"budget-backend", "itinerary-be"}   # nginx hardcodes these names

for tier in ("database", "api", "frontend"):
    names = []
    for s in [x for x in manifest["services"] if x["tier"] == tier]:
        sc = {"enabled": True, "namespace": "voyageai.local"}
        if tier != "frontend":
            dns = s["name"] if s["name"] in short_alias else f'{s["service_connect_name"]}.voyageai.local'
            sc["services"] = [{"portName": s["name"], "discoveryName": s["service_connect_name"],
                               "clientAliases": [{"port": s["container_port"], "dnsName": dns}]}]
        req = {"cluster": cluster, "serviceName": s["name"], "taskDefinition": s["name"],
               "desiredCount": 1, "launchType": "FARGATE",
               "networkConfiguration": {"awsvpcConfiguration": {
                   "subnets": subnets, "securityGroups": [task_sg], "assignPublicIp": "ENABLED"}},
               "serviceConnectConfiguration": sc}
        if s.get("alb_listener_port"):
            req["loadBalancers"] = [{"targetGroupArn": alb["target_groups"][s["name"]],
                                     "containerName": s["name"], "containerPort": s["container_port"]}]
            req["healthCheckGracePeriodSeconds"] = 120
        ex = run("ecs", "describe-services", "--cluster", cluster, "--services", s["name"],
                 "--query", "services[?status=='ACTIVE'].serviceName", "--output", "text")
        if s["name"] in ex.stdout:
            print("SKIP (exists)", s["name"]); names.append(s["name"]); continue
        path = f'/tmp/svc-{s["name"]}.json'
        json.dump(req, open(path, "w"))
        r = run("ecs", "create-service", "--cli-input-json", f"file://{path}",
                "--query", "service.serviceName", "--output", "text")
        print(("OK   " if r.returncode == 0 else "FAIL ") + s["name"], (r.stdout or r.stderr).strip()[:300])
        if r.returncode != 0:
            sys.exit(1)
        names.append(s["name"])
    print(f"--- waiting for {tier} tier to be stable (a few minutes) ---", flush=True)
    r = run("ecs", "wait", "services-stable", "--cluster", cluster, "--services", *names)
    if r.returncode != 0:
        sys.exit(f"{tier} tier did not become stable. Check the failing service's logs, then re-run (existing services are skipped).")

print("ALL SERVICES STABLE. Open: http://" + alb["dns"] + ":3000/")