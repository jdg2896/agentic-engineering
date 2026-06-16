# SPEC — self-maintaining agentic-engineering resource guide

_Status: v1 design, signed off via /grill-me on 2026-05-03._

> **Note:** For decisions about the renderer's output target and surfaced artifacts (`✓` marks, header date, opinionated stack), see [`readme-as-rendered-output.md`](readme-as-rendered-output.md) (2026-05-03).

## 1. Goal

Turn `agentic-engineering.md` from a hand-edited Markdown file into a **self-maintaining curated resource guide** for engineers building agentic systems across disciplines (FE, BE, infra, QA, data). Two automated agents keep it fresh:

- **Verifier** — checks every URL is still live and canonical; opens a PR when something breaks or migrates.
- **Scout** — periodically scans a curated list of reputable feeds and proposes new resources via PR.

The Markdown doc becomes a _rendered artifact_ of structured data + hand-written prose templates. Editorial judgment stays human; mechanical maintenance does not.

## 2. Architecture at a glance

```
resources.yaml           ← source of truth for resource entries (data)
sources.yaml             ← feeds the scout monitors
scout/seen.yaml          ← tombstones for proposed-and-decided URLs
templates/*.md           ← hand-written prose (intro, top-7 framing, stack table, caveats, footer)
scripts/render.py        ← renders agentic-engineering.md from data + templates
scripts/verify_links.py  ← URL health checks (no LLM)
scripts/scout.py         ← fetches feeds, calls Claude, emits candidates (uses LLM)
agentic-engineering.md   ← generated; committed for GitHub rendering
.github/workflows/
  verify-links.yml       ← weekly (Monday)
  scout-resources.yml    ← weekly (Thursday), skip empty runs
  render-on-edit.yml     ← rebuild + diff check on PRs touching resources.yaml
```

**Cheap script vs. expensive judgment.** The verifier is pure HTTP — no LLM, free. The scout is the only place an LLM is invoked, and only for judgment (fit, categorization, blurb).

## 3. Data model

### 3.1 `resources.yaml` — source of truth

Single file. Keyed on a hand-assigned `slug` ID that never changes (URLs change; the slug is identity).

```yaml
sections:
  - id: foundations
    order: 1
    title: "Foundational design & 'what is an agent'"
    description: |
      Mental models for what an agent is, when to build one vs. a workflow,
      context engineering, and the canonical "agents are mostly software" doctrine.
  # ... 13 more sections, in current document order

top_7: # explicit ordered list of resource ids
  - building-effective-agents
  - effective-context-engineering
  - twelve-factor-agents
  - multi-agent-research-system
  - dont-build-multi-agents
  - openai-practical-guide
  - applied-llms-year

resources:
  - id: building-effective-agents # slug, stable, never changes
    section: foundations # FK → sections[].id
    url: https://www.anthropic.com/research/building-effective-agents
    title: Building Effective Agents
    author: "Anthropic (Schluntz/Zhang)"
    type: article # article|paper|docs|repo|video|spec|course|book
    license: null # OS|SaaS|OS+SaaS|proprietary|null — only set for tools/products
    blurb: |
      The "workflows vs agents" mental model that everything else builds on.
    cluster: null # optional cluster-id; multiple resources sharing one render the same bullet
    tags: [canonical, mental-model] # free-form
    added_at: 2026-05-03
    verified_at: 2026-05-03 # last time URL was confirmed live + canonical
    archived: false # upstream is dead but historically important
    paywall: false # verifier won't false-positive on auth gates
    superseded_by: null # optional id of replacement
    first_dead_at: null # date of first consecutive dead check; cleared on any non-dead outcome
    quarantined_at: null # set by verifier when (today - first_dead_at) >= 21 days; renderer skips entry
    quarantine_reason: null # status_code or error string captured at quarantine time
    notes: null # private, never rendered
```

The three fields `first_dead_at`, `quarantined_at`, and `quarantine_reason` are written by the verifier (see `docs/specs/auto-quarantine-dead-links.md`). They also apply to `worth_following:` entries, which the verifier matches by `url`.

**Multi-link bullets** (e.g., the two awesome-mcp-servers registries, Promptfoo OS+SaaS): one resource per URL, share a `cluster` id. Renderer combines clustered resources into a single bullet.

**Top-7 ordering** lives in the file-level `top_7` list, not as a per-resource field — single source of truth, easy to reorder.

### 3.2 `sources.yaml` — what the scout monitors

```yaml
sources:
  - id: anthropic-engineering
    type: rss # rss|atom|github-releases|github-tag-feed|html-index
    url: https://www.anthropic.com/engineering/rss.xml
    cadence: weekly # advisory; the workflow runs weekly anyway
    last_checked_at: 2026-05-03
    enabled: true
    notes: null
  - id: simonwillison-ai-agents
    type: atom
    url: https://simonwillison.net/tags/ai-agents/atom/
    last_checked_at: 2026-05-03
    enabled: true
  - id: langgraph-releases
    type: github-releases
    url: https://github.com/langchain-ai/langgraph/releases.atom
    last_checked_at: 2026-05-03
    enabled: true
  # initial set drawn from the "Worth following for ongoing signal" footer
```

**v1 initial sources** (~20 entries) drawn from the existing "Worth following" footer plus GitHub release feeds for tracked frameworks (LangGraph, Inspect AI, Letta, Agent SDKs, etc.). Curated by hand on first commit.

### 3.3 `scout/seen.yaml` — tombstones

Prevents the scout from re-proposing the same item every week.

```yaml
seen:
  - url: https://example.com/some-blog-post
    first_seen_at: 2026-05-03
    decided_at: 2026-05-04
    decision: rejected # added|rejected|deferred
    reason: "Generic listicle; doesn't meet curation bar"
    proposed_for_section: foundations
    source: simonwillison-ai-agents
  - url: https://www.anthropic.com/engineering/some-new-piece
    first_seen_at: 2026-05-03
    decided_at: 2026-05-05
    decision: added
    resource_id: some-new-piece # slug it was added under
```

When a candidate is added via PR merge, a follow-up step appends to `seen.yaml`. When a candidate PR is closed without merge, mark `rejected` with the reason from the PR comment.

## 4. Render pipeline

`scripts/render.py` reads `resources.yaml` and the templates in `templates/` to produce `agentic-engineering.md`.

**Templates** wrap the generated lists:

- `templates/header.md` — title + compilation date + caveats blurb at top
- `templates/top_7.md` — the "If you only read 7 things" intro line; the list itself is generated
- `templates/sections_intro.md` — optional per-section intro paragraphs (most are blank in v1)
- `templates/opinionated_stack.md` — hand-written table at the bottom
- `templates/caveats.md` — caveats + "Worth following" footer

**Render rules:**

- Sections rendered in `order`.
- Within a section, resources rendered in the order they appear in `resources[]` (manual ordering preserved).
- A `cluster` of N resources renders as one bullet with N links joined `, `, with one shared blurb (taken from the first member; or a `cluster_blurb` field — TBD if needed).
- The `verified_at` mark (`✓`) is rendered if `verified_at` is within the last 90 days. Older verifications drop the mark — the doc honestly reflects what's been spot-checked recently.
- `archived: true` resources render with `_(archived)_` suffix.
- `superseded_by`-set resources are hidden from rendering (or rendered with strikethrough — TBD; default hidden).
- Compilation date in the header is auto-set to the most recent `verified_at` across all entries.

**Render runs in CI on every PR** that touches `resources.yaml`, `templates/`, or `sources.yaml`. The workflow regenerates `agentic-engineering.md` and fails the check if the committed file doesn't match — so the rendered output is always in sync with the data and reviewable in the same PR diff.

## 5. Workflows

### 5.1 `verify-links.yml`

- **Trigger:** `cron: '0 13 * * 1'` (Mondays 13:00 UTC) + `workflow_dispatch`.
- **Permissions:** `contents: write`, `pull-requests: write`.
- **First step:** read `MAINTENANCE_PAUSED` repo variable; if `true`, log and exit 0.
- **Steps:**
  1. Checkout.
  2. Run `scripts/verify_links.py` over every entry in `resources.yaml`:
     - Issue `HEAD` (fall back to `GET` on 405); follow up to 5 redirects.
     - Categorize each URL: `ok` | `dead` (4xx/5xx terminal) | `migrated` (final URL has different host or path-prefix from the recorded URL) | `paywall` (skipped if `paywall: true` on the resource).
     - On `ok`: bump `verified_at` to today.
     - On `dead` / `migrated`: leave `verified_at` untouched; record details in a per-run report.
  3. Run `scripts/render.py` to update `agentic-engineering.md`.
  4. Use `peter-evans/create-pull-request@v7` to open/update PR on stable branch `bot/verify-YYYY-WW`:
     - Title: `Weekly verification — N ok, M dead, K migrated`.
     - Body: three sections (verified / dead / migrated), each a checklist with details (URL, status code, final URL, suggested action).
     - Skip PR creation entirely if there is no diff (when nothing needed updating).
- **No LLM calls.** Cost: free.

### 5.2 `scout-resources.yml`

- **Trigger:** `cron: '0 13 * * 4'` (Thursdays 13:00 UTC) + `workflow_dispatch`.
- **Permissions:** `contents: write`, `pull-requests: write`.
- **First step:** read `MAINTENANCE_PAUSED`; exit 0 if `true`.
- **Steps:**
  1. Checkout.
  2. Run `scripts/scout.py`:
     - For each enabled source in `sources.yaml`, fetch the feed (RSS/Atom/GitHub-releases).
     - Filter to items newer than `last_checked_at` for that source.
     - Diff against `scout/seen.yaml` (drop already-seen URLs).
     - For each remaining candidate, call Claude (Haiku 4.5 by default; Sonnet 4.6 if quality issues observed) with a structured-output prompt:
       - System prompt includes: section list with descriptions, house-style blurb examples (3-5 from existing doc), inclusion criteria from the doc's caveats.
       - User prompt: candidate URL, title, snippet, source.
       - Required output: `{decision: include|reject, section: <id>, slug: <proposed>, blurb: <one line>, type: <enum>, license: <enum|null>, tags: [...], rationale: <one line>}`.
       - Use **prompt caching** on the system prompt (sections + style examples) — single 5-min TTL window covers all candidates in one run.
     - Bump `last_checked_at` on each source.
     - Emit `candidates.yaml` with all `decision: include` items.
     - Append `decision: reject` items to `scout/seen.yaml` immediately (tombstoned with reason).
  3. **Skip-empty-runs:** if `candidates.yaml` is empty, commit only the `last_checked_at` bumps to a side branch and exit without opening a PR.
  4. Otherwise, render proposed additions into `resources.yaml` (appended to the right section), regenerate `agentic-engineering.md`, and open/update PR on `bot/scout-YYYY-WW`:
     - Title: `Scout — N candidates`.
     - Body: per-candidate checklist with title, URL, source, proposed section, proposed blurb, rationale. Reviewer drops candidates by editing the YAML in the PR.
- **LLM calls:** one structured-output call per candidate (~2k cached input + ~500 output). Estimated cost at 30 sources, ~3 candidates/week average: **~$0.05-$0.20/month**.

### 5.3 `render-on-edit.yml`

- **Trigger:** `pull_request` on `main` when paths `resources.yaml`, `sources.yaml`, `templates/**`, or `scripts/render.py` change.
- **Permissions:** `contents: read`.
- **Steps:** run `scripts/render.py`; `git diff --exit-code agentic-engineering.md` — fail if not in sync.

## 6. Identity, auth, kill switch

- **PR identity:** default `github-actions[bot]`. No GH App in v1.
- **Token:** `GITHUB_TOKEN` is sufficient for in-repo PR creation. No PAT.
- **Anthropic API key:** repo secret `ANTHROPIC_API_KEY`, scoped to scout workflow only.
- **Kill switch:** repo _variable_ `MAINTENANCE_PAUSED` (default `false`). Both maintenance workflows check first, exit cleanly if `true`. Toggle via Settings → Variables or `gh variable set MAINTENANCE_PAUSED -b true`.
- **Branch protection on `main`:** require PR; no required reviewers (single human).

## 7. Trust model

**No auto-merge in v1.** Every change goes through human review. Reasoning:

- The verifier's "still canonical" check (status 200 + redirect-host equality) can be fooled by parked-domain or "we've moved" stubs returning 200. Auto-merging `verified_at` bumps risks silently certifying rot.
- Cost of review for safe diffs is negligible (one-click squash-merge).

**v2 candidate (deferred):** auto-merge `verified_at`-only diffs once the verifier includes a content sanity check (title-substring match, or a small Claude-as-judge call). Encode in a separate `auto-merge.yml` gated on:

- PR author = `github-actions[bot]`,
- Touched files ⊆ `{resources.yaml}`,
- Diff scope = `verified_at` lines only,
- Passing checks.

**PR shape:** one batch PR per workflow run, not one PR per URL. Stable branch names ensure re-runs update the existing PR rather than spawning duplicates.

## 8. Implementation choices

- **Language:** Python. Anthropic SDK is most mature there; `feedparser` and `requests` cover the verifier and scout. (Swappable if a strong preference exists; not load-bearing.)
- **Anthropic model:** **Sonnet 4.6** (`claude-sonnet-4-6`) for the scout. At ~3 candidates/week the cost delta vs. Haiku is ~$0.06/month — not a real tiebreaker — and the scout's task (editorial judgment + house-style blurb imitation) is exactly where Sonnet pulls ahead. The asymmetric risk matters too: a false-positive is visible in the PR and easy to reject; a false-negative is silently tombstoned in `seen.yaml` and you never see it. Better calibration is the right trade.
  - Haiku 4.5 reserved for any future high-volume mechanical sub-task (e.g., a pre-filter sieve before judgment) where false-negatives are recoverable.
  - Opus 4.7 deferred to v2 if a deep-judgment pass on borderline rejects is added.
- **Prompt caching:** required on the scout's system prompt (sections + style examples). 5-min TTL covers a single workflow run.
- **`peter-evans/create-pull-request@v7`** for all PR creation. Stable branch names per run-window.

## 9. v1 implementation checklist

1. **Migration** — convert current `agentic-engineering.md` → `resources.yaml` + `templates/`. One-time, hand-curated. Assign slugs.
2. **Renderer** — `scripts/render.py`. Acceptance: round-trip the existing doc to within trivial whitespace.
3. **`render-on-edit.yml`** — keeps the rendered MD honest from day one.
4. **Verifier** — `scripts/verify_links.py` + `verify-links.yml`. Acceptance: opens a PR with realistic ok/dead/migrated counts on the existing dataset.
5. **Branch protection** on `main`; create `MAINTENANCE_PAUSED` variable (default `false`); add `ANTHROPIC_API_KEY` secret.
6. **`sources.yaml`** — initial ~20 entries from the "Worth following" footer + GitHub release feeds.
7. **Scout** — `scripts/scout.py` + `scout-resources.yml`. Includes seen-set logic, prompt caching, structured output, skip-empty-runs.
8. **Dry-run scout once** with `workflow_dispatch` before enabling cron; tune house-style prompt examples based on output.

## 10. Deferred to v2+

- Auto-merge `verified_at`-only diffs (after verifier adds content sanity check).
- Anchor / URL-fragment validation.
- Domain-migration _target-finding_ (when a 404 fires, ask Claude to find the new canonical URL).
- Supersession detection (newer post by same author replacing older).
- Semantic dedup on candidates (embedding similarity vs. URL-only).
- Human-in-the-loop conversational triage agent — natural fit for a Claude Code routine, not GH Actions.
- Dedicated GitHub App identity (only if pattern is replicated to other repos or PR branding starts to matter).
- Statistics dashboard (avg age per section, % verified in last 90 days, decay heatmap).

## 11. Risk register

| Risk                                                | Mitigation                                                                                                    |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Verifier false-positive on parked-domain 200 OK     | No auto-merge in v1; v2 content sanity check                                                                  |
| Scout re-proposes already-rejected items            | `seen.yaml` tombstones keyed on URL                                                                           |
| Anthropic API outage during scout run               | Job fails; retries next week; no harm                                                                         |
| Source feed disappears                              | Per-source try/except in scout; error logged, others continue                                                 |
| Duplicate bot PRs                                   | Stable branch name + peter-evans dedupe                                                                       |
| Bot opens PR with bad data while you're on holiday  | `MAINTENANCE_PAUSED=true` kill switch, single click                                                           |
| Render-on-edit blocks all PRs if renderer is broken | Treat renderer breakage as a P0; fix-forward via direct push (allowed under "single human" branch protection) |

---

## Tasks

### T0 — Repo scaffolding

**Branch:** `t0/scaffolding`
**PR title:** `T0: repo scaffolding`
**Depends on:** —
**SPEC references:** §2 (architecture), §8 (implementation choices)

#### Goal

Set up the Python project structure so subsequent tasks have a working build environment. No application logic in this task.

#### Scope

Create:

- **`pyproject.toml`** — `uv`-managed project, Python `>=3.11`. Dependencies:
  - `pyyaml` — YAML reading/writing
  - `requests` — HTTP for verifier
  - `feedparser` — RSS/Atom for scout
  - `anthropic` — Claude SDK (pin to a recent version)
  - `jinja2` — template rendering
  - Dev: `pytest`, `ruff`
- **`.gitignore`** — Python standard (`__pycache__/`, `*.pyc`, `.venv/`, `dist/`, `*.egg-info/`) plus `.env`, `.env.local`, `verification_report.json`, `candidates.yaml` (these are CI artifacts, not source).
- **Empty directories** with `.gitkeep` files so they're committed:
  - `scripts/`
  - `templates/`
  - `tests/`
  - `scout/`
- **Update `README.md`** to add two short lines:
  - one pointing at `SPEC.md` for the design
  - one pointing at `tasks/` for the implementation breakdown

#### Acceptance

- `uv sync` succeeds from a clean clone.
- `uv run python -c "import yaml, requests, feedparser, anthropic, jinja2"` succeeds.
- All listed directories exist in the repo (committed via `.gitkeep` if otherwise empty).
- `git status` is clean after `uv sync` (i.e., `uv.lock` is committed).

#### Out of scope

- Any script in `scripts/` (T1+).
- Any template in `templates/` (T1).
- Any workflow in `.github/workflows/` (T3+).
- Pre-commit hooks.
- CONTRIBUTING.md or other docs.

#### Notes

- If `uv` isn't installed in the dev environment, install instructions go in the PR body, not in `README.md` (the README is reader-facing, not contributor-facing in v1).
- Pin Python via `requires-python = ">=3.11"` in `pyproject.toml`. Don't add a `.python-version` file unless `uv` requires it.

---

### T1 — Schema + renderer + 3 sections migrated

**Branch:** `t1/schema-renderer`
**PR title:** `T1: schema, renderer, 3 sections migrated`
**Depends on:** T0
**SPEC references:** §3 (data model), §4 (render pipeline)

#### Goal

Establish the structured data schema, build the renderer, and prove correctness by round-tripping the first three sections of the existing `agentic-engineering.md` (the "If you only read 7" list, section 1 "Foundational design", section 2 "Tool integration & MCP") through the renderer to a byte-identical (whitespace-tolerant) match against the original.

#### Scope

##### `resources.yaml`

- All 14 sections in the `sections:` list, each with `id`, `order`, `title`, and a hand-written 2-3 sentence `description` that captures what the section is about. The `description` is what the scout's LLM uses for categorization in T6, so make it concrete.
- `top_7:` — the ordered list of 7 resource slugs from the existing "If you only read 7 things" section.
- `resources:` — populated **only** for sections `foundations` and `tools-mcp`. Every entry hand-migrated with all required fields per SPEC §3.1.

##### `templates/`

Hand-written prose templates that wrap the generated lists. Use Jinja2.

- **`header.md`** — title, the opinionated quality-bar subtitle (`_Opinionated. No tutorials, no listicles, no marketing. Continuously maintained — links verified weekly._`), compilation-date placeholder (renderer fills with the most recent `verified_at`), the verification-mark caveat blurb at top.
- **`top_7.md`** — the "If you only read 7 things" intro line + numbered list rendered from the `top_7` slugs (resolved against `resources[]`).
- **`sections.md.j2`** — body template, iterates `sections` in order, rendering each section's resources as a Markdown bullet list.
- **`opinionated_stack.md`** — the table at the bottom of the current doc, copy-pasted verbatim. Hand-written, not generated.
- **`caveats.md`** — caveats + "Worth following for ongoing signal" footer, copy-pasted verbatim.

##### `scripts/render.py`

- Reads `resources.yaml`.
- Loads templates via Jinja2.
- For each section in order, renders its resources. Empty sections (no resources yet) are **skipped** (not rendered as headers) — T2 will fill them.
- **Cluster handling:** resources sharing a `cluster` id render as a single bullet, with their links comma-separated, sharing one blurb (taken from the first member of the cluster in document order).
- **Verification mark (`✓`):** rendered if `verified_at` is non-null AND within the last 90 days.
- **Archived:** entries with `archived: true` render with a `_(archived)_` suffix.
- **Superseded:** entries with `superseded_by` non-null are hidden from rendering.
- **Compilation date** in header set to the maximum `verified_at` across all entries.
- Output: writes `agentic-engineering.md`.
- Idempotent: running twice produces no diff.

#### Acceptance

- `uv run python scripts/render.py` succeeds with no errors.
- For the populated portion (top_7 + foundations + tools-mcp), the generated `agentic-engineering.md` matches the corresponding parts of the original `agentic-engineering.md` after normalizing trailing whitespace and trailing newlines. Verify with:
  ```bash
  diff <(grep -v '^$' agentic-engineering.md) <(grep -v '^$' /tmp/original.md)
  ```
  (Save the original somewhere before regenerating; the populated portion should match.)
- Sections 3-14 render as just the section header (or are skipped — your choice, document it).
- `uv run pytest tests/` passes (add a basic `tests/test_render.py` if convenient, but not required for T1).

#### Out of scope

- Migration of sections 3-14 (T2).
- CI gate (T3).
- Any verifier or scout logic.
- Any workflow files.

#### Notes

- **Slug convention:** kebab-case, derived from title or canonical short-name. Examples: `building-effective-agents`, `effective-context-engineering`, `mcp-spec`, `mcp-servers-repo` (for `modelcontextprotocol/servers`). Keep them human-readable.
- **`verified_at` for migrated entries:**
  - For ✓-marked entries in the original: set `verified_at: 2026-05-03` (the doc's compile date).
  - For non-✓ entries: set `verified_at: null`. This makes them not render with ✓ and makes T4's verifier the thing that fills them in over time.
- **Multi-link bullets in the original** (e.g., the `wong2/awesome-mcp-servers` and `appcypher/awesome-mcp-servers` line) → split into two resource entries sharing a `cluster: awesome-mcp-servers` id.
- **Preserve voice exactly.** Don't paraphrase blurbs — copy them verbatim from the original. Voice consistency is the whole point of structured data.
- **Section descriptions are the one place you write new prose.** Look at the resources in each section to inform the description. These get used by the scout in T6 for categorization.

---

### T2 — Bulk migration (sections 3-14)

**Branch:** `t2/bulk-migration`
**PR title:** `T2: bulk-migrate sections 3-14`
**Depends on:** T1
**SPEC references:** §3 (data model)

#### Goal

Mechanically migrate the remaining sections of `agentic-engineering.md` (sections 3 through 14) into `resources.yaml`, following the schema and conventions established by T1.

#### Scope

For each section 3-14 in the existing `agentic-engineering.md`:

- Add resource entries to `resources.yaml` under the section's existing `id`.
- Use the **same conventions as T1** for slugs, blurb verbatim-copy, `verified_at` (`2026-05-03` for ✓; `null` otherwise), `cluster` for multi-link bullets, `tags`, etc.
- **Set `license:` for tool/product entries.** Sections that have OS/SaaS markers (notably section 9 "Evaluation frameworks", section 10 "Observability", section 7 "Inference & gateway"): copy those markers into the `license` field as `OS`, `SaaS`, or `OS+SaaS`.
- **Map `type:` accurately:**
  - Anthropic engineering posts → `article`
  - arXiv papers → `paper`
  - GitHub repos (e.g., `modelcontextprotocol/servers`) → `repo`
  - Documentation portals (e.g., `LangGraph overview`) → `docs`
  - YouTube videos → `video`
  - Specifications (e.g., MCP spec) → `spec`
  - Books, courses → `book`, `course`

#### Acceptance

- After running `uv run python scripts/render.py`, the generated `agentic-engineering.md` round-trips against the **full original** with only whitespace differences. Diff command:
  ```bash
  diff <(grep -v '^$' agentic-engineering.md) <(grep -v '^$' <reference-copy>)
  ```
- All ~120 entries have **unique** slug IDs. Verify with:
  ```bash
  uv run python -c "import yaml; d=yaml.safe_load(open('resources.yaml')); ids=[r['id'] for r in d['resources']]; assert len(ids)==len(set(ids)), 'duplicate slugs'"
  ```
- Every resource has the required fields populated (id, section, url, title, author, type, blurb, added_at).
- All `section` references resolve to a valid section `id`.

#### Out of scope

- Schema changes — if the renderer needs minor extensions for an edge case (e.g., a third type of cluster grouping), make the extension and call it out clearly in the PR body. Don't reshape the schema.
- New resources beyond what's in the current `agentic-engineering.md`.
- Any workflow or CI changes (T3).
- Updating verification dates beyond the migration default — that's the verifier's job.

#### Notes

- **Voice preservation is non-negotiable.** Copy blurbs verbatim. The only acceptable edits are converting bracketed Markdown links into the `url:` field and stripping the leading author/title that's already captured in `author:`/`title:`.
- **Cluster examples to watch for:**
  - `wong2/awesome-mcp-servers` and `appcypher/awesome-mcp-servers` (section 2).
  - `temporal-community/temporal-ai-agent` is standalone, but the two preceding Temporal blog posts could plausibly cluster — judgment call; if uncertain, leave as separate entries.
  - The `getzep/graphiti` entry in section 5 sits next to the parent Zep entry — keep separate unless the original prose treats them as one.
- **Benchmarks line in section 9** is a long compound bullet listing many benchmarks. Either:
  - Split into one resource per benchmark with `cluster: benchmarks-9`, OR
  - Keep as a single entry with a multi-link blurb. **Recommend splitting** so each benchmark's URL has its own lifecycle.
- **"Worth following for ongoing signal"** in the footer of the current doc is **not** part of `resources.yaml` — it's editorial prose handled by `templates/caveats.md`. Don't migrate it as resources. (T5 will use it as the seed for `sources.yaml`.)
- If you find a typo or broken thing in the original doc while migrating, **don't fix it in this PR** — flag it in the PR description and let it be a follow-up.

---

### T3 — Render-on-edit CI gate

**Branch:** `t3/render-on-edit`
**PR title:** `T3: render-on-edit CI gate`
**Depends on:** T2
**SPEC references:** §4 (last paragraph), §5.3

#### Goal

Add a CI gate that ensures `agentic-engineering.md` is always in sync with `resources.yaml` + templates. Any PR that touches the data side without re-rendering must fail the check.

#### Scope

##### `.github/workflows/render-on-edit.yml`

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

##### `tests/test_render.py` (small)

- One test that asserts `render.py` is idempotent: runs it, captures output, runs it again, asserts no change.
- Optional: a snapshot test against a small fixture in `tests/fixtures/` — only if convenient. Not required.

#### Acceptance

- Workflow file lints (use `actionlint` if available locally; not required to install).
- Manual test in this PR: include a deliberately stale `agentic-engineering.md` in one commit, push, observe the CI fail, fix in a follow-up commit, observe pass. Document the test in the PR body. (Or skip the manual test and rely on the next real PR to validate.)
- `uv run pytest tests/test_render.py` passes locally.

#### Out of scope

- Verifier or scout workflows (T4, T6).
- Branch protection setup (manual; handled in T4 PR).
- Auto-formatting or any other CI checks (linting, type-checking) — keep this workflow focused on the render gate. Add separate workflows later if needed.

#### Notes

- Use a **frozen** uv install (`--frozen`) to ensure CI matches local. Requires `uv.lock` to be committed (T0 acceptance).
- Don't add `push` as a trigger. We only care about PRs — `main` is protected and accepts merges only via PR (configured in T4).
- The workflow runs on every PR touching the listed paths, even drafts. That's intentional — fast feedback.

---

### T4 — Verifier (script + workflow + ops)

**Branch:** `t4/verifier`
**PR title:** `T4: link verifier + weekly workflow`
**Depends on:** T3
**SPEC references:** §5.1, §6 (kill switch, branch protection), §7 (trust model — no auto-merge in v1)

#### Goal

Build the link-health verifier. No LLM calls — pure HTTP. Runs weekly on Mondays, opens a batch PR with status changes.

#### Scope

##### `scripts/verify_links.py`

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

##### `.github/workflows/verify-links.yml`

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

##### Manual setup checklist (include verbatim in the PR body for T4)

- [ ] Create repo **variable** `MAINTENANCE_PAUSED` (default value: `false`). Settings → Secrets and variables → Actions → Variables → New repository variable.
- [ ] Enable branch protection on `main`: Settings → Branches → Branch protection rules → require pull request before merging; no required reviewers (single-reviewer repo); require status checks: `render-on-edit`.
- [ ] Verify the workflow appears in the Actions tab and `workflow_dispatch` runs successfully.

#### Acceptance

- `uv run python scripts/verify_links.py --dry-run` succeeds locally and produces a coherent report against the full migrated `resources.yaml`. Most entries should be `ok`; a handful may be `migrated` or `dead` (these will be the first PR's content once the workflow runs).
- Workflow file passes `actionlint` if available; otherwise eyeball-review.
- The PR body includes the manual setup checklist verbatim.

#### Out of scope

- Auto-merge of `verified_at`-only diffs (deferred to v2 — see SPEC §7).
- Content sanity check (e.g., title-substring match) — deferred to v2.
- Anchor / fragment validation — deferred to v2.
- Migration target-finding (suggesting the new URL when something migrates) — deferred to v2.
- `ANTHROPIC_API_KEY` setup — verifier doesn't call any LLM.

#### Notes

- **YAML round-tripping is the bear.** PyYAML default dumping will reorder keys and lose comments. Use `ruamel.yaml` if preserving formatting matters. Easiest path: use `ruamel.yaml` with `preserve_quotes=True`, default flow style block. Acceptable degradation: reformat the whole file, accept the one-time diff in this PR.
- **Paywall detection** in v1 is just "this resource is marked `paywall: true`, so don't flag 401/403 as dead." We're not actively detecting paywall walls.
- **Migration detection** in v1 is heuristic (host+path comparison). It will produce some false positives (e.g., a redirect from `http://` to `https://` should NOT be flagged as migrated — normalize scheme before comparing). Add a few obvious normalizations: trailing slash, `http→https`, `www.` prefix.
- **Don't try to fix migrations automatically** in v1. The PR shows you the new URL; you decide whether to update the `url` field manually before merging.
- **The first run will be loud** — many of the unverified-at-migration entries will become `ok` and bump `verified_at`. That's the point. Subsequent runs will be quiet.

---

### T5 — Scout sources seed

**Branch:** `t5/sources-seed`
**PR title:** `T5: scout sources seed`
**Depends on:** T0 (independent of T1-T4 in principle; conventionally done after T4)
**SPEC references:** §3.2 (sources.yaml schema)

#### Goal

Curated initial set of feeds the scout will monitor. **This is an editorial decision, not a mechanical one** — the AI proposes, the human approves in PR review. Don't blindly merge a long list.

#### Scope

##### `sources.yaml`

Hand-curate ~15-20 entries seeded primarily from the "Worth following for ongoing signal" footer of the current `agentic-engineering.md`, plus GitHub release feeds for tracked frameworks.

**Recommended seed set** (each entry: `id`, `type`, `url`, `cadence: weekly`, `last_checked_at: <today>`, `enabled: true`):

**Author/blog feeds:**

- Anthropic Engineering blog (RSS/Atom)
- Cognition blog
- Simon Willison `ai-agents` tag (Atom)
- Embrace The Red blog (Johann Rehberger)
- Cloudflare Agents tag
- LangChain blog
- Hamel Husain's blog
- Eugene Yan's blog
- Chip Huyen's blog (occasional, but worth)

**GitHub release feeds** (`https://github.com/<repo>/releases.atom`):

- `langchain-ai/langgraph`
- `openai/openai-agents-python`
- `UKGovernmentBEIS/inspect_ai`
- `letta-ai/letta` (or whatever the canonical repo is — verify)
- `microsoft/autogen` (or successor Agent Framework)
- `google/adk-python`
- `huggingface/smolagents`
- `modelcontextprotocol/servers`

**Spec / changelog feeds:**

- MCP spec changelog (if it has an Atom feed; otherwise skip)

##### `scout/seen.yaml`

- Initialize as `{seen: []}` so the scout has a file to read on first run.

#### Acceptance

- YAML validates: `uv run python -c "import yaml; yaml.safe_load(open('sources.yaml'))"` succeeds.
- Each `url` returns 200 on a HEAD/GET (do a quick local check — don't add a permanent verifier; just confirm at PR time). Document any failures in the PR body and exclude them from the seed.
- PR body includes a **per-source rationale** (one line each) so the human reviewer can decide whether to keep or cut each entry.

#### Out of scope

- Scout script (T6).
- Any LLM logic.
- Source health monitoring (similar to verifier-for-resources, but for sources). Defer.

#### Notes

- **Don't merge speculative sources.** If a feed URL isn't confirmed alive, leave it out of the seed and propose it as a follow-up.
- For each source, `id` should be a kebab-case slug (e.g., `simonwillison-ai-agents`, `langgraph-releases`, `anthropic-engineering`).
- **`type` field values:**
  - `rss` — generic RSS 2.0 feed
  - `atom` — Atom feed
  - `github-releases` — GitHub `/releases.atom` (treated specially by the scout for parsing release notes)
  - `github-tag-feed` — a GitHub user/repo activity Atom feed (rare; only if needed)
  - `html-index` — fallback for sites without feeds (defer in v1; don't add any of these now)
- **Be choosy.** A noisy feed (e.g., a vendor blog that posts daily marketing content) will burn API tokens and waste your review time. Better to start with 12 high-signal sources than 25 mixed-quality ones. The scout's `seen.yaml` and your rejection rate will tell you which feeds to prune in month 2.
- **"Worth following" entries that aren't blogs** (e.g., conference channels, Discord servers) — skip; the scout only reads syndicated feeds.

---

### T6 — Scout (script + workflow + ops)

**Branch:** `t6/scout`
**PR title:** `T6: scout script + weekly workflow`
**Depends on:** T5
**SPEC references:** §5.2, §3.3 (seen.yaml), §6 (identity, kill switch), §7 (trust — no auto-merge), §8 (Sonnet 4.6 model choice with reasoning)

#### Goal

Build the resource-scout agent: weekly fetch from `sources.yaml`, dedup against `seen.yaml`, run each candidate through Claude Sonnet 4.6 for editorial judgment, batch the includes into a PR.

#### Scope

##### `scripts/scout.py`

- **Input:** `sources.yaml`, `scout/seen.yaml`, `resources.yaml` (read-only — the LLM uses section descriptions and existing slugs for context).
- **For each enabled source in `sources.yaml`:**
  - Fetch via `feedparser`.
  - Filter to items newer than the source's `last_checked_at`.
  - Filter out items whose URL is already in `scout/seen.yaml` (any decision) or matches an existing `resources[].url`.
- **For each remaining candidate, call Claude Sonnet 4.6** (`claude-sonnet-4-6`) with **structured output** (use the SDK's tool-based structured output: `tools=[{"name": "judge_candidate", "input_schema": {...}}]` + `tool_choice={"type": "tool", "name": "judge_candidate"}`):
  - **System prompt** — built once per run, with `cache_control: {"type": "ephemeral"}`:
    - The full list of `sections` (id + title + description) from `resources.yaml`.
    - 3-5 hand-picked example resource entries showing house-style blurb voice.
    - Inclusion criteria: hand-written, ~10 bullet points. Examples: "must be substantive technical content, not marketing"; "no listicles or SEO content"; "papers must have practical infra implications"; "tools must be production-ready or notable research".
  - **User prompt:** candidate's URL, title, summary/snippet from the feed, source id.
  - **Required output schema:**
    ```json
    {
      "decision": "include" | "reject",
      "section": "<existing-section-id>",
      "slug": "<proposed-kebab-case-slug>",
      "title": "<title>",
      "author": "<author-or-org>",
      "type": "article|paper|docs|repo|video|spec|course|book",
      "license": "OS|SaaS|OS+SaaS|proprietary|null",
      "blurb": "<one line in house style>",
      "tags": ["..."],
      "rationale": "<one line explaining the decision>"
    }
    ```
- **For each result:**
  - On `decision: include`: add to in-memory `candidates` list; do NOT update `seen.yaml` yet (will be updated post-merge by a follow-up step or manually — defer auto-tombstone-on-merge to v2).
  - On `decision: reject`: append to `scout/seen.yaml` with `decision: rejected`, `reason: <rationale>`, source, dates.
- **After all candidates processed:**
  - Bump `last_checked_at: <today>` on every enabled source.
  - Write `candidates.yaml` with the include list. Empty file if nothing.
- **Flags:**
  - `--dry-run` — make API calls but don't write `seen.yaml` / `sources.yaml` / `candidates.yaml` to disk.
  - `--limit N` — process only first N candidates (for testing).
  - `--source <id>` — restrict to a single source.

##### `.github/workflows/scout-resources.yml`

- **Trigger:** `cron: '0 13 * * 4'` (Thursdays 13:00 UTC) + `workflow_dispatch`.
- **Permissions:** `contents: write`, `pull-requests: write`.
- **Steps:**
  1. Read `vars.MAINTENANCE_PAUSED`. Exit 0 if `true`.
  2. Checkout.
  3. Setup uv + `uv sync --frozen`.
  4. Run `uv run python scripts/scout.py` with `ANTHROPIC_API_KEY` from secrets.
  5. **Skip-empty-runs:** if `candidates.yaml` is empty (or doesn't exist), commit only the `last_checked_at` and `seen.yaml` changes to a side branch (`bot/scout-bookkeeping-${YEAR_WEEK}`) — or skip committing entirely and let the next run pick them up. **Recommend:** always open a tiny PR for `last_checked_at` + `seen.yaml` updates so the bookkeeping is visible; just don't append to `resources.yaml`.
  6. Otherwise: append candidates to `resources.yaml` (in the proposed sections), run `scripts/render.py`, and open a PR via `peter-evans/create-pull-request@v7`:
     - Branch: `bot/scout-${YEAR_WEEK}`.
     - PR title: `Scout — ${N} candidates`.
     - PR body: per-candidate checklist with title, URL, source, proposed section, proposed blurb, rationale.

##### Manual setup checklist (include verbatim in the PR body for T6)

- [ ] Add repo **secret** `ANTHROPIC_API_KEY`. Settings → Secrets and variables → Actions → Secrets → New repository secret.
- [ ] Run the workflow once via `workflow_dispatch` after merging T6 to validate the end-to-end flow.

#### Acceptance

- `uv run python scripts/scout.py --dry-run` succeeds locally with `ANTHROPIC_API_KEY` set in env. Produces a coherent `candidates.yaml` and `seen.yaml` (rejection tombstones).
- Skip-empty-runs verified: run `--dry-run` twice; the second run produces empty candidates (because items already tombstoned).
- House-style blurb output is recognizable as fitting the existing doc — eyeball-check against 5 random includes.
- Workflow file passes `actionlint` (or eyeball).
- Manual setup checklist is in the PR body.

#### Out of scope

- Auto-merge of any kind (deferred to v2).
- Conversational HITL triage (Claude Code routine — v2).
- Semantic dedup against existing `resources[]` beyond URL exact match (v2).
- Auto-tombstoning when a candidate PR is merged (v2 — for now, manual: when you merge a scout PR, append the candidate to `seen.yaml` as `decision: added` either by hand or via a small follow-up workflow on `pull_request: closed`).
- Multi-pass judgment (e.g., Sonnet first-pass + Opus deep-judge on borderlines) — v2.
- Automatic source-health monitoring — defer.

#### Notes

- **Pin the Anthropic SDK** in `pyproject.toml`. Use whatever the latest stable is at T6 implementation time.
- **Prompt caching is required.** The system prompt should be marked with `"cache_control": {"type": "ephemeral"}`. The first candidate in a run pays cache-write; all subsequent reads hit cache. Without this, cost goes up ~5-10x on multi-candidate runs.
- **Be defensive about feed parsing.** Wrap each source in a try/except so one bad feed doesn't kill the run. Log per-source errors as workflow annotations (`::error::`) so they surface in the Actions UI.
- **Voice training.** The 3-5 example resources in the system prompt are the most important calibration knob. Pick examples that cover variety (an Anthropic engineering post, a paper, a tool entry with `OS+SaaS` license, a GitHub repo). Iterate on these in subsequent PRs based on rejection-quality observation.
- **Inclusion criteria** in the system prompt: be honest and concrete. Don't write "high quality" — write "papers must include practical infrastructure implications, not pure ML theory". Lift criteria from the doc's existing caveats section.
- **Slug collision:** if the proposed slug conflicts with an existing `resources[].id`, append `-2` or similar. Renderer will fail loudly on duplicates anyway, so this is a safety net.
- **`type` and `license`:** if Sonnet returns `null` or invalid values, fall back to defaults (`type: article`, `license: null`) and let the human fix in PR review. Don't crash the run.
