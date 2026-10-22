import json, os, subprocess, yaml

region = os.environ["AWS_REGION"]
vpc = os.environ["VPC_ID"]
subnets = os.environ["SUBNET_IDS"].split()
alb_sg = os.environ["ALB_SG"]

def aws(*args, ok=()):
    r = subprocess.run(["aws", *args, "--region", region, "--output", "json"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0 and not any(k in r.stderr for k in ok):
        raise SystemExit("FAILED: aws " + " ".join(args[:2]) + "\n" + r.stderr)
    return json.loads(r.stdout) if r.stdout.strip() else {}

manifest = yaml.safe_load(open("scripts/deploy/aws/services.yml"))
alb = aws("elbv2", "create-load-balancer", "--name", "voyageai-alb",
          "--subnets", *subnets, "--security-groups", alb_sg,
          "--scheme", "internet-facing")["LoadBalancers"][0]
print("ALB:", alb["DNSName"])

health = {"destination-frontend": "/health", "accommodation-frontend": "/health",
          "activity-frontend": "/health"}
tgs = {}
for s in manifest["services"]:
    port = s.get("alb_listener_port")
    if not port:
        continue
    path = health.get(s["name"], "/")
    tg = aws("elbv2", "create-target-group", "--name", f"tg-{s['name']}",
             "--protocol", "HTTP", "--port", str(s["container_port"]),
             "--vpc-id", vpc, "--target-type", "ip",
             "--health-check-path", path, "--matcher", "HttpCode=200-399",
             "--health-check-interval-seconds", "15",
             "--healthy-threshold-count", "2")["TargetGroups"][0]["TargetGroupArn"]
    aws("elbv2", "create-listener", "--load-balancer-arn", alb["LoadBalancerArn"],
        "--protocol", "HTTP", "--port", str(port),
        "--default-actions", f"Type=forward,TargetGroupArn={tg}",
        ok=("DuplicateListener",))
    tgs[s["name"]] = tg
    print(f"OK   {s['name']}: ALB :{port} -> container :{s['container_port']} (health {path})")

with open(os.path.expanduser("~/voyage-alb.json"), "w") as f:
    json.dump({"alb_arn": alb["LoadBalancerArn"], "dns": alb["DNSName"], "target_groups": tgs}, f, indent=2)