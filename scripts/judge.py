"""Scout's editorial judge, run through the Claude Code CLI in headless mode.

Authenticates with `CLAUDE_CODE_OAUTH_TOKEN` per docs/adr/0001-oauth-token-for-ci.md.
`judge_entry` is the whole interface: it hides the CLI invocation, JSON parsing and
schema validation, and raises `JudgeError` instead of returning a partial judgment.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable

MODEL = "claude-sonnet-4-6"
CLI_TIMEOUT_SECONDS = 300

# The judge_candidate schema, passed to the CLI as --json-schema and re-checked here.
JUDGMENT_SCHEMA = {
    "type": "object",
    "required": ["decision", "section", "slug", "title", "author", "type", "blurb", "tags", "rationale"],
    "properties": {
        "decision": {"type": "string", "enum": ["include", "reject"]},
        "section": {"type": "string"},
        "slug": {"type": "string"},
        "title": {"type": "string"},
        "author": {"type": "string"},
        "type": {"type": "string", "enum": ["article", "paper", "docs", "repo", "video", "spec", "course", "book"]},
        "license": {"type": ["string", "null"]},
        "blurb": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
}

AUTH_HINT = (
    "The Claude Code OAuth token is missing, invalid or expired. Tokens from "
    "`claude setup-token` expire after one year: run `claude setup-token` and update "
    "the CLAUDE_CODE_OAUTH_TOKEN repo secret (see docs/adr/0001-oauth-token-for-ci.md)."
)


class JudgeError(Exception):
    """The judge produced no usable judgment."""


class JudgeAuthError(JudgeError):
    """The Claude Code CLI could not authenticate."""


_JSON_TYPES = {
    "string": str,
    "array": list,
    "object": dict,
    "null": type(None),
}


def _check(value: object, schema: dict, path: str) -> None:
    allowed = schema.get("type")
    if allowed is not None:
        names = allowed if isinstance(allowed, list) else [allowed]
        if not any(isinstance(value, _JSON_TYPES[n]) for n in names):
            raise JudgeError(f"judgment field '{path}' must be {' or '.join(names)}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise JudgeError(f"judgment field '{path}' has unknown value {value!r}; expected one of {schema['enum']}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            _check(item, schema["items"], f"{path}[{i}]")


def _validate(judgment: object) -> dict:
    if not isinstance(judgment, dict):
        raise JudgeError(f"judgment must be an object, got {judgment!r}")
    missing = [k for k in JUDGMENT_SCHEMA["required"] if k not in judgment]
    if missing:
        raise JudgeError(f"judgment is missing required field(s): {', '.join(missing)}")
    for key, prop in JUDGMENT_SCHEMA["properties"].items():
        if key in judgment:
            _check(judgment[key], prop, key)
    return judgment


def _is_auth_failure(envelope: dict) -> bool:
    if envelope.get("api_error_status") in (401, 403):
        return True
    text = str(envelope.get("result", "")).lower()
    return any(s in text for s in ("failed to authenticate", "invalid api key", "oauth token", "/login"))


def parse_judgment(stdout: str) -> dict:
    """Parse `claude -p --output-format json` stdout into a validated judgment.

    Raises JudgeAuthError on an authentication failure and JudgeError on any other
    CLI error, malformed JSON, missing structured output, or schema violation.
    """
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise JudgeError(f"Claude Code CLI output is not valid JSON ({exc}): {stdout[:200]!r}") from exc
    if not isinstance(envelope, dict):
        raise JudgeError(f"Claude Code CLI output is not a JSON object: {stdout[:200]!r}")
    if envelope.get("is_error"):
        if _is_auth_failure(envelope):
            raise JudgeAuthError(f"{envelope.get('result')} {AUTH_HINT}")
        raise JudgeError(f"Claude Code CLI error: {envelope.get('result') or envelope.get('subtype')}")
    judgment = envelope.get("structured_output")
    if judgment is None:
        raise JudgeError(f"Claude Code CLI returned no structured_output (result: {envelope.get('result')!r})")
    return _validate(judgment)


def run_claude(prompt: str, system: str) -> str:
    """Invoke the Claude Code CLI headless and return its stdout (the JSON envelope)."""
    cmd = [
        "claude", "-p", prompt,
        "--system-prompt", system,
        "--model", MODEL,
        "--output-format", "json",
        "--json-schema", json.dumps(JUDGMENT_SCHEMA),
        # Feed content is untrusted: give the judge no tools to act with.
        "--tools", "",
        "--no-session-persistence",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=CLI_TIMEOUT_SECONDS, check=False)
    except FileNotFoundError as exc:
        raise JudgeError("Claude Code CLI (`claude`) is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise JudgeError(f"Claude Code CLI timed out after {CLI_TIMEOUT_SECONDS}s") from exc
    # On API errors the CLI exits non-zero but still prints the JSON envelope; parse that.
    if proc.returncode != 0 and not proc.stdout.strip():
        raise JudgeError(f"Claude Code CLI exited {proc.returncode}: {proc.stderr.strip()[:500]}")
    return proc.stdout


def judge_entry(
    system: str,
    title: str,
    url: str,
    summary: str,
    source_id: str,
    run: Callable[[str, str], str] = run_claude,
) -> dict:
    """Judge one candidate entry; return a validated judgment dict or raise JudgeError."""
    prompt = (
        f"Evaluate this candidate resource for inclusion.\n\n"
        f"Title: {title}\n"
        f"URL: {url}\n"
        f"Source feed: {source_id}\n"
        f"Summary/description:\n{summary or '(no summary available)'}"
    )
    return parse_judgment(run(prompt, system))
