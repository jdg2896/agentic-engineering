# README as rendered output

## Why

The repository's purpose is the curated guide. Today the guide is at
`agentic-engineering.md` and `README.md` is a 6-line pointer to it — one
extra click for every visitor on a repo whose entire reason for existing
is the resource list.

Beyond relocating, several artifacts in the rendered doc reflect the
manual-compilation era and no longer match how the repo works:

- `_Compiled May 2026._` header framing implies a one-time event; the
  repo is continuously maintained by the verifier and scout.
- `✓` marks next to URLs originally meant "live-verified at compile
  time." The verifier now writes `verified_at` weekly, so `✓` lands on
  essentially every reachable URL — making it noise rather than signal.
- The `## Caveats` section's first bullet (`~30 of ~120 URLs verified at
  compile time`) is hardcoded prose from the manual era.
- The `## Opinionated minimal stack` table is a hand-curated snapshot
  of subjective picks. It decays faster than the resource list, isn't
  structured data so neither verifier nor scout can maintain it, and
  concentrates ~10 such judgments into one staleness vector. It violates
  the project's core "self-maintaining" principle.
- The `Worth following for ongoing signal` content (currently buried in
  caveats as names without URLs) is a distinct kind of recommendation —
  feeds to subscribe to, not posts to read once. It deserves its own
  real estate, and its URLs deserve verification like everything else.

## What

After this spec lands:

- `README.md` is the rendered guide. `render.py` overwrites the entire
  file. `agentic-engineering.md` is deleted.
- `_Compiled X_` and the `✓` preamble are gone from the header. A
  one-line subtitle names the maintenance model.
- `✓` marks no longer appear on URLs.
- `## Caveats` is replaced by `## Worth following for ongoing signal`,
  populated from a structured `worth_following` top-level list in
  `resources.yaml`. URLs are verified weekly like every other resource.
- `## Opinionated minimal stack` and its template are deleted.
- The render-on-edit CI gate diffs `README.md` instead of
  `agentic-engineering.md`.

## Constraints

### Must

- Use the existing renderer architecture (Jinja2 templates +
  `resources.yaml` data). No new tools or libraries.
- `worth_following` URLs go through the existing verifier — no parallel
  verification path.
- The render-on-edit gate keeps its current trigger paths
  (`resources.yaml`, `templates/`, `scripts/render.py`,
  `pyproject.toml`); only the diff target changes.
- Each PR (T1, T2) leaves the repo in a working state — tests pass,
  renderer succeeds, CI gate green.

### Must Not

- Don't add a stub `agentic-engineering.md` redirect file. Clean break.
- Don't restructure existing topic sections (1–14) or the top-7 list.
- Don't edit historical task files in
  `docs/specs/self-maintaining-guide/`. They describe what was built.
- Don't strip `verified_at` from `resources.yaml` or remove
  `format_link`'s `archived` suffix logic — they remain useful beyond
  the dropped `✓` mark.

### Out of Scope

- Restructuring topic sections.
- Schema additions beyond `worth_following` (e.g., `picks`, `tags`).
- Scout changes — `worth_following` stays human-curated; scout proposes
  posts in topic sections, not feeds.
- A `docs/specs/` index or per-spec `README.md` (defer until ≥5 specs).

## Current State

- Renderer: `scripts/render.py`. Reads `resources.yaml` + `templates/`,
  writes `agentic-engineering.md`. Computes `compilation_month_year`
  from max `verified_at`. `format_link` adds `✓` when a resource's
  `verified_at` is within 90 days.
- Templates: `header.md` (Jinja2 var: `compilation_month_year`),
  `top_7.md`, `sections.md.j2`, `opinionated_stack.md`, `caveats.md`.
- Tests: `tests/test_render.py`.
- CI gate: `.github/workflows/render-on-edit.yml` runs
  `git diff --exit-code agentic-engineering.md` on render-relevant
  changes.
- `worth_following` URLs to add (caveats bullet 4 had names only):
  - Anthropic Engineering — `https://www.anthropic.com/engineering`
  - Cognition blog — `https://cognition.ai/blog`
  - Simon Willison — `https://simonwillison.net/`
  - Embrace The Red — `https://embracethered.com/blog/`
  - Cloudflare AI agents tag — `https://blog.cloudflare.com/tag/ai-agents/`
  - LangChain blog — `https://blog.langchain.com/`
  - Hamel's blog — `https://hamel.dev/`

## Tasks

### T1: Render content cleanup

**What:** Strip artifacts, add the `worth_following` section, and add
this spec + supersession note. Output target stays
`agentic-engineering.md` for this PR — the file is regenerated with
the new content so the diff is reviewable.

- Templates:
  - Rewrite `templates/header.md`: drop the `compilation_month_year`
    line and the `URLs marked ✓...` preamble. Subtitle becomes:
    `_Backend & infrastructure focus. Agent/language-agnostic. Continuously maintained — links verified weekly._`
  - Rename `templates/caveats.md` → `templates/worth_following.md`.
    Replace contents with a Jinja2-iterated `## Worth following for ongoing signal`
    section that renders each entry as a Markdown bullet.
  - Delete `templates/opinionated_stack.md`.
- Renderer (`scripts/render.py`):
  - Remove `is_verified`, `VERIFICATION_WINDOW_DAYS`, and the `mark`
    branch in `format_link` and `render_top_7_line`.
  - Remove `compute_compilation_month_year` and the
    `compilation_month_year` kwarg passed to `header.md`.
  - Remove the `_render("opinionated_stack.md")` call and the `---`
    separator that surrounded it.
  - Replace `_render("caveats.md")` with
    `_render("worth_following.md", worth_following=data["worth_following"])`.
- Data (`resources.yaml`): add a top-level `worth_following:` list
  with the 7 feeds (`name`, `url`, `blurb`).
- Tests (`tests/test_render.py`): drop assertions about `✓`, `Compiled`,
  and opinionated-stack content. Add an assertion that the rendered
  output contains `## Worth following for ongoing signal` and at least
  one feed URL. Add a defensive assertion that no ` ✓` mark appears.
- Spec metadata: this file lands at
  `docs/specs/readme-as-rendered-output.md`. Add supersession note at
  the top of `docs/specs/self-maintaining-guide/SPEC.md`.

**Files:** `scripts/render.py`, `templates/header.md`,
`templates/worth_following.md` (renamed from `caveats.md`),
`templates/opinionated_stack.md` (deleted), `resources.yaml`,
`tests/test_render.py`, `agentic-engineering.md` (regenerated),
`docs/specs/readme-as-rendered-output.md` (new),
`docs/specs/self-maintaining-guide/SPEC.md` (supersession note).
Larger than the template's ≤3-file guideline because the changes are
tightly coupled — splitting produces broken intermediate states.

**Verify:**
- `uv run pytest tests/test_render.py` — all green.
- `uv run python scripts/render.py` — exits 0.
- `agentic-engineering.md` no longer contains `Compiled`, ` ✓`, or
  `Opinionated minimal stack`.
- `agentic-engineering.md` contains `## Worth following for ongoing signal`
  with 7 bulleted links.
- `git diff --exit-code agentic-engineering.md` — clean (rendered
  output committed).

### T2: Switch render target to README.md

**What:** Move the rendered guide from `agentic-engineering.md` to
`README.md`. Pure migration — no content changes from T1.

- `scripts/render.py`: change
  `OUTPUT_PATH = ROOT / "agentic-engineering.md"` to
  `OUTPUT_PATH = ROOT / "README.md"`. Update module docstring.
- Run `uv run python scripts/render.py` to regenerate `README.md`
  (full overwrite of the current pointer-only README).
- `git rm agentic-engineering.md`.
- `.github/workflows/render-on-edit.yml`: change
  `git diff --exit-code agentic-engineering.md` to
  `git diff --exit-code README.md`. Trigger paths unchanged.
- `resources.yaml`: update line-2 comment (`update agentic-engineering.md`
  → `update README.md`).
- `tests/test_render.py`: update any references to `OUTPUT_PATH`'s old
  basename.

**Files:** `scripts/render.py`, `README.md` (regenerated, fully
overwritten), `agentic-engineering.md` (deleted),
`.github/workflows/render-on-edit.yml`, `resources.yaml`,
`tests/test_render.py`.

**Verify:**
- `uv run pytest` — all green.
- `uv run python scripts/render.py && git diff --exit-code README.md`
  — clean.
- `agentic-engineering.md` does not exist.
- GitHub renders the new README at the repo home.
- `.github/workflows/render-on-edit.yml` shows `README.md` as diff
  target.

## Validation

End-to-end after T2 lands:

- The repository home page on GitHub shows the curated guide as the
  README.
- The guide contains: top-7 list, sections 1–14 with all resources, and
  a `## Worth following for ongoing signal` section at the bottom with
  7 feed links. No `✓` marks. No "Compiled" header. No opinionated
  stack.
- Edit a resource in `resources.yaml`, run
  `uv run python scripts/render.py`, observe `README.md` updates
  accordingly.
- Open a PR that touches `resources.yaml` without re-rendering — the
  render-on-edit gate fails on `README.md` diff.
- Next weekly verifier run picks up `worth_following` URLs (since they
  live in `resources.yaml`); broken ones surface in the verifier's PR
  like every other resource.
