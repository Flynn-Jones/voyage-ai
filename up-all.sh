#!/usr/bin/env bash
# Brings up every service project independently — no docker-compose `include:`,
# so there's no risk of "services.X conflicts with imported resource" even
# though every project reuses the generic service names frontend/backend/database.
#
# Usage:
#   ./scripts/up-all.sh          # build + start everything, detached
#   ./scripts/up-all.sh --logs   # same, then follow logs from all projects
#
# Run from the repo root. Skips folders that don't exist yet or don't have a
# docker-compose.yml yet, and continues past any project that fails to build
# or start instead of stopping the whole run — useful while some feature
# folders are still empty placeholders.

set -uo pipefail   # no -e: we want to keep going even if one project fails

PROJECTS=(shared
  destination-service
  accommodation-service
  activity-service
  budget-service
  itinerary-service
)

echo "==> Ensuring shared external network exists"
docker network inspect microservices-net >/dev/null 2>&1 || docker network create microservices-net

FAILED=()
SKIPPED=()
STARTED=()

for p in "${PROJECTS[@]}"; do
  if [ ! -d "$p" ]; then
    echo "==> Skipping $p (folder does not exist)"
    SKIPPED+=("$p")
    continue
  fi

  if [ ! -f "$p/docker-compose.yml" ]; then
    echo "==> Skipping $p (no docker-compose.yml yet)"
    SKIPPED+=("$p")
    continue
  fi

  echo "==> Starting $p"
  if (cd "$p" && docker compose up -d --build); then
    echo "==> $p started"
    STARTED+=("$p")
  else
    echo "==> WARNING: $p failed to build/start, continuing anyway"
    FAILED+=("$p")
  fi
done

echo
echo "==> Done."
[ ${#STARTED[@]} -gt 0 ] && echo "    Started:                       ${STARTED[*]}"
[ ${#SKIPPED[@]} -gt 0 ] && echo "    Skipped (not implemented yet): ${SKIPPED[*]}"
[ ${#FAILED[@]} -gt 0 ]  && echo "    Failed (check manually):       ${FAILED[*]}"
echo
echo "    Run 'docker compose -f <project>/docker-compose.yml ps' to check a"
echo "    specific project, or 'docker ps' to see everything running."

if [[ "${1:-}" == "--logs" ]]; then
  for p in "${STARTED[@]}"; do
    (cd "$p" && docker compose logs -f &)
  done
  wait
fi

exit 0
