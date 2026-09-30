"""Scout's editorial judge, run through the Claude Code CLI in headless mode.

Authenticates with `CLAUDE_CODE_OAUTH_TOKEN` per docs/adr/0001-oauth-token-for-ci.md.
Two judgment kinds share one CLI invocation, model setting, hardening, retry policy
and error types: `judge_entry` (Scout's per-entry judgment) and `judge_topic_fit`
(a Trial's Topic fit verdict on a Prospective Source's feed as a whole). Each hides
the CLI invocation, retries, JSON parsing and schema validation, and raises
`JudgeError` instead of returning a partial judgment.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable

# Opus for editorial quality; Scout's per-run judge budget protects the shared quota (ADR 0001).
MODEL = "claude-opus-5-5"
CLI_TIMEOUT_SECONDS = 300
# Attempts per entry, including the first; the backoff before attempt n+1 is n * this.
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 10
# How much of an entry's summary the judge sees. A GitHub release can carry 100 KB+
# of release notes; the opening is enough to judge it, and the rest only costs tokens.
MAX_SUMMARY_CHARS = 4_000
MAX_TITLE_CHARS = 500
# A Topic fit call sees a whole Trial sample (up to 10 entries) at once, so each
# summary is shortened further: its opening says what the entry is about.
TOPIC_FIT_SUMMARY_CHARS = 600

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

# The Topic fit schema: whether a feed, as a whole, is about agentic systems, and why.
# Passed to the CLI as --json-schema and re-checked here, like JUDGMENT_SCHEMA.
TOPIC_FIT_SCHEMA = {
    "type": "object",
    "required": ["topic_fit", "rationale"],
    "properties": {
        "topic_fit": {"type": "boolean"},
        "rationale": {"type": "string"},
    },
}

AUTH_HINT = (
    "The Claude Code OAuth token is missing, invalid or expired. Tokens from "
    "`claude setup-token` expire after one year: run `claude setup-token` and update "
    "the CLAUDE_CODE_OAUTH_TOKEN repo secret (see docs/adr/0001-oauth-token-for-ci.md)."
)


_WHITESPACE_RE = re.compile(r"\s+")


def _one_line(value: object, max_len: int = 500) -> str:
    """Collapse whitespace and drop control characters so CLI/model text stays on one log line.

    Judge errors are printed as `::error::` / `::warning::` lines, where a newline
    would let the rest of the text start its own workflow command. A local helper,
    since scout.py imports this module and not the other way round.
    """
    text = _WHITESPACE_RE.sub(" ", str(value))
    return "".join(ch for ch in text if ch.isprintable()).strip()[:max_len]


class JudgeError(Exception):
    """The judge produced no usable judgment."""


class JudgeAuthError(JudgeError):
    """The Claude Code CLI could not authenticate."""


class JudgeQuotaError(JudgeError):
    """The Claude subscription usage limit is exhausted."""


class JudgeLaunchError(JudgeError):
    """The Claude Code CLI could not be started (not installed, E2BIG, ...); a retry fails the same way."""


_JSON_TYPES = {
    "string": str,
    "boolean": bool,
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


def _validate(judgment: object, schema: dict) -> dict:
    if not isinstance(judgment, dict):
        raise JudgeError(f"judgment must be an object, got {judgment!r}")
    missing = [k for k in schema["required"] if k not in judgment]
    if missing:
        raise JudgeError(f"judgment is missing required field(s): {', '.join(missing)}")
    for key, prop in schema["properties"].items():
        if key in judgment:
            _check(judgment[key], prop, key)
    return judgment


def _mentions_auth_failure(text: str) -> bool:
    text = text.lower()
    return any(s in text for s in ("failed to authenticate", "invalid api key", "oauth token", "/login"))


# The CLI's subscription limit messages ("You've hit your session limit · resets 3pm
# (UTC)", "5-hour limit reached ∙ resets 3pm", "Weekly limit reached ∙ resets Mon",
# "... usage limit"). These reset in hours, not seconds. A bare HTTP 429 / "rate
# limit" or "limit reached" is deliberately not matched: short rate limits clear
# within the retry backoff.
_QUOTA_RE = re.compile(
    r"\bhit your\b[^.\n]{0,40}?\blimit\b"
    r"|\busage limit\b"
    r"|\b(?:session|weekly|5-hour|opus|sonnet)\s+limit\b",
    re.IGNORECASE,
)


def _mentions_quota_exhausted(text: str) -> bool:
    return _QUOTA_RE.search(text) is not None


def _is_auth_failure(envelope: dict) -> bool:
    if envelope.get("api_error_status") in (401, 403):
        return True
    return _mentions_auth_failure(str(envelope.get("result", "")))


def parse_judgment(stdout: str) -> dict:
    """Parse `claude -p --output-format json` stdout into a validated per-entry judgment.

    Raises JudgeAuthError on an authentication failure, JudgeQuotaError when the
    subscription usage limit is exhausted, and JudgeError on any other CLI error, malformed JSON, missing structured output, or schema violation.
    """
    return _parse(stdout, JUDGMENT_SCHEMA)


def parse_topic_fit(stdout: str) -> dict:
    """Parse CLI stdout into a validated Topic fit verdict (`TOPIC_FIT_SCHEMA`).

    Raises exactly as `parse_judgment` does. `topic_fit` must be a JSON boolean, so
    any other answer fails closed instead of reading as a pass.
    """
    return _parse(stdout, TOPIC_FIT_SCHEMA)


def _parse(stdout: str, schema: dict) -> dict:
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise JudgeError(f"Claude Code CLI output is not valid JSON ({exc}): {stdout[:200]!r}") from exc
    if not isinstance(envelope, dict):
        raise JudgeError(f"Claude Code CLI output is not a JSON object: {stdout[:200]!r}")
    if envelope.get("is_error"):
        if _is_auth_failure(envelope):
            raise JudgeAuthError(f"{_one_line(envelope.get('result'))} {AUTH_HINT}")
        if _mentions_quota_exhausted(str(envelope.get("result", ""))):
            raise JudgeQuotaError(f"Claude Code CLI error: {_one_line(envelope.get('result'))}")
        raise JudgeError(f"Claude Code CLI error: {_one_line(envelope.get('result') or envelope.get('subtype'))}")
    judgment = envelope.get("structured_output")
    if judgment is None:
        raise JudgeError(f"Claude Code CLI returned no structured_output (result: {envelope.get('result')!r})")
    return _validate(judgment, schema)


def run_claude(prompt: str, system: str, schema: dict = JUDGMENT_SCHEMA) -> str:
    """Invoke the Claude Code CLI headless and return its stdout (the JSON envelope).

    The prompt goes in on stdin, not argv: it carries feed text of any length, and
    one argv string over Linux's 128 KiB cap fails the exec with E2BIG. The system
    prompt stays in argv; it is built from the guide's sections and a few example
    blurbs (a few KB), not from feed content.
    """
    cmd = [
        "claude", "-p",
        "--system-prompt", system,
        "--model", MODEL,
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
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
                cmd,
                input=prompt,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=CLI_TIMEOUT_SECONDS,
                check=False,
            )
    except FileNotFoundError as exc:
        raise JudgeLaunchError("Claude Code CLI (`claude`) is not installed or not on PATH") from exc
    except OSError as exc:
        raise JudgeLaunchError(f"Claude Code CLI could not be started: {_one_line(exc)}") from exc
    except subprocess.TimeoutExpired as exc:
        raise JudgeError(f"Claude Code CLI timed out after {CLI_TIMEOUT_SECONDS}s") from exc
    # On API errors the CLI exits non-zero but still prints the JSON envelope; parse that.
    if proc.returncode != 0 and not proc.stdout.strip():
        message = f"Claude Code CLI exited {proc.returncode}: {_one_line(proc.stderr)}"
        if _mentions_auth_failure(proc.stderr):
            raise JudgeAuthError(f"{message} {AUTH_HINT}")
        if _mentions_quota_exhausted(proc.stderr):
            raise JudgeQuotaError(message)
        raise JudgeError(message)
    return proc.stdout


def run_claude_topic_fit(prompt: str, system: str) -> str:
    """`run_claude` with the Topic fit schema."""
    return run_claude(prompt, system, TOPIC_FIT_SCHEMA)


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
    a usage limit resets in hours, not within the backoff, and so is
    `JudgeLaunchError`, since a CLI that cannot be started fails the same way every
    time. Once attempts run out, the last error is raised.

    The summary is capped at `MAX_SUMMARY_CHARS`, with a note saying so, since
    release notes can run to hundreds of KB; the title is cut to `MAX_TITLE_CHARS`.
    """
    if summary and len(summary) > MAX_SUMMARY_CHARS:
        summary = (
            f"{summary[:MAX_SUMMARY_CHARS]}\n"
            f"[... truncated: showing the first {MAX_SUMMARY_CHARS:,} of {len(summary):,} characters]"
        )
    prompt = (
        f"Evaluate this candidate resource for inclusion.\n\n"
        f"Title: {str(title)[:MAX_TITLE_CHARS]}\n"
        f"URL: {url}\n"
        f"Source feed: {source_id}\n"
        f"Summary/description:\n{summary or '(no summary available)'}"
    )
    return _with_retries(lambda: parse_judgment(run(prompt, system)), _one_line(url, 200), sleep)


def judge_topic_fit(
    system: str,
    feed_title: str,
    feed_url: str,
    entries: Iterable[tuple[str, str]],
    run: Callable[[str, str], str] = run_claude_topic_fit,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Judge whether a feed, as a whole, has Topic fit; return `{"topic_fit", "rationale"}`.

    `entries` are the (title, summary) pairs of a Trial's sample, newest first. Takes
    the same system prompt as `judge_entry` (the guide's sections and inclusion
    criteria) and shares its CLI hardening, retry policy and error types: an
    off-schema answer is retried and, once attempts run out, raised as `JudgeError`,
    never read as a pass. Each summary is cut to `TOPIC_FIT_SUMMARY_CHARS` and each
    title to `MAX_TITLE_CHARS`.
    """
    lines = []
    for i, (title, summary) in enumerate(entries, start=1):
        summary = str(summary or "")
        if len(summary) > TOPIC_FIT_SUMMARY_CHARS:
            summary = f"{summary[:TOPIC_FIT_SUMMARY_CHARS]} [... truncated]"
        lines.append(
            f"{i}. Title: {str(title)[:MAX_TITLE_CHARS]}\n"
            f"   Summary: {summary or '(no summary available)'}"
        )
    prompt = (
        "This is a feed-level question, not an entry judgment. The per-entry rules in the "
        "system prompt (the GitHub release-notes capability bar, the news/announcements "
        "rule, reject-if-a-similar-resource-exists, and any language rule) are for judging "
        "single entries later in the Trial; they are NOT Topic fit criteria. Topic fit is "
        "about subject matter only: for example, a release feed of an agent framework or "
        "SDK has Topic fit even if most of its releases are patch or bugfix releases.\n\n"
        "Decide whether this feed has Topic fit for the guide: whether the feed as a whole "
        "is about building, evaluating, operating or securing agentic systems. Judge the "
        "feed, not any single entry: a feed mostly about something else (for example "
        "general model serving, embeddings or training) lacks Topic fit even when an "
        "occasional entry touches on agents. Use the guide's sections and inclusion "
        "criteria as context. Answer with `topic_fit` and a one- or two-sentence "
        "`rationale`.\n\n"
        f"Feed title: {str(feed_title or '(untitled)')[:MAX_TITLE_CHARS]}\n"
        f"Feed URL: {_one_line(feed_url, 500)}\n"
        "Recent entries, newest first:\n" + ("\n".join(lines) or "(none)")
    )
    return _with_retries(
        lambda: parse_topic_fit(run(prompt, system)), f"Topic fit of {_one_line(feed_url, 200)}", sleep,
    )


def _with_retries(attempt_once: Callable[[], dict], subject: str, sleep: Callable[[float], None]) -> dict:
    """Make one judge call with `judge_entry`'s retry policy; `subject` (already one
    line) names the call in the retry warning."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return attempt_once()
        except (JudgeAuthError, JudgeQuotaError, JudgeLaunchError):
            raise
        except JudgeError as exc:
            if attempt == MAX_ATTEMPTS:
                raise
            delay = attempt * RETRY_BACKOFF_SECONDS
            print(
                f"::warning::judge attempt {attempt}/{MAX_ATTEMPTS} failed for {subject}: "
                f"{_one_line(exc)}; "
                f"retrying in {delay}s",
                flush=True,
            )
            sleep(delay)
    raise AssertionError("unreachable")
