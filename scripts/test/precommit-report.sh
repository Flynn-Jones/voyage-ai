#!/usr/bin/env bash
# Runs every pre-commit hook over the whole repo (within the scope set by the
# top-level `files:` regex in .pre-commit-config.yaml) and saves the output as
# a timestamped report you can cite in your pre-commit-security-report.md.
#
# Usage (from anywhere in the repo):
#   scripts/test/precommit-report.sh              # -> docs/release-2/shared/pre-commit-<UTC>.txt
#   scripts/test/precommit-report.sh student-3    # -> docs/release-2/student-3/pre-commit-<UTC>.txt
#
# Needs pre-commit (`pip install pre-commit`). Override the command with
# PRE_COMMIT, e.g. PRE_COMMIT="python -m pre_commit". Exits with pre-commit's
# status, so a failing scan still fails the script after saving the report.

set -uo pipefail

WHO="${1:-shared}"
if [[ ! "$WHO" =~ ^(shared|student-[1-5])$ ]]; then
  echo "usage: $0 [shared|student-1..student-5]" >&2
  exit 2
fi

ROOT="$(git rev-parse --show-toplevel)" || exit 2
cd "$ROOT" || exit 2
read -r -a PRE_COMMIT_CMD <<<"${PRE_COMMIT:-pre-commit}"
if ! "${PRE_COMMIT_CMD[@]}" --version >/dev/null 2>&1; then
  echo "pre-commit not found; install it with: pip install pre-commit" >&2
  exit 2
fi

OUT_DIR="docs/release-2/$WHO"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$OUT_DIR/pre-commit-$STAMP.txt"
mkdir -p "$OUT_DIR"

{
  echo "# pre-commit security scan"
  echo "date:       $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "who:        $WHO"
  echo "branch:     $(git rev-parse --abbrev-ref HEAD)"
  echo "commit:     $(git rev-parse --short HEAD)"
  echo "pre-commit: $("${PRE_COMMIT_CMD[@]}" --version)"
  # the multi-line `files: |` block, flattened onto one line
  echo "scope:      $(sed -n '/^files:/,/^ *)/p' .pre-commit-config.yaml | tr -s ' \n' ' ')"
  echo
} >"$OUT"

"${PRE_COMMIT_CMD[@]}" run --all-files --color never 2>&1 | tee -a "$OUT"
STATUS=${PIPESTATUS[0]}

{
  echo
  echo "exit status: $STATUS ($([ "$STATUS" -eq 0 ] && echo PASS || echo FAIL))"
} >>"$OUT"

echo
echo "Report saved to $OUT"
exit "$STATUS"
