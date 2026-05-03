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

## Writing specs

Design specs live under `docs/specs/`. Before writing one, read [`docs/specs/TEMPLATE.md`](docs/specs/TEMPLATE.md) and follow its structure (Why / What / Constraints / Current State / Tasks / Validation). Keep tasks small enough to execute in a fresh session — each one should touch ≤3 files and have a concrete `Verify` step.
