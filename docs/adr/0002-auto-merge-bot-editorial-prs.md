# Auto-merge bot-authored editorial PRs; PR history is the audit trail

Scout (new Resources) and Source-discovery (new Sources) PRs **auto-merge by default** with
no human review. Editorial quality is delegated to the LLM judge; the squash-merged PR
history is the audit-and-rollback trail. A human is pulled in only when a circuit-breaker
trips, via the existing `auto-merge-skipped` label + review path.

## Why this is safe enough

- Auto-merge still produces a real PR that runs required checks (notably the **render gate**,
  which blocks any out-of-sync `README.md`) before merging — "auto-merge" ≠ "no PR".
- Breakers catch *anomalies*, not steady-state misjudgment, which is accepted as delegated:
  - **Scout:** hold if candidate count > 8 (mass-include), any candidate's `section` is not
    an existing section id (structural / hallucinated output), or judge `confidence` is `low`.
  - **Source-discovery:** hold if the proposed feed fails to parse or has no recent entry,
    fails dedup, or the run exceeds 5 new sources.
- A borderline *Source* is self-correcting: Scout still judges each of its posts against the
  unchanged inclusion bar, so off-topic posts never reach the guide.
- `top_7` ("if you only read 7 things") is never touched by automation — it stays
  hand-curated.

## Considered and rejected

Keeping a human review gate on scout (the status quo) was rejected: the owner wants the loop
fully automated and accepts PR history as the backstop. Recoveries in link-verification
remain human-reviewed (that gate predates this decision and is unchanged).
