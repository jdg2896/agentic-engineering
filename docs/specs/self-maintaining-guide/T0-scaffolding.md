# T0 — Repo scaffolding

**Branch:** `t0/scaffolding`
**PR title:** `T0: repo scaffolding`
**Depends on:** —
**SPEC references:** §2 (architecture), §8 (implementation choices)

## Goal

Set up the Python project structure so subsequent tasks have a working build environment. No application logic in this task.

## Scope

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

## Acceptance

- `uv sync` succeeds from a clean clone.
- `uv run python -c "import yaml, requests, feedparser, anthropic, jinja2"` succeeds.
- All listed directories exist in the repo (committed via `.gitkeep` if otherwise empty).
- `git status` is clean after `uv sync` (i.e., `uv.lock` is committed).

## Out of scope

- Any script in `scripts/` (T1+).
- Any template in `templates/` (T1).
- Any workflow in `.github/workflows/` (T3+).
- Pre-commit hooks.
- CONTRIBUTING.md or other docs.

## Notes

- If `uv` isn't installed in the dev environment, install instructions go in the PR body, not in `README.md` (the README is reader-facing, not contributor-facing in v1).
- Pin Python via `requires-python = ">=3.11"` in `pyproject.toml`. Don't add a `.python-version` file unless `uv` requires it.
