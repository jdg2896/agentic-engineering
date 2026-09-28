# CLAUDE.md

Project-level guidance for Claude Code and AI agents working in this repo.

## Before making any changes

Always sync with main and create a branch before starting work:

```
git checkout main && git pull origin main
git checkout -b <type>/<short-description>
```

Direct pushes to `main` are blocked by the branch ruleset — all changes must go through a PR. Working on a branch from the start avoids a forced detour later.

## Commit messages

All commits must follow **Conventional Commits** — see https://gist.github.com/qoomon/5dfcdf8eec66a051ecd85625518cfd13 for the full spec.

Format: `<type>[optional scope]: <description>`

Common types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`.

Example: `feat(renderer): add cluster bullet support`

## Planning work

Do **not** write design specs under `docs/specs/` any more. That directory
was archived to `docs/archive/specs/` on 2026-09-28 and is kept for
historical reference only.

Plan and execute work with the Matt Pocock skills vendored in
`.claude/skills/` (installed from `mattpocock/skills` via `npx skills add`;
`skills-lock.json` pins them, `npx skills update` refreshes them):

1. `/grill-with-docs` — align on the change; updates `CONTEXT.md` / ADRs inline.
2. `/to-spec` — publish the aligned spec as a GitHub issue.
3. `/to-tickets` — break it into tracer-bullet tickets with blocking edges.
4. `/implement` — build from the tickets (TDD + code review built in).

Run `/ask-matt` if unsure which skill fits.

## Agent skills

### Issue tracker

Issues, specs and tickets live in this repo's GitHub Issues via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` at the repo root plus `docs/adr/`, created lazily by `/domain-modeling`. See `docs/agents/domain.md`.
