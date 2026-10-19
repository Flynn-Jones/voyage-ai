#!/usr/bin/env bash
# Build, push and deploy the whole VoyageAI app to AWS ECS Fargate.
#
#   scripts/deploy/aws/deploy.sh [--dry-run] [--phase all|infra|images|app]
#
#   infra   network + ECR stacks (repositories must exist before pushing)
#   images  docker compose build, tag <registry>/<prefix>/<image>:<IMAGE_TAG>, push (ECR scans on push)
#   app     cluster + services stacks, wait until every service is stable, save stack outputs
#   all     infra -> images -> app (default)
#
# Settings: AWS_REGION, STACK_PREFIX, IMAGE_TAG, USE_LAB_ROLE, ENABLE_EFS (see lib.sh).
# MCP, RAG, the Multi-Agent Server and Ollama are NOT deployed; every backend gets the AI flags false.
# --dry-run prints every command without calling AWS or Docker (no credentials needed).

# shellcheck source=scripts/deploy/aws/lib.sh
source "$(dirname "$0")/lib.sh"

PHASE=all
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --phase) PHASE="${2:?--phase needs a value}"; shift ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done
case "$PHASE" in all|infra|images|app) ;; *) die "--phase must be all, infra, images or app" ;; esac

log "region=$AWS_REGION prefix=$STACK_PREFIX tag=$IMAGE_TAG lab_role=$USE_LAB_ROLE efs=$ENABLE_EFS dry_run=$DRY_RUN phase=$PHASE"
require_tools aws docker git "$PYTHON"
manifest check >/dev/null || die "services.yml/ecr.yml are stale: run python scripts/deploy/aws/manifest.py generate"

ACCOUNT="$(account_id)"
REGISTRY="$(registry "$ACCOUNT")"

phase_infra() {
  deploy_stack network "$AWS_DIR/network.yml"
  deploy_stack ecr "$AWS_DIR/ecr.yml"
}

phase_images() {
  log "ECR login: $REGISTRY"
  if is_dry; then
    printf '[dry-run] aws ecr get-login-password --region %q | docker login --username AWS --password-stdin %q\n' \
      "$AWS_REGION" "$REGISTRY"
  else
    aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY"
  fi

  # Fargate runs linux/amd64 (manifest.py RuntimePlatform); force it on Apple-silicon laptops too.
  export DOCKER_DEFAULT_PLATFORM=linux/amd64
  log "Building the app exactly as docker-compose.yml defines it"
  run docker compose --project-directory "$ROOT" -f "$ROOT/docker-compose.yml" -p "$COMPOSE_PROJECT" build
  run docker build -t "$COMPOSE_PROJECT-smoke-runner" "$AWS_DIR/smoke-runner"

  local image compose_service built remote
  while read -r image compose_service; do
    if [ "$compose_service" = "-" ]; then built="$COMPOSE_PROJECT-$image"; else built="$COMPOSE_PROJECT-$compose_service"; fi
    remote="$REGISTRY/$STACK_PREFIX/$image:$IMAGE_TAG"
    run docker tag "$built" "$remote"
    run docker push "$remote"
  done < <(manifest images)
}

phase_app() {
  local lab_role_arn=""
  if [ "$USE_LAB_ROLE" = "true" ]; then
    lab_role_arn="arn:aws:iam::$ACCOUNT:role/LabRole"
    log "Learner Lab: using $lab_role_arn for task and execution roles"
  fi
  deploy_stack cluster "$AWS_DIR/cluster.yml" "UseLabRole=$USE_LAB_ROLE" "LabRoleArn=$lab_role_arn" "EnableEfs=$ENABLE_EFS"
  deploy_stack services "$AWS_DIR/services.yml" "ImageTag=$IMAGE_TAG" "EnableEfs=$ENABLE_EFS"

  log "Waiting for every ECS service to become stable"
  local services=() name
  while read -r name; do services+=("$name"); done < <(manifest services)   # bash 3.2 (macOS) has no mapfile
  for ((i = 0; i < ${#services[@]}; i += 10)); do   # the waiter accepts at most 10 services per call
    run aws ecs wait services-stable --region "$AWS_REGION" --cluster "$STACK_PREFIX" --services "${services[@]:i:10}"
  done

  local out
  out="$EVIDENCE_DIR/stack-outputs-$(utc_stamp).json"
  log "Saving stack outputs to ${out#"$ROOT"/}"
  if is_dry; then
    printf '[dry-run] aws cloudformation describe-stacks ... > %q\n' "$out"
  else
    mkdir -p "$EVIDENCE_DIR"
    aws cloudformation describe-stacks --region "$AWS_REGION" --output json \
      --query "Stacks[?starts_with(StackName, '$STACK_PREFIX-')].Outputs[]" >"$out"
  fi
  log "Home page: $(stack_output cluster AlbUrl)  (frontends on ports 3001-3005; see FrontendUrls)"
}

case "$PHASE" in
  infra) phase_infra ;;
  images) phase_images ;;
  app) phase_app ;;
  all) phase_infra; phase_images; phase_app ;;
esac
log "deploy.sh phase=$PHASE done"
