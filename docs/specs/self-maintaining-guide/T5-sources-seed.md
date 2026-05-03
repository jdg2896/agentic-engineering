# T5 — Scout sources seed

**Branch:** `t5/sources-seed`
**PR title:** `T5: scout sources seed`
**Depends on:** T0 (independent of T1-T4 in principle; conventionally done after T4)
**SPEC references:** §3.2 (sources.yaml schema)

## Goal

Curated initial set of feeds the scout will monitor. **This is an editorial decision, not a mechanical one** — the AI proposes, the human approves in PR review. Don't blindly merge a long list.

## Scope

### `sources.yaml`

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

### `scout/seen.yaml`

- Initialize as `{seen: []}` so the scout has a file to read on first run.

## Acceptance

- YAML validates: `uv run python -c "import yaml; yaml.safe_load(open('sources.yaml'))"` succeeds.
- Each `url` returns 200 on a HEAD/GET (do a quick local check — don't add a permanent verifier; just confirm at PR time). Document any failures in the PR body and exclude them from the seed.
- PR body includes a **per-source rationale** (one line each) so the human reviewer can decide whether to keep or cut each entry.

## Out of scope

- Scout script (T6).
- Any LLM logic.
- Source health monitoring (similar to verifier-for-resources, but for sources). Defer.

## Notes

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
