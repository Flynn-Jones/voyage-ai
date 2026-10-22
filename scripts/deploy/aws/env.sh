export AWS_PAGER=""
export AWS_REGION=ap-southeast-2
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
source ~/.venvs/voyage/bin/activate

services_tsv() {
  python3 - <<'PY'
import yaml
with open("scripts/deploy/aws/services.yml") as f:
    data = yaml.safe_load(f)
for s in data["services"]:
    print("\t".join([
        s["name"], s["group"], s["tier"], s["ecr_repo"], s["dockerfile"],
        str(s["container_port"]), str(s.get("alb_listener_port") or "-"),
        s["service_connect_name"],
    ]))
PY
}
