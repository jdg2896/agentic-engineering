# Tasks

Self-contained implementation tasks for the v1 self-maintaining resource guide.

## How to use

If you're picking up a task in a fresh session:

1. **Read `SPEC.md` at repo root** — full design context.
2. **Read the task file** for the task you're working on (e.g., `T1-schema-renderer.md`).
3. **Work on a branch matching the task file's branch convention** (e.g., `t1/schema-renderer`).
4. **Open a PR** with the conventional title (`T<n>: <short name>`). Reference `SPEC.md` sections you relied on.
5. **Acceptance criteria in the task file are the contract** — the PR is done when those pass, no more, no less.

## Task order

Linear dependencies. Each task assumes prior tasks have been merged to `main`.

| ID | Name | Depends on |
|---|---|---|
| T0 | Repo scaffolding | — |
| T1 | Schema + renderer + 3 sections migrated | T0 |
| T2 | Bulk migration (sections 3-14) | T1 |
| T3 | Render-on-edit CI gate | T2 |
| T4 | Verifier (script + workflow) | T3 |
| T5 | Sources seed | T0 (independent of T4 in principle, but conventionally after) |
| T6 | Scout (script + workflow) | T5 |

## Conventions

- One PR per task. Squash-merge.
- Branch name: `t<n>/<slug>`.
- PR title: `T<n>: <short name>`.
- PR body: bullet list of what was built, link to relevant `SPEC.md` sections, and any deviations from the task file (with reasoning).
- **Out-of-scope sections in task files are binding** — don't pull future-task work forward, even if it seems convenient.

## What NOT to do in a task session

- Don't redesign — the design is in `SPEC.md`. If a real design issue surfaces, surface it in the PR description and stop, don't unilaterally re-decide.
- Don't add features beyond the task scope.
- Don't generate documentation, READMEs, or summary files unless the task requires it.
