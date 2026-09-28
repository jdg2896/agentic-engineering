# Authenticate LLM-using CI jobs with a Claude subscription OAuth token, not an API key

The scout and source-discovery jobs authenticate to Claude with a `claude setup-token`
OAuth token (`CLAUDE_CODE_OAUTH_TOKEN`) drawn from the owner's Pro/Max subscription,
**not** an `ANTHROPIC_API_KEY`. Chosen to avoid incurring per-call API costs.

## Consequences

- The raw `anthropic` Python SDK cannot use an OAuth token, so the judge call runs through
  the Claude Code CLI headless (`claude -p --model … --json-schema …`) instead of
  `messages.create(..., tool_choice=...)`. We trade guaranteed-valid forced-tool output for
  `--json-schema` validation — which is why the scout auto-merge has a structural breaker
  that rejects an unknown `section` id (see [0002](0002-auto-merge-bot-editorial-prs.md)).
- The token draws from the owner's **personal weekly quota**, shared with interactive
  Claude Code usage. A heavy scout/research week competes with real work.
- Subscription tokens are intended for interactive use; **unattended CI automation is a
  ToS gray area** ("not for automation" / "one human, one beneficiary"). Enforcement
  posture is unverifiable from docs, but the restriction is real.
- The token expires after **1 year** and must be rotated.
- `verify_links.py` makes no LLM calls, so the link-verification job is unaffected.

If API spend later becomes acceptable, reverting to `ANTHROPIC_API_KEY` + the `anthropic`
SDK forced tool call restores stronger structured-output guarantees and removes the ToS and
quota-contention concerns. Don't "fix" this back without weighing that cost.
