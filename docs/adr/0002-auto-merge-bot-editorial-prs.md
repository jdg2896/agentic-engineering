# Auto-merge bot-authored editorial PRs; PR history is the audit trail

Scout (new Resources) PRs **auto-merge by default** with no human review. Editorial quality is delegated to the LLM judge; the squash-merged PR
history is the audit-and-rollback trail. A human is pulled in only when a circuit-breaker
trips, via the existing `auto-merge-skipped` label + review path.

## Why this is safe enough

- The render runs inside each bot job before the PR opens, and the job fails if `README.md`
  is dirty afterwards, so an out-of-sync README is never committed. The pull-request **render
  gate** is not a required check: PRs opened with the Actions token never trigger it, and a
  required check that never runs would block auto-merge forever.
- Breakers catch *anomalies*, not steady-state misjudgment, which is accepted as delegated:
  - **Scout:** hold if candidate count > cap (default 8; mass-include), or on structural
    errors: any candidate's `section` is not an existing section id, or its `type` is outside
    the schema enum (hallucinated / malformed output).
  - **Verify:** hold on mass quarantine (more than 5 newly quarantined in one run), or on
    any cross-host migration (a redirect to a different host, ignoring `www.`, which could
    be an expired domain now pointing elsewhere).
    Recoveries and top-7 quarantines auto-merge and are called out in the PR body for
    post-hoc review.
- Source-discovery is deferred to a separate spec; its breakers will be decided there.
- `top_7` ("if you only read 7 things") is never touched by automation — it stays
  hand-curated.

## Considered and rejected

Keeping a human review gate on scout (the status quo) was rejected: the owner wants the loop
fully automated and accepts PR history as the backstop. Recoveries in link-verification
are no longer human-reviewed either: they auto-merge and are listed in the PR body.
