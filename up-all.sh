#!/usr/bin/env bash
# Tears down every service project independently.
#
# Usage:
#   ./scripts/down-all.sh              # stop + remove containers only
#   ./scripts/down-all.sh --volumes    # also remove named volumes (wipes all SQLite data)
#
# Run from the repo root. Skips folders that don't exist yet or don't have a
# docker-compose.yml yet, and continues past any project that errors out
# instead of stopping the whole run — useful while some feature folders are
# still empty placeholders.

set -uo pipefail   # no -e: we want to keep going even if one project fails

PROJECTS=(shared
  destination-service
  accommodation-service
  activity-service
  budget-service
  itinerary-service
)

FLAG=""
if [[ "${1:-}" == "--volumes" ]]; then
  FLAG="--volumes"
  echo "==> WARNING: this will also delete all SQLite data volumes."
fi

FAILED=()
SKIPPED=()

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

  echo "==> Stopping $p"
  if (cd "$p" && docker compose down $FLAG); then
    echo "==> $p stopped"
  else
    echo "==> WARNING: $p failed to stop cleanly, continuing anyway"
    FAILED+=("$p")
  fi
done

echo
echo "==> Done."
[ ${#SKIPPED[@]} -gt 0 ] && echo "    Skipped (not implemented yet): ${SKIPPED[*]}"
[ ${#FAILED[@]} -gt 0 ]  && echo "    Failed (check manually):       ${FAILED[*]}"
exit 0
