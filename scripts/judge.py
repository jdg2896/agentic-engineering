"""Scout's editorial judge, run through the Claude Code CLI in headless mode.

Authenticates with `CLAUDE_CODE_OAUTH_TOKEN` per docs/adr/0001-oauth-token-for-ci.md.
`judge_entry` is the whole interface: it hides the CLI invocation, retries, JSON
parsing and schema validation, and raises `JudgeError` instead of returning a
partial judgment.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from collections.abc import Callable

MODEL = "claude-sonnet-4-6"
CLI_TIMEOUT_SECONDS = 300
# Attempts per entry, including the first; the backoff before attempt n+1 is n * this.
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 10

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


class JudgeQuotaError(JudgeError):
    """The Claude subscription usage limit is exhausted."""


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
        unknown = [n for n in names if n not in _JSON_TYPES]
        if unknown:
            raise JudgeError(f"judgment schema for '{path}' uses unsupported JSON type(s): {unknown}")
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


def _mentions_auth_failure(text: str) -> bool:
    text = text.lower()
    return any(s in text for s in ("failed to authenticate", "invalid api key", "oauth token", "/login"))


# The CLI's subscription limit messages ("You've hit your session limit · resets 3pm
# (UTC)", "... usage limit", "... weekly limit"). These reset in hours, not seconds.
# A bare HTTP 429 / "rate limit" is deliberately not matched: short rate limits clear
# within the retry backoff.
_QUOTA_RE = re.compile(r"\bhit your\b[^.\n]{0,40}?\blimit\b|\busage limit\b", re.IGNORECASE)


def _mentions_quota_exhausted(text: str) -> bool:
    return _QUOTA_RE.search(text) is not None


def _is_auth_failure(envelope: dict) -> bool:
    if envelope.get("api_error_status") in (401, 403):
        return True
    return _mentions_auth_failure(str(envelope.get("result", "")))


def parse_judgment(stdout: str) -> dict:
    """Parse `claude -p --output-format json` stdout into a validated judgment.

    Raises JudgeAuthError on an authentication failure, JudgeQuotaError when the
    subscription usage limit is exhausted, and JudgeError on any other CLI error, malformed JSON, missing structured output, or schema violation.
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
        if _mentions_quota_exhausted(str(envelope.get("result", ""))):
            raise JudgeQuotaError(f"Claude Code CLI error: {envelope.get('result')}")
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
        # Load no user/project/local settings (so no hooks) and no MCP servers.
        "--setting-sources", "",
        "--strict-mcp-config",
    ]
    try:
        # Run from an empty directory so the repo's CLAUDE.md and skills are not loaded.
        with tempfile.TemporaryDirectory(prefix="scout-judge-") as cwd:
            proc = subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True, timeout=CLI_TIMEOUT_SECONDS, check=False
            )
    except FileNotFoundError as exc:
        raise JudgeError("Claude Code CLI (`claude`) is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise JudgeError(f"Claude Code CLI timed out after {CLI_TIMEOUT_SECONDS}s") from exc
    # On API errors the CLI exits non-zero but still prints the JSON envelope; parse that.
    if proc.returncode != 0 and not proc.stdout.strip():
        message = f"Claude Code CLI exited {proc.returncode}: {proc.stderr.strip()[:500]}"
        if _mentions_auth_failure(proc.stderr):
            raise JudgeAuthError(f"{message} {AUTH_HINT}")
        if _mentions_quota_exhausted(proc.stderr):
            raise JudgeQuotaError(message)
        raise JudgeError(message)
    return proc.stdout


def judge_entry(
    system: str,
    title: str,
    url: str,
    summary: str,
    source_id: str,
    run: Callable[[str, str], str] = run_claude,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Judge one candidate entry; return a validated judgment dict or raise JudgeError.

    Any `JudgeError` is retried, up to `MAX_ATTEMPTS` attempts in all: CLI timeouts
    and errors are usually transient, and malformed or off-schema output is a
    nondeterministic model slip worth another draw. `JudgeAuthError` is raised at
    once, since a bad token will not fix itself, and so is `JudgeQuotaError`, since
    a usage limit resets in hours, not within the backoff. Once attempts run out,
    the last error is raised.
    """
    prompt = (
        f"Evaluate this candidate resource for inclusion.\n\n"
        f"Title: {title}\n"
        f"URL: {url}\n"
        f"Source feed: {source_id}\n"
        f"Summary/description:\n{summary or '(no summary available)'}"
    )
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return parse_judgment(run(prompt, system))
        except (JudgeAuthError, JudgeQuotaError):
            raise
        except JudgeError as exc:
            if attempt == MAX_ATTEMPTS:
                raise
            delay = attempt * RETRY_BACKOFF_SECONDS
            print(
                f"::warning::judge attempt {attempt}/{MAX_ATTEMPTS} failed for {url}: {exc}; "
                f"retrying in {delay}s",
                flush=True,
            )
            sleep(delay)
    raise AssertionError("unreachable")
