# T2 — Bulk migration (sections 3-14)

**Branch:** `t2/bulk-migration`
**PR title:** `T2: bulk-migrate sections 3-14`
**Depends on:** T1
**SPEC references:** §3 (data model)

## Goal

Mechanically migrate the remaining sections of `agentic-engineering.md` (sections 3 through 14) into `resources.yaml`, following the schema and conventions established by T1.

## Scope

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

## Acceptance

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

## Out of scope

- Schema changes — if the renderer needs minor extensions for an edge case (e.g., a third type of cluster grouping), make the extension and call it out clearly in the PR body. Don't reshape the schema.
- New resources beyond what's in the current `agentic-engineering.md`.
- Any workflow or CI changes (T3).
- Updating verification dates beyond the migration default — that's the verifier's job.

## Notes

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
