# Auto-quarantine dead links

## Why

The weekly link verifier detects dead URLs but only reports them in a
PR body — a human still has to delete or replace each entry by hand.
That manual step is the bottleneck: today's
`verification_report.json` has 3 long-dead entries plus the soft-404
and `worth_following` cases caught by PR #23, and they'll keep landing
in the rendered guide for weeks until someone gets around to it.

The original verifier spec (`T4-verifier.md`) explicitly deferred
"auto-merge of `verified_at`-only diffs" to v2. This spec is that v2,
expanded: the bot can also write a `quarantined_at` field that hides
dead entries from the rendered guide without deleting them, and the
weekly PR auto-merges when its contents are routine.

The aim is "auto for the boring 95%, ping me for the surprising 5%" —
the maintainer reviews PRs that quarantine top-7 entries, propose mass
changes, recover previously-dead URLs, or list migration suggestions;
everything else lands without manual approval.

## What

After this spec lands:

- `resources:` and `worth_following:` entries gain three optional
  fields: `first_dead_at`, `quarantined_at`, `quarantine_reason`.
- The verifier writes `first_dead_at: <today>` on the first
  consecutive `dead` outcome (clears it on any non-`dead` outcome).
- After 21 days continuously dead
  (`today - first_dead_at >= 21`), the verifier writes
  `quarantined_at: <today>` and `quarantine_reason: <status_or_error>`.
  Once quarantined, further dead checks are no-ops.
- The renderer treats `quarantined_at` like the existing
  `hidden`/`superseded_by` fields: the entry stays in the YAML but
  doesn't render in section bullets or the `worth_following` list.
- `top_7:` references to a quarantined entry **hard-fail** the
  renderer. A quarantined marquee pick is editorially significant and
  must be resolved manually (drop from `top_7:` or unquarantine).
- The verifier still re-checks quarantined entries weekly. On a
  successful re-check, it clears `quarantined_at` and
  `quarantine_reason` and stamps `verified_at`. Recovery PRs surface a
  "Recovered" section in the body and are **never auto-merged**.
- `verify-links.yml` enables GitHub auto-merge on the weekly PR only
  when the diff is "routine":
  - Only `verified_at` / `first_dead_at` writes, AND/OR
  - `quarantined_at` writes that (a) reference no `top_7:` slug and
    (b) total ≤5 in this run.
  - Any recovery, top-7 quarantine, or >5 quarantines in one run
    blocks auto-merge — the PR sits for human review.
  - Migration listings in the PR body do **not** block auto-merge
    (they don't modify the YAML; a human eyeballs them when they
    happen to look at git log).
- All three workflows (`render-on-edit`, `scout-resources`,
  `verify-links`) bump their action pins to current Node 24-based
  majors as part of T3.

## Constraints

### Must

- Reuse the existing "in YAML, hidden from render" pattern
  (`hidden`/`superseded_by`) for quarantined entries — same render-time
  filter, no parallel mechanism.
- Use a time-based threshold (`first_dead_at`), not a strike counter,
  so the policy is independent of cron cadence.
- Keep verification weekly. Detection latency of up to 7 days is
  acceptable for a low-velocity human-curated guide.
- Match `worth_following:` entries by `url` when writing fields
  (verifier-side `wf:<slug>` ids are synthesized, not stored).
- Honour `MAINTENANCE_PAUSED` repo variable as the kill switch
  throughout — auto-merge must not bypass it.
- All three workflows ship on the same major-version cohort
  (`actions/checkout@v5`, `astral-sh/setup-uv@v6`, current
  `peter-evans/create-pull-request` major) so the deprecation
  surface stays uniform.

### Must Not

- Auto-delete quarantined entries. They live in the YAML
  indefinitely; manual cleanup if it ever bothers anyone.
- Auto-rewrite `url:` for migrated entries. Migrations stay manual
  review until a future spec proposes a heuristic guard.
- Re-set `first_dead_at` once an entry is quarantined. The clock
  doesn't restart on a still-dead re-check.
- Auto-merge recovery, top-7 quarantine, or mass-quarantine PRs.
  These need eyeballs even when CI is green.
- Run the verifier daily. Decoupling verify-vs-PR cadences and the
  added `verified_at` churn aren't worth the marginal latency win
  on this guide.
- Add a separate "graveyard" file or state DB. State lives in
  `resources.yaml` next to `verified_at`.

### Out of Scope

- Migration auto-rewrite (deferred — needs a "deep path" heuristic so
  homepage redirects don't silently replace article URLs).
- Hard-delete escalation for entries quarantined longer than N months
  (YAGNI — write the cleanup script when clutter actually pains).
- Daily-cadence verification or workflow-dispatch-on-mention.
- Anchor / fragment validation (still in T4's deferred list).
- Content-substring sanity check (also still deferred).
- Any change to the soft-404 or `worth_following` verification logic
  itself — that landed in PR #23 and is assumed merged.

## Current State

- **Verifier:** `scripts/verify_links.py` (with PR #23's changes)
  buckets each URL into `ok` / `migrated` / `dead` / `paywall_skipped`.
  Writes `verified_at` on `ok` and `paywall_skipped` outcomes via
  `apply_updates` (verify_links.py:220). Iterates both `resources:`
  and `worth_following:` via `collect_targets`. No persistent
  consecutive-failure state today.
- **Render filter:** `build_populated_sections` skips entries with
  `superseded_by` or `hidden` set (render.py:79-82). Top-7 lookup is
  unfiltered (render.py:106-112) — a quarantined slug in `top_7:`
  would render a working link to a quarantined entry today.
- **Workflow:** `.github/workflows/verify-links.yml` runs Mondays
  13:00 UTC, opens a PR via `peter-evans/create-pull-request@v7`. PR
  body has three sections: Verified (collapsed), Dead, Migrated. No
  auto-merge wiring.
- **Action pins (all deprecated Node 20):**
  `actions/checkout@v4`, `astral-sh/setup-uv@v5`,
  `peter-evans/create-pull-request@v7`, used across
  `render-on-edit.yml`, `scout-resources.yml`, and `verify-links.yml`.
- **Tests:** `tests/test_render.py` (13 tests),
  `tests/test_verify_links.py` (8 tests, added in PR #23).
- **Schema reference:** `docs/specs/self-maintaining-guide/SPEC.md` §3.1
  documents the resource fields. Add the three new fields there.

## Tasks

### T1: Renderer respects `quarantined_at`

**What:** Update the renderer to filter quarantined entries the same
way it filters `hidden` / `superseded_by`, and hard-fail on a
quarantined `top_7:` slug. Ships first so the renderer is ready before
the verifier ever writes the field.

- `scripts/render.py`:
  - In `build_populated_sections`, extend the `visible` filter:
    `not r.get("superseded_by") and not r.get("hidden") and not r.get("quarantined_at")`.
  - In `render()`, after building `resources_by_id`, validate the
    `top_7:` list: for each slug, raise a clear error if the
    referenced resource has `quarantined_at` set. Message should name
    the slug and the quarantine reason and suggest dropping it from
    `top_7:` or unquarantining.
  - For `worth_following`, filter `data["worth_following"]` before
    passing to the template:
    `[w for w in data["worth_following"] if not w.get("quarantined_at")]`.
- `tests/test_render.py`:
  - Test: a section entry with `quarantined_at` set does not appear
    in rendered output.
  - Test: a `worth_following` entry with `quarantined_at` set does
    not appear in the rendered "Worth following" section.
  - Test: a `top_7:` slug pointing at a quarantined entry raises with
    a message naming the slug.

**Files:** `scripts/render.py`, `tests/test_render.py`,
`docs/specs/self-maintaining-guide/SPEC.md` (extend §3.1 schema with
the three new optional fields).

**Verify:**
- `uv run pytest tests/test_render.py` — all green, including the new
  cases.
- `uv run python scripts/render.py` against the current
  `resources.yaml` (no quarantined entries yet) — produces an
  identical `README.md` to the pre-change state.
- Manual: temporarily add `quarantined_at: 2026-05-03` to one
  non-top-7 resource, re-render, confirm it disappears from the
  section. Revert before commit.
- Manual: temporarily add `quarantined_at` to a `top_7:` entry,
  re-render, confirm `render.py` exits non-zero with a useful error.
  Revert before commit.

### T2: Verifier writes the quarantine state

**What:** Extend `apply_updates` and `check_url`'s consumers to drive
the `first_dead_at` → `quarantined_at` state machine, including
recovery. Verifier still iterates quarantined entries (so they can
recover), but treats further `dead` outcomes on already-quarantined
entries as no-ops.

- `scripts/verify_links.py`:
  - Define `QUARANTINE_GRACE_DAYS = 21` near the existing
    `SOFT_404_SNIFF_BYTES` constant.
  - In `apply_updates(yaml_data, results, today)`, expand to handle
    both sections and both shapes (resource by `id`, worth_following
    by `url`). Build two lookups:
    `results_by_resource_id` and `results_by_url`.
  - State transitions per entry, given an outcome from the report:
    - `ok` or `paywall_skipped`:
      - Set `verified_at: today`.
      - Clear `first_dead_at` if present.
      - If entry has `quarantined_at`, this is a **recovery**: clear
        `quarantined_at` and `quarantine_reason`. Mark this in the
        result for the workflow's PR-classification step.
    - `migrated`:
      - Clear `first_dead_at` if present (the URL resolved).
      - Do not stamp `verified_at` (matches existing behavior).
      - Do not touch quarantine fields.
    - `dead` (includes `error: soft_404`):
      - If `quarantined_at` is already set: no-op. Don't re-arm
        anything.
      - Else if `first_dead_at` is unset: set `first_dead_at: today`.
      - Else if `today - first_dead_at >= QUARANTINE_GRACE_DAYS`: set
        `quarantined_at: today`, set
        `quarantine_reason: <status_code or error>`. (Leave
        `first_dead_at` set for audit; the no-op rule above ensures
        it doesn't re-trigger.)
      - Else: leave `first_dead_at` as-is, accumulating the elapsed
        window.
  - Surface a structured summary to the workflow. Extend
    `verification_report.json` with two new top-level lists:
    - `newly_quarantined`: entries that just had `quarantined_at`
      written this run, each with `id`, `url`, `kind`,
      `quarantine_reason`, and a `top_7: bool` field (true when the
      `id` appears in `data["top_7"]`).
    - `recovered`: entries that just had `quarantined_at` cleared,
      each with `id`, `url`, `kind`.
  - For `worth_following` matching, build a URL→entry map from the
    YAML once and look up by `final_url`'s pre-redirect URL (the
    original `url` recorded in the result).
- `tests/test_verify_links.py`:
  - Add unit tests for the state transitions, mocking only the input
    YAML structure and result dicts (no HTTP). Cover:
    - First dead sets `first_dead_at`.
    - Second dead within 21 days does not set `quarantined_at`.
    - Dead at exactly 21 days sets `quarantined_at` and
      `quarantine_reason`.
    - Subsequent dead on a quarantined entry is a no-op.
    - `ok` on a quarantined entry clears the fields and stamps
      `verified_at`.
    - `migrated` clears `first_dead_at` but leaves `verified_at`.
    - `worth_following` entries are matched by URL and updated
      identically.
  - Add a test that the report contains the `newly_quarantined` and
    `recovered` lists with the `top_7: bool` flag populated.

**Files:** `scripts/verify_links.py`, `tests/test_verify_links.py`.

**Verify:**
- `uv run pytest` — all 21+ tests green.
- `uv run python scripts/verify_links.py --dry-run` against the
  current `resources.yaml` — exits 0; report includes empty
  `newly_quarantined` / `recovered` lists when no entries hit the
  threshold; existing dead entries (e.g. `holmes-gpt`) get
  `first_dead_at: <today>` proposed in the dry-run preview.
- Manual: synthesize a test fixture YAML with one entry having
  `first_dead_at` 22 days ago; confirm a dry-run reports
  `newly_quarantined` for that entry.

### T3: Workflow auto-merge + action version bumps

**What:** Wire weekly PR auto-merge with the tiered guards, restructure
the PR body around the new state, and bump action versions across all
three workflows.

- `.github/workflows/verify-links.yml`:
  - Compute `AUTO_MERGE_OK` boolean in the existing `meta` step:
    true iff `report.recovered == []` AND no
    `entry.top_7 == true` in `newly_quarantined` AND
    `len(newly_quarantined) <= 5`. Migration listings do not affect
    this (they don't modify the YAML).
  - Restructure PR body sections:
    - `## Quarantined ({n})` — entries newly quarantined this run,
      with reason. Top-7 entries flagged with a leading `🚩`.
    - `## Recovered ({n})` — entries that came back to life. Empty
      section omitted.
    - `## Dead — accumulating ({n})` — entries with `first_dead_at`
      set but not yet quarantined, with days elapsed.
    - Existing `## Verified` (collapsed) and `## Migrated` sections
      unchanged.
  - PR title:
    `Weekly verification — ${OK} ok / ${QUARANTINED} quarantined / ${RECOVERED} recovered / ${DEAD} dead / ${MIGRATED} migrated`.
  - After `peter-evans/create-pull-request` step, add a step that
    runs `gh pr merge "$PR_URL" --auto --squash` only when
    `AUTO_MERGE_OK == 'true'` and `steps.cpr.outputs.pull-request-number`
    is non-empty. Use `${{ secrets.GITHUB_TOKEN }}` (the default
    workflow token has `pull-requests: write` already).
  - Add a label `auto-merge-skipped` to the PR when
    `AUTO_MERGE_OK == 'false'`, alongside the existing
    `automated,link-health` labels, so the human knows at a glance
    why it didn't merge itself.
- `.github/workflows/render-on-edit.yml`,
  `.github/workflows/scout-resources.yml`,
  `.github/workflows/verify-links.yml`:
  - `actions/checkout@v4` → `@v5`.
  - `astral-sh/setup-uv@v5` → `@v6` (verify v6 exists when
    implementing; otherwise pin the current major).
  - `peter-evans/create-pull-request@v7` → current major (read its
    release notes; v8 if released).
- One-time repo-settings prerequisite (note in PR body, not code):
  - GitHub auto-merge must be enabled in repo settings (Settings →
    General → Pull Requests → Allow auto-merge). Branch protection
    on `main` already requires the `render-on-edit` status check, so
    the auto-merge waits for CI before landing.

**Files:** `.github/workflows/verify-links.yml`,
`.github/workflows/render-on-edit.yml`,
`.github/workflows/scout-resources.yml`.

**Verify:**
- `actionlint .github/workflows/*.yml` (if available) clean;
  otherwise eyeball-review.
- Trigger `verify-links.yml` via `workflow_dispatch` on the feature
  branch with `MAINTENANCE_PAUSED=true` set as a job-scoped env to
  ensure the dispatch path is exercised but no PR is opened. Confirm
  the early-exit step still works.
- Open a one-off test PR by manually editing
  `verification_report.json` to inject a fake `newly_quarantined`
  entry; trigger the post-verifier steps in isolation (extract
  the meta step into a local `python3 -` invocation) and confirm
  the computed PR body, title, and `AUTO_MERGE_OK` value are
  correct for: (a) verified-only run, (b) one routine quarantine,
  (c) one top-7 quarantine, (d) six quarantines, (e) one recovery.
- Confirm bumped action majors are documented as the workflow's
  current pins by inspecting the workflow files post-change.

## Validation

End-to-end acceptance once T1–T3 land:

- All 21+ tests green: `uv run pytest`.
- `uv run python scripts/render.py` against unchanged `resources.yaml`
  produces a byte-identical `README.md`.
- `uv run python scripts/verify_links.py --dry-run` exits 0 against
  unchanged `resources.yaml`; new report fields
  (`newly_quarantined`, `recovered`) are present even when empty.
- Manual end-to-end on a scratch fixture: insert
  `first_dead_at: <23 days ago>` on one resource, run the verifier
  (non-dry), confirm `quarantined_at` is written and rendering still
  succeeds (entry disappears from the relevant section).
- Manual top-7 hard-fail check: as above but on a `top_7:` slug;
  confirm `render.py` raises and the render-on-edit gate would fail.
- First production run after merge: weekly verifier opens a PR; it
  either auto-merges (routine) or sits with the
  `auto-merge-skipped` label (surprising). PR title and body match
  the new format.
- Action versions: all three workflows pin Node 24-based majors.
  `actions/checkout@v5` and `astral-sh/setup-uv@v6` (or whatever was
  current at implementation) appear in every workflow.
- The five URLs that motivated this thread (`memory-tool` soft-404,
  `holmes-gpt`, two `honeycomb.io` entries,
  `blog.cloudflare.com/tag/ai-agents/`) progress from `dead` → 21
  days later → `quarantined_at` set → disappear from rendered guide
  without any human edit to `resources.yaml`.
