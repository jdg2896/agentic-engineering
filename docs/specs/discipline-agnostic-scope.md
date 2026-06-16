# Discipline-agnostic scope

## Why

The guide's title says "Agentic Engineering" but the subtitle scopes it
to *"Backend & infrastructure focus"*, and the scout's editorial prompt
hard-codes *"for backend engineers building agentic systems in developer
workflows"*. The content, however, is already discipline-neutral: 13 of
14 sections (foundations, MCP, multi-agent, durable execution, memory,
sandboxing, inference, evals, observability, security, coding-agent
infra) apply equally to a frontend agent, an SRE agent, or a QA agent.
Only Section 14 (`Backend-specific agent patterns (SRE, K8s, IaC)`) is
genuinely discipline-flavored — and even its label is a misnomer: those
are SRE/ops/IaC tools, not "things backend engineers use."

So the narrowness is an artifact of framing, not content. The fix is to
stop the framing from lying: rewrite the subtitle as a quality-bar
statement, retitle, rename the one mislabelled section, strip the
"backend engineers" scoping from the scout so it stops rejecting
otherwise-good cross-discipline content, and add a first set of
authority feeds for the next pillar (FE / computer-use) so the scout
actually has discipline-broad material to surface. This is *light
reframing*, not a wholesale restructure — no new discipline sections are
created in this spec.

## What

After this spec lands:

- The rendered guide's H1 is `Agentic Engineering — A Curated Resource
  Guide` (drops "Workflow").
- The subtitle is the quality-bar line:
  `_Opinionated. No tutorials, no listicles, no marketing. Continuously
  maintained — links verified weekly._`
- Section 14 is titled
  `SRE & operations agents (K8s, observability, IaC)` with a description
  body rewritten to match (no "backend" framing).
- `scripts/scout.py`'s system prompt no longer scopes inclusion to
  "backend engineers"; the rubric is discipline-neutral but keeps every
  existing quality filter (no listicles, no marketing, substantive
  technical content, dedupe, etc.).
- `sources.yaml` gains a maintainer-approved set of 5–10 FE /
  computer-use authority feeds.
- The two docs/specs files that quote the old framing are updated so
  the repo has no stale "backend focus" references.
- `README.md` is re-rendered and the only content deltas are the H1,
  the subtitle, and the Section 14 heading/description.

## Constraints

### Must

- Regenerate `README.md` via `uv run python scripts/render.py`. Never
  hand-edit `README.md` — it is generated output (see
  `readme-as-rendered-output.md`).
- Keep every existing scout quality filter. The only removal is the
  "must be relevant to backend engineers / dev workflows" scoping; the
  no-listicle / no-marketing / dedupe / substance bars stay verbatim.
- New feeds must be **authority outlets** (the outlet itself is the
  quality gate), matching the existing `sources.yaml` pattern. The
  maintainer approves the shortlist before any `sources.yaml` edit.
- Conventional Commits; branch off `main` before work (per CLAUDE.md).
- Section 14's parenthetical hint stays (matches the scan-orient style
  of sibling titles like "Sandboxing & code execution").

### Must Not

- Create any new discipline section (no "Frontend agents", no "QA
  agents") in this spec. Coverage broadens via the existing
  concern-based sections + the renamed Section 14 only.
- Add aggregator / firehose feeds (HN, lobste.rs, subreddits). Feeds
  are authority outlets only — the cost/quality model depends on it.
- Change the `top_7:` list, the verifier, or any render logic beyond
  what re-rendering the new header/section requires.
- Surface-touch many disciplines. FE / computer-use is the *only* new
  pillar seeded here; other disciplines wait for authority outlets.

### Out of Scope

- **Meta-curation workflow (PR2, separate spec).** A monthly GitHub
  Action that opens a single tracking Issue. Decided parameters,
  recorded here so they survive until that spec is written:
  - Output: a single monthly **GitHub Issue** (not a PR, not a doc).
  - Cadence: monthly (first-of-month Action).
  - Scope of proposals: **feed-add** and **feed-drop** proposals (with
    evidence — candidate-rationale co-occurrence for adds, accept/reject
    ratio for drops) **plus section-cluster *flags* as observation
    only** ("N FE links accumulated this month; consider a section").
  - Explicitly NOT proposed by the workflow: section renames, section
    splits, `top_7:` changes.
  - Build sequencing: write the PR2 spec after PR1 merges.
- Migration auto-rewrite, anchor validation, and anything else already
  deferred by `auto-quarantine-dead-links.md`.

## Current State

The "backend" framing lives in exactly these source locations
(`README.md` is regenerated, not a source):

- **`templates/header.md:1`** — H1
  `# Agentic Engineering Workflow — A Curated Resource Guide`.
- **`templates/header.md:3`** — subtitle
  `_Backend & infrastructure focus. Agent/language-agnostic.
  Continuously maintained — links verified weekly._`
- **`resources.yaml:107`** — Section 14 title
  `"Backend-specific agent patterns (SRE, K8s, IaC)"`, with a
  multi-line `description:` body referencing the same framing.
- **`scripts/scout.py:50`** — system-prompt opener: *"You are an
  editorial assistant for a curated resource guide for backend
  engineers building agentic systems in developer workflows."*
- **`scripts/scout.py:87`** — inclusion criterion: *"Content must be
  relevant to backend engineers building agentic systems in dev
  workflows"*.
- **`docs/specs/self-maintaining-guide.md:9`** — *"...for backend
  engineers leveraging agentic engineering in their dev workflows"*.
- **`docs/specs/self-maintaining-guide.md:361`** — *"`header.md` —
  title, 'Backend & infrastructure focus' subtitle, ..."*.
- **`docs/specs/readme-as-rendered-output.md:111`** — quotes the old
  subtitle verbatim inside a code span.

Render pipeline: `scripts/render.py` composes `README.md` from
`templates/header.md` + `resources.yaml`. The scout
(`scripts/scout.py`) builds its system prompt in `build_system_prompt`
(scout.py:48) from section titles + house-style examples + the
inclusion criteria list; feeds come from `sources.yaml` (14 enabled,
all backend/applied-LLM leaning today).

## Tasks

### T1: Retitle, re-subtitle, rename Section 14, re-render

**What:**
- `templates/header.md`: H1 → `# Agentic Engineering — A Curated
  Resource Guide`; subtitle line → `_Opinionated. No tutorials, no
  listicles, no marketing. Continuously maintained — links verified
  weekly._`.
- `resources.yaml`: Section 14 `title:` →
  `"SRE & operations agents (K8s, observability, IaC)"`; rewrite its
  `description:` body so it describes ops/SRE/IaC agents with no
  "backend" framing (keep it the same shape/length as sibling
  descriptions).
- Re-render: `uv run python scripts/render.py`.

**Files:** `templates/header.md`, `resources.yaml`, `README.md`
(regenerated).

**Verify:**
- `uv run pytest` — green (render tests unaffected).
- `git diff README.md` shows changes confined to the H1 line, the
  subtitle line, and the Section 14 heading + its description bullet
  block — no other section reordered or altered.
- `grep -n "Workflow" README.md` returns no title match;
  `grep -n "Backend-specific" README.md` returns nothing.

### T2: Discipline-neutral scout rubric

**What:** In `scripts/scout.py`:
- `build_system_prompt` opener (scout.py:50): drop "for backend
  engineers building agentic systems in developer workflows" → a
  discipline-neutral phrasing aligned with the new subtitle (e.g.
  "...for a curated, opinionated resource guide on agentic
  engineering.").
- Inclusion criteria (scout.py:87): replace "Content must be relevant
  to backend engineers building agentic systems in dev workflows" with
  a discipline-neutral relevance bar (e.g. "Content must be about
  building, evaluating, operating, or securing agentic systems —
  any engineering discipline (FE, BE, infra, QA, data)"). Leave all
  other criteria lines byte-identical.

**Files:** `scripts/scout.py`.

**Verify:**
- `grep -ni "backend" scripts/scout.py` returns nothing.
- `uv run pytest` — green.
- `uv run python scripts/scout.py --dry-run --limit 3` runs without
  error and prints judged candidates (API key permitting; otherwise
  confirm the prompt string via a local `build_system_prompt` import).

### T3: Strip stale "backend" framing from docs/specs

**What:**
- `docs/specs/self-maintaining-guide.md:9` — reword to drop "for
  backend engineers"; describe the audience as engineers building
  agentic systems across disciplines.
- `docs/specs/self-maintaining-guide.md:361` — update the `header.md`
  description to reflect the new subtitle.
- `docs/specs/readme-as-rendered-output.md:111` — update the quoted
  subtitle to the new line.

**Files:** `docs/specs/self-maintaining-guide.md`,
`docs/specs/readme-as-rendered-output.md`.

**Verify:** `grep -rn -i "backend" docs/specs/` returns nothing (or
only this spec's intentional historical references).

### T4: Propose & approve FE / computer-use feed shortlist

**What:** Produce a shortlist of 5–10 authority RSS/Atom feeds for the
FE / computer-use pillar (candidates: Vercel AI blog, browser-use,
Playwright AI/team blog, relevant Anthropic/OpenAI computer-use feeds,
Cursor/Zed engineering blogs, etc.). For each: `id`, `type`, `url`,
one-line rationale for why the outlet is an authority. Post for
maintainer approval; do **not** edit `sources.yaml` yet.

**Files:** none (deliverable is the shortlist in the PR description /
review thread).

**Verify:** Maintainer approves the shortlist. Each proposed `url`
returns a parseable feed: `curl -sSL <url> | head -c 500` shows
RSS/Atom XML.

### T5: Add approved feeds to sources.yaml

**What:** Append the approved feeds to `sources.yaml` following the
existing entry shape (`id`, `type`, `url`, `cadence: weekly`,
`last_checked_at: <today>`, `enabled: true`, `notes: null`). Group them
under a new comment banner (e.g. `# ── FE / computer-use feeds ──`).

**Files:** `sources.yaml`.

**Verify:**
- `uv run python -c "import yaml,pathlib;
  yaml.safe_load(pathlib.Path('sources.yaml').read_text())"` — parses.
- For each new feed:
  `uv run python scripts/scout.py --dry-run --source <new-id> --limit 1`
  reports the feed fetched and N entries found (0 is acceptable; a
  parse error is not).

## Validation

End-to-end acceptance once T1–T5 land:

- `uv run pytest` — all green.
- `uv run python scripts/render.py` is idempotent: running it twice
  produces no diff; the diff vs. `main` is confined to the H1,
  subtitle, and Section 14 heading/description.
- Rendered `README.md` top reads:
  `# Agentic Engineering — A Curated Resource Guide` then
  `_Opinionated. No tutorials, no listicles, no marketing. Continuously
  maintained — links verified weekly._`.
- Section 14 renders as
  `SRE & operations agents (K8s, observability, IaC)` with all six
  existing entries intact.
- `grep -rn -i "backend" templates/ scripts/ resources.yaml docs/specs/`
  returns nothing (excluding this spec).
- `sources.yaml` has the approved FE/computer-use feeds; a scout
  `--dry-run` over them parses without error.
- No new section was added; `top_7:` is unchanged; verifier and render
  logic untouched.
