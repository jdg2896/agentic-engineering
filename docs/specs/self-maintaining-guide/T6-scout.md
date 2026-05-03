# T6 — Scout (script + workflow + ops)

**Branch:** `t6/scout`
**PR title:** `T6: scout script + weekly workflow`
**Depends on:** T5
**SPEC references:** §5.2, §3.3 (seen.yaml), §6 (identity, kill switch), §7 (trust — no auto-merge), §8 (Sonnet 4.6 model choice with reasoning)

## Goal

Build the resource-scout agent: weekly fetch from `sources.yaml`, dedup against `seen.yaml`, run each candidate through Claude Sonnet 4.6 for editorial judgment, batch the includes into a PR.

## Scope

### `scripts/scout.py`

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

### `.github/workflows/scout-resources.yml`

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

### Manual setup checklist (include verbatim in the PR body for T6)

- [ ] Add repo **secret** `ANTHROPIC_API_KEY`. Settings → Secrets and variables → Actions → Secrets → New repository secret.
- [ ] Run the workflow once via `workflow_dispatch` after merging T6 to validate the end-to-end flow.

## Acceptance

- `uv run python scripts/scout.py --dry-run` succeeds locally with `ANTHROPIC_API_KEY` set in env. Produces a coherent `candidates.yaml` and `seen.yaml` (rejection tombstones).
- Skip-empty-runs verified: run `--dry-run` twice; the second run produces empty candidates (because items already tombstoned).
- House-style blurb output is recognizable as fitting the existing doc — eyeball-check against 5 random includes.
- Workflow file passes `actionlint` (or eyeball).
- Manual setup checklist is in the PR body.

## Out of scope

- Auto-merge of any kind (deferred to v2).
- Conversational HITL triage (Claude Code routine — v2).
- Semantic dedup against existing `resources[]` beyond URL exact match (v2).
- Auto-tombstoning when a candidate PR is merged (v2 — for now, manual: when you merge a scout PR, append the candidate to `seen.yaml` as `decision: added` either by hand or via a small follow-up workflow on `pull_request: closed`).
- Multi-pass judgment (e.g., Sonnet first-pass + Opus deep-judge on borderlines) — v2.
- Automatic source-health monitoring — defer.

## Notes

- **Pin the Anthropic SDK** in `pyproject.toml`. Use whatever the latest stable is at T6 implementation time.
- **Prompt caching is required.** The system prompt should be marked with `"cache_control": {"type": "ephemeral"}`. The first candidate in a run pays cache-write; all subsequent reads hit cache. Without this, cost goes up ~5-10x on multi-candidate runs.
- **Be defensive about feed parsing.** Wrap each source in a try/except so one bad feed doesn't kill the run. Log per-source errors as workflow annotations (`::error::`) so they surface in the Actions UI.
- **Voice training.** The 3-5 example resources in the system prompt are the most important calibration knob. Pick examples that cover variety (an Anthropic engineering post, a paper, a tool entry with `OS+SaaS` license, a GitHub repo). Iterate on these in subsequent PRs based on rejection-quality observation.
- **Inclusion criteria** in the system prompt: be honest and concrete. Don't write "high quality" — write "papers must include practical infrastructure implications, not pure ML theory". Lift criteria from the doc's existing caveats section.
- **Slug collision:** if the proposed slug conflicts with an existing `resources[].id`, append `-2` or similar. Renderer will fail loudly on duplicates anyway, so this is a safety net.
- **`type` and `license`:** if Sonnet returns `null` or invalid values, fall back to defaults (`type: article`, `license: null`) and let the human fix in PR review. Don't crash the run.
