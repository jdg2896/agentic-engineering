# CLAUDE.md

Project-level guidance for Claude Code and AI agents working in this repo.

## Before making any changes

Always sync with main before starting work:

```
git checkout main && git pull origin main
```

This avoids working on stale code or creating branches off the wrong base.

## Commit messages

All commits must follow **Conventional Commits** — see https://gist.github.com/qoomon/5dfcdf8eec66a051ecd85625518cfd13 for the full spec.

Format: `<type>[optional scope]: <description>`

Common types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`.

Example: `feat(renderer): add cluster bullet support`
