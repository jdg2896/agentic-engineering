# T1 — Schema + renderer + 3 sections migrated

**Branch:** `t1/schema-renderer`
**PR title:** `T1: schema, renderer, 3 sections migrated`
**Depends on:** T0
**SPEC references:** §3 (data model), §4 (render pipeline)

## Goal

Establish the structured data schema, build the renderer, and prove correctness by round-tripping the first three sections of the existing `agentic-engineering.md` (the "If you only read 7" list, section 1 "Foundational design", section 2 "Tool integration & MCP") through the renderer to a byte-identical (whitespace-tolerant) match against the original.

## Scope

### `resources.yaml`

- All 14 sections in the `sections:` list, each with `id`, `order`, `title`, and a hand-written 2-3 sentence `description` that captures what the section is about. The `description` is what the scout's LLM uses for categorization in T6, so make it concrete.
- `top_7:` — the ordered list of 7 resource slugs from the existing "If you only read 7 things" section.
- `resources:` — populated **only** for sections `foundations` and `tools-mcp`. Every entry hand-migrated with all required fields per SPEC §3.1.

### `templates/`

Hand-written prose templates that wrap the generated lists. Use Jinja2.

- **`header.md`** — title, "Backend & infrastructure focus" subtitle, compilation-date placeholder (renderer fills with the most recent `verified_at`), the verification-mark caveat blurb at top.
- **`top_7.md`** — the "If you only read 7 things" intro line + numbered list rendered from the `top_7` slugs (resolved against `resources[]`).
- **`sections.md.j2`** — body template, iterates `sections` in order, rendering each section's resources as a Markdown bullet list.
- **`opinionated_stack.md`** — the table at the bottom of the current doc, copy-pasted verbatim. Hand-written, not generated.
- **`caveats.md`** — caveats + "Worth following for ongoing signal" footer, copy-pasted verbatim.

### `scripts/render.py`

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

## Acceptance

- `uv run python scripts/render.py` succeeds with no errors.
- For the populated portion (top_7 + foundations + tools-mcp), the generated `agentic-engineering.md` matches the corresponding parts of the original `agentic-engineering.md` after normalizing trailing whitespace and trailing newlines. Verify with:
  ```bash
  diff <(grep -v '^$' agentic-engineering.md) <(grep -v '^$' /tmp/original.md)
  ```
  (Save the original somewhere before regenerating; the populated portion should match.)
- Sections 3-14 render as just the section header (or are skipped — your choice, document it).
- `uv run pytest tests/` passes (add a basic `tests/test_render.py` if convenient, but not required for T1).

## Out of scope

- Migration of sections 3-14 (T2).
- CI gate (T3).
- Any verifier or scout logic.
- Any workflow files.

## Notes

- **Slug convention:** kebab-case, derived from title or canonical short-name. Examples: `building-effective-agents`, `effective-context-engineering`, `mcp-spec`, `mcp-servers-repo` (for `modelcontextprotocol/servers`). Keep them human-readable.
- **`verified_at` for migrated entries:**
  - For ✓-marked entries in the original: set `verified_at: 2026-05-03` (the doc's compile date).
  - For non-✓ entries: set `verified_at: null`. This makes them not render with ✓ and makes T4's verifier the thing that fills them in over time.
- **Multi-link bullets in the original** (e.g., the `wong2/awesome-mcp-servers` and `appcypher/awesome-mcp-servers` line) → split into two resource entries sharing a `cluster: awesome-mcp-servers` id.
- **Preserve voice exactly.** Don't paraphrase blurbs — copy them verbatim from the original. Voice consistency is the whole point of structured data.
- **Section descriptions are the one place you write new prose.** Look at the resources in each section to inform the description. These get used by the scout in T6 for categorization.
