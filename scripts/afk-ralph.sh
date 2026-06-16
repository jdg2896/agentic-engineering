#!/bin/bash
set -euo pipefail

# Usage: afk-ralph.sh <spec-file> [max-iterations] [branch-name]
#   e.g. afk-ralph.sh docs/specs/discipline-agnostic-scope.md 30
# If no spec is given, lists available specs and exits.
# Each iteration is a FRESH sandboxed Claude session (no shared memory);
# the spec file + progress file + git history are the only state carried
# between iterations. Stops early when the spec is complete.
#
# Branch/PR flow (this repo blocks direct pushes to main — see CLAUDE.md):
#   - Before the loop: sync main and create the work branch (or resume an
#     existing one). All iteration commits land on that branch.
#   - After the spec completes: push the branch and open a PR.

# Resolve repo root so the script works from any CWD.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$REPO_ROOT" ]; then
  REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
fi
cd "$REPO_ROOT"

SPEC="${1:-}"
SPEC_DIR="docs/specs"
MAX_ITERS="${2:-25}"
SANDBOX_NAME="claude-$(basename "$REPO_ROOT")"

if [ -z "$SPEC" ]; then
  echo "Usage: $0 <spec-file> [max-iterations] [branch-name]"
  echo "Available specs:"
  ls -1 "$SPEC_DIR"/*.md 2>/dev/null || echo "  (none found in $SPEC_DIR)"
  exit 1
fi

if [ ! -f "$SPEC" ]; then
  echo "Spec not found: $SPEC"
  exit 1
fi

# Per-spec progress file so switching specs doesn't clobber progress.
PROGRESS="${SPEC%.md}.progress.txt"
touch "$PROGRESS"

# Branch name: 3rd arg, else feat/<spec-slug>. The type prefix doubles as
# the Conventional Commits type for the PR title (CLAUDE.md: squash-merge
# makes the PR title the commit on main, so it must be conventional).
SPEC_SLUG="$(basename "${SPEC%.md}")"
BRANCH="${3:-feat/$SPEC_SLUG}"
BRANCH_TYPE="${BRANCH%%/*}"
[ "$BRANCH_TYPE" = "$BRANCH" ] && BRANCH_TYPE="feat"
SPEC_TITLE="$(sed -n 's/^# //p' "$SPEC" | head -1)"
[ -z "$SPEC_TITLE" ] && SPEC_TITLE="$SPEC_SLUG"

# --- Branch setup (before the loop, so every iteration commits here) ------
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$CURRENT_BRANCH" = "$BRANCH" ]; then
  echo "Resuming on existing branch: $BRANCH"
elif git rev-parse --verify --quiet "refs/heads/$BRANCH" >/dev/null; then
  echo "Switching to existing branch: $BRANCH"
  git checkout "$BRANCH"
else
  # Fresh branch: sync main first (best-effort — an offline run still
  # proceeds from local main rather than aborting the whole job).
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "Working tree has tracked modifications; commit or stash them first." >&2
    git status --short >&2
    exit 1
  fi
  git checkout main
  git pull --ff-only origin main \
    || echo "warning: could not sync origin/main; continuing on local main"
  git checkout -b "$BRANCH"
  echo "Created branch: $BRANCH (from main)"
fi

# Ensure the sandbox exists BEFORE the loop so the in-loop exec never blocks
# on an interactive create prompt. `docker sandbox create claude WORKSPACE`
# auto-names the sandbox `claude-<workspace-basename>` (no --name flag), which
# is exactly how SANDBOX_NAME is derived above.
if ! docker sandbox ls -q 2>/dev/null | grep -qx "$SANDBOX_NAME"; then
  echo "Creating sandbox $SANDBOX_NAME (workspace: $REPO_ROOT) ..."
  docker sandbox create claude "$REPO_ROOT"
fi

open_pr() {
  # Push the branch and open a PR. Best-effort: the work is already
  # committed locally, so network/tooling gaps warn rather than fail.
  if ! git push -u origin "$BRANCH"; then
    echo "warning: git push failed — branch not pushed, skipping PR." >&2
    return 0
  fi
  if ! command -v gh >/dev/null 2>&1; then
    echo "gh CLI not found — branch pushed; open the PR manually." >&2
    return 0
  fi
  local existing
  existing="$(gh pr view "$BRANCH" --json url --jq .url 2>/dev/null || true)"
  if [ -n "$existing" ]; then
    echo "PR already exists: $existing"
    return 0
  fi
  local pr_body
  pr_body="## Summary

Automated implementation of $SPEC via afk-ralph.sh.

Each task was implemented in a fresh sandboxed session; see
$PROGRESS and the commit history for the per-task trail.

## Test plan

- See the spec's Validation section.
- uv run pytest

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
  gh pr create \
    --base main \
    --head "$BRANCH" \
    --title "$BRANCH_TYPE: $SPEC_TITLE" \
    --body "$pr_body" \
    && echo "PR opened for $BRANCH"
}

for ((i=1; i<=MAX_ITERS; i++)); do
  echo "=== Iteration $i/$MAX_ITERS — $SPEC ==="
  log="$(mktemp)"

  # exec into the existing sandbox directly — `docker sandbox run <name> -- args`
  # silently drops the agent args; exec passes them correctly. stdin is closed
  # so it can never block on input. Stream output live via tee AND keep it in
  # $log for the completion check (do not swallow it in result=$(...)).
  docker sandbox exec -w "$REPO_ROOT" "$SANDBOX_NAME" \
    claude --permission-mode acceptEdits -p "@$SPEC @$PROGRESS \
  1. Read the spec and progress file. \
  2. Find the next incomplete task in the spec and implement it. \
  3. Run the tests/checks in the task's Verify step (this repo: uv run pytest). \
  4. Update $PROGRESS with what you did. \
  5. Commit your changes using Conventional Commits. \
  ONLY DO ONE TASK AT A TIME. \
  If the spec is complete, output <promise>COMPLETE</promise>." </dev/null 2>&1 | tee "$log"

  if grep -q "<promise>COMPLETE</promise>" "$log"; then
    rm -f "$log"
    echo "Spec complete after $i iterations: $SPEC"
    open_pr
    exit 0
  fi
  rm -f "$log"
done

echo "Reached max iterations ($MAX_ITERS) without completion: $SPEC"
echo "Branch '$BRANCH' has the partial work; re-run to resume or open a PR manually."
