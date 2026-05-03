# T3 — Render-on-edit CI gate

**Branch:** `t3/render-on-edit`
**PR title:** `T3: render-on-edit CI gate`
**Depends on:** T2
**SPEC references:** §4 (last paragraph), §5.3

## Goal

Add a CI gate that ensures `agentic-engineering.md` is always in sync with `resources.yaml` + templates. Any PR that touches the data side without re-rendering must fail the check.

## Scope

### `.github/workflows/render-on-edit.yml`

- **Trigger:** `pull_request` on paths:
  - `resources.yaml`
  - `templates/**`
  - `scripts/render.py`
  - `pyproject.toml` (in case dependency changes affect rendering)
- **Permissions:** `contents: read`.
- **Steps:**
  1. Checkout.
  2. Set up Python via `astral-sh/setup-uv@v3` (or whatever the current canonical action is).
  3. `uv sync --frozen`.
  4. `uv run python scripts/render.py`.
  5. `git diff --exit-code agentic-engineering.md` — fails the check if the rendered file is out of sync.

### `tests/test_render.py` (small)

- One test that asserts `render.py` is idempotent: runs it, captures output, runs it again, asserts no change.
- Optional: a snapshot test against a small fixture in `tests/fixtures/` — only if convenient. Not required.

## Acceptance

- Workflow file lints (use `actionlint` if available locally; not required to install).
- Manual test in this PR: include a deliberately stale `agentic-engineering.md` in one commit, push, observe the CI fail, fix in a follow-up commit, observe pass. Document the test in the PR body. (Or skip the manual test and rely on the next real PR to validate.)
- `uv run pytest tests/test_render.py` passes locally.

## Out of scope

- Verifier or scout workflows (T4, T6).
- Branch protection setup (manual; handled in T4 PR).
- Auto-formatting or any other CI checks (linting, type-checking) — keep this workflow focused on the render gate. Add separate workflows later if needed.

## Notes

- Use a **frozen** uv install (`--frozen`) to ensure CI matches local. Requires `uv.lock` to be committed (T0 acceptance).
- Don't add `push` as a trigger. We only care about PRs — `main` is protected and accepts merges only via PR (configured in T4).
- The workflow runs on every PR touching the listed paths, even drafts. That's intentional — fast feedback.
