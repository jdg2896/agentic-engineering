# T4 — Verifier (script + workflow + ops)

**Branch:** `t4/verifier`
**PR title:** `T4: link verifier + weekly workflow`
**Depends on:** T3
**SPEC references:** §5.1, §6 (kill switch, branch protection), §7 (trust model — no auto-merge in v1)

## Goal

Build the link-health verifier. No LLM calls — pure HTTP. Runs weekly on Mondays, opens a batch PR with status changes.

## Scope

### `scripts/verify_links.py`

- **Input:** `resources.yaml`.
- **For each resource entry:**
  - Issue `HEAD` request; fall back to `GET` on 405 / 501.
  - Follow up to 5 redirects.
  - User-Agent: `agentic-engineering-bot (+https://github.com/<owner>/<repo>)` — fill `<owner>/<repo>` from `GITHUB_REPOSITORY` env var if available, else hardcode the user's repo (configurable).
  - Timeout: 15 seconds.
- **Categorize each URL:**
  - `ok` — final status 200, final URL has same host (eTLD+1) and same path-prefix (within reason: trailing slash differences ignored) as the recorded `url`.
  - `migrated` — final status 200 but final URL host or significant path differs from recorded `url`. Record `final_url` for the report.
  - `dead` — terminal 4xx (except as below) or 5xx. Record status code.
  - `paywall_skipped` — got 401/403 AND resource has `paywall: true`. Treat as a successful verification (bump `verified_at`).
- **On `ok`:** set `verified_at: <today>` in the resource entry.
- **On `migrated` / `dead`:** leave `verified_at` untouched.
- **Concurrency:** ~10 concurrent requests, polite. Use `concurrent.futures.ThreadPoolExecutor` or `httpx` async — your call.
- **Output:**
  - Updated `resources.yaml` (sorted/structured the same way it was input — preserve formatting where possible; if YAML round-trip mangles formatting, accept the diff and note it in the PR).
  - `verification_report.json` artifact with three buckets (ok / migrated / dead) and per-entry detail. Used by the workflow to build the PR body.
- **Flags:**
  - `--dry-run` — runs checks, prints report, does not modify `resources.yaml`.
  - `--limit N` — verify only first N entries (for local testing).

### `.github/workflows/verify-links.yml`

- **Trigger:** `cron: '0 13 * * 1'` (Mondays 13:00 UTC) + `workflow_dispatch`.
- **Permissions:** `contents: write`, `pull-requests: write`.
- **Steps:**
  1. Read `vars.MAINTENANCE_PAUSED`. If `true`, log "maintenance paused" and exit 0.
  2. Checkout.
  3. Setup uv + `uv sync --frozen`.
  4. Run `uv run python scripts/verify_links.py`.
  5. Run `uv run python scripts/render.py` to keep `agentic-engineering.md` in sync.
  6. Use `peter-evans/create-pull-request@v7`:
     - Branch: `bot/verify-${{ env.YEAR_WEEK }}` (compute `YEAR_WEEK` from the current date in a prior step, e.g., `2026-W18`).
     - PR title: `Weekly verification — ${OK} ok / ${DEAD} dead / ${MIGRATED} migrated` (counts from the report).
     - PR body: three checklist sections — Verified (collapsed `<details>`), Dead (expanded), Migrated (expanded). Include URL, status, and `final_url` where applicable.
     - Skip if no diff.

### Manual setup checklist (include verbatim in the PR body for T4)

- [ ] Create repo **variable** `MAINTENANCE_PAUSED` (default value: `false`). Settings → Secrets and variables → Actions → Variables → New repository variable.
- [ ] Enable branch protection on `main`: Settings → Branches → Branch protection rules → require pull request before merging; no required reviewers (single-reviewer repo); require status checks: `render-on-edit`.
- [ ] Verify the workflow appears in the Actions tab and `workflow_dispatch` runs successfully.

## Acceptance

- `uv run python scripts/verify_links.py --dry-run` succeeds locally and produces a coherent report against the full migrated `resources.yaml`. Most entries should be `ok`; a handful may be `migrated` or `dead` (these will be the first PR's content once the workflow runs).
- Workflow file passes `actionlint` if available; otherwise eyeball-review.
- The PR body includes the manual setup checklist verbatim.

## Out of scope

- Auto-merge of `verified_at`-only diffs (deferred to v2 — see SPEC §7).
- Content sanity check (e.g., title-substring match) — deferred to v2.
- Anchor / fragment validation — deferred to v2.
- Migration target-finding (suggesting the new URL when something migrates) — deferred to v2.
- `ANTHROPIC_API_KEY` setup — verifier doesn't call any LLM.

## Notes

- **YAML round-tripping is the bear.** PyYAML default dumping will reorder keys and lose comments. Use `ruamel.yaml` if preserving formatting matters. Easiest path: use `ruamel.yaml` with `preserve_quotes=True`, default flow style block. Acceptable degradation: reformat the whole file, accept the one-time diff in this PR.
- **Paywall detection** in v1 is just "this resource is marked `paywall: true`, so don't flag 401/403 as dead." We're not actively detecting paywall walls.
- **Migration detection** in v1 is heuristic (host+path comparison). It will produce some false positives (e.g., a redirect from `http://` to `https://` should NOT be flagged as migrated — normalize scheme before comparing). Add a few obvious normalizations: trailing slash, `http→https`, `www.` prefix.
- **Don't try to fix migrations automatically** in v1. The PR shows you the new URL; you decide whether to update the `url` field manually before merging.
- **The first run will be loud** — many of the unverified-at-migration entries will become `ok` and bump `verified_at`. That's the point. Subsequent runs will be quiet.
