# SPEC — self-maintaining agentic-engineering resource guide

_Status: v1 design, signed off via /grill-me on 2026-05-03._

> **Note:** For decisions about the renderer's output target and surfaced artifacts (`✓` marks, header date, opinionated stack), see [`readme-as-rendered-output.md`](../readme-as-rendered-output.md) (2026-05-03).

## 1. Goal

Turn `agentic-engineering.md` from a hand-edited Markdown file into a **self-maintaining curated resource guide** for backend engineers leveraging agentic engineering in their dev workflows. Two automated agents keep it fresh:

- **Verifier** — checks every URL is still live and canonical; opens a PR when something breaks or migrates.
- **Scout** — periodically scans a curated list of reputable feeds and proposes new resources via PR.

The Markdown doc becomes a *rendered artifact* of structured data + hand-written prose templates. Editorial judgment stays human; mechanical maintenance does not.

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

top_7:                                  # explicit ordered list of resource ids
  - building-effective-agents
  - effective-context-engineering
  - twelve-factor-agents
  - multi-agent-research-system
  - dont-build-multi-agents
  - openai-practical-guide
  - applied-llms-year

resources:
  - id: building-effective-agents       # slug, stable, never changes
    section: foundations                # FK → sections[].id
    url: https://www.anthropic.com/research/building-effective-agents
    title: Building Effective Agents
    author: "Anthropic (Schluntz/Zhang)"
    type: article                       # article|paper|docs|repo|video|spec|course|book
    license: null                       # OS|SaaS|OS+SaaS|proprietary|null — only set for tools/products
    blurb: |
      The "workflows vs agents" mental model that everything else builds on.
    cluster: null                       # optional cluster-id; multiple resources sharing one render the same bullet
    tags: [canonical, mental-model]     # free-form
    added_at: 2026-05-03
    verified_at: 2026-05-03             # last time URL was confirmed live + canonical
    archived: false                     # upstream is dead but historically important
    paywall: false                      # verifier won't false-positive on auth gates
    superseded_by: null                 # optional id of replacement
    first_dead_at: null                 # date of first consecutive dead check; cleared on any non-dead outcome
    quarantined_at: null                # set by verifier when (today - first_dead_at) >= 21 days; renderer skips entry
    quarantine_reason: null             # status_code or error string captured at quarantine time
    notes: null                         # private, never rendered
```

The three fields `first_dead_at`, `quarantined_at`, and `quarantine_reason` are written by the verifier (see `docs/specs/auto-quarantine-dead-links.md`). They also apply to `worth_following:` entries, which the verifier matches by `url`.

**Multi-link bullets** (e.g., the two awesome-mcp-servers registries, Promptfoo OS+SaaS): one resource per URL, share a `cluster` id. Renderer combines clustered resources into a single bullet.

**Top-7 ordering** lives in the file-level `top_7` list, not as a per-resource field — single source of truth, easy to reorder.

### 3.2 `sources.yaml` — what the scout monitors

```yaml
sources:
  - id: anthropic-engineering
    type: rss                           # rss|atom|github-releases|github-tag-feed|html-index
    url: https://www.anthropic.com/engineering/rss.xml
    cadence: weekly                     # advisory; the workflow runs weekly anyway
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
    decision: rejected                  # added|rejected|deferred
    reason: "Generic listicle; doesn't meet curation bar"
    proposed_for_section: foundations
    source: simonwillison-ai-agents
  - url: https://www.anthropic.com/engineering/some-new-piece
    first_seen_at: 2026-05-03
    decided_at: 2026-05-05
    decision: added
    resource_id: some-new-piece         # slug it was added under
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
- **Kill switch:** repo *variable* `MAINTENANCE_PAUSED` (default `false`). Both maintenance workflows check first, exit cleanly if `true`. Toggle via Settings → Variables or `gh variable set MAINTENANCE_PAUSED -b true`.
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
- Domain-migration *target-finding* (when a 404 fires, ask Claude to find the new canonical URL).
- Supersession detection (newer post by same author replacing older).
- Semantic dedup on candidates (embedding similarity vs. URL-only).
- Human-in-the-loop conversational triage agent — natural fit for a Claude Code routine, not GH Actions.
- Dedicated GitHub App identity (only if pattern is replicated to other repos or PR branding starts to matter).
- Statistics dashboard (avg age per section, % verified in last 90 days, decay heatmap).

## 11. Risk register

| Risk | Mitigation |
|---|---|
| Verifier false-positive on parked-domain 200 OK | No auto-merge in v1; v2 content sanity check |
| Scout re-proposes already-rejected items | `seen.yaml` tombstones keyed on URL |
| Anthropic API outage during scout run | Job fails; retries next week; no harm |
| Source feed disappears | Per-source try/except in scout; error logged, others continue |
| Duplicate bot PRs | Stable branch name + peter-evans dedupe |
| Bot opens PR with bad data while you're on holiday | `MAINTENANCE_PAUSED=true` kill switch, single click |
| Render-on-edit blocks all PRs if renderer is broken | Treat renderer breakage as a P0; fix-forward via direct push (allowed under "single human" branch protection) |
