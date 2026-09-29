"""Unit tests for scripts/judge.py: parsing, validating and retrying Claude Code CLI output."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import judge  # noqa: E402

VALID_JUDGMENT = {
    "decision": "include",
    "section": "patterns",
    "slug": "building-effective-agents",
    "title": "Building effective agents",
    "author": "Anthropic",
    "type": "article",
    "license": None,
    "blurb": "Workflows vs. agents, and when each earns its complexity.",
    "tags": ["patterns", "workflows"],
    "rationale": "Reproducible architecture guidance.",
}


def _envelope(**overrides) -> str:
    """A `claude -p --output-format json` result envelope, as the CLI prints it."""
    env = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "api_error_status": None,
        "num_turns": 2,
        "result": json.dumps(VALID_JUDGMENT),
        "structured_output": VALID_JUDGMENT,
    }
    env.update(overrides)
    return json.dumps(env)


def test_valid_output_returns_the_judgment() -> None:
    assert judge.parse_judgment(_envelope()) == VALID_JUDGMENT


def test_malformed_json_raises_judge_error() -> None:
    with pytest.raises(judge.JudgeError, match="not valid JSON"):
        judge.parse_judgment('{"type": "result", "structured_output": {')


def test_missing_required_field_raises_judge_error() -> None:
    partial = {k: v for k, v in VALID_JUDGMENT.items() if k != "section"}
    with pytest.raises(judge.JudgeError, match="section"):
        judge.parse_judgment(_envelope(structured_output=partial))


def test_unknown_decision_value_raises_judge_error() -> None:
    bad = {**VALID_JUDGMENT, "decision": "maybe"}
    with pytest.raises(judge.JudgeError, match="decision"):
        judge.parse_judgment(_envelope(structured_output=bad))


def test_unknown_type_value_raises_judge_error() -> None:
    bad = {**VALID_JUDGMENT, "type": "podcast"}
    with pytest.raises(judge.JudgeError, match="type"):
        judge.parse_judgment(_envelope(structured_output=bad))


def test_missing_structured_output_raises_judge_error() -> None:
    with pytest.raises(judge.JudgeError, match="structured_output"):
        judge.parse_judgment(_envelope(structured_output=None))


def test_auth_failure_raises_auth_error_naming_the_fix() -> None:
    # Captured from a real `claude -p` run with an invalid CLAUDE_CODE_OAUTH_TOKEN.
    stdout = _envelope(
        is_error=True,
        api_error_status=401,
        result="Failed to authenticate. API Error: 401 OAuth access token is invalid.",
        structured_output=None,
    )
    with pytest.raises(judge.JudgeAuthError) as exc_info:
        judge.parse_judgment(stdout)
    message = str(exc_info.value)
    assert "claude setup-token" in message
    assert "one year" in message


def test_other_cli_error_raises_judge_error_with_cli_message() -> None:
    stdout = _envelope(is_error=True, api_error_status=529, result="API Error: 529 Overloaded", structured_output=None)
    with pytest.raises(judge.JudgeError, match="Overloaded") as exc_info:
        judge.parse_judgment(stdout)
    assert not isinstance(exc_info.value, judge.JudgeAuthError)


def test_judge_entry_sends_the_entry_and_system_prompt_to_the_cli() -> None:
    calls = []

    def fake_run(prompt: str, system: str) -> str:
        calls.append((prompt, system))
        return _envelope()

    result = judge.judge_entry("SYSTEM", "A title", "https://example.com/a", "a summary", "src", run=fake_run)

    assert result == VALID_JUDGMENT
    [(prompt, system)] = calls
    assert system == "SYSTEM"
    assert "https://example.com/a" in prompt
    assert "A title" in prompt


def _scripted_run(*outcomes):
    """A fake CLI runner returning (or raising) each outcome in turn, recording calls."""
    calls: list[str] = []
    queue = list(outcomes)

    def run(prompt: str, system: str) -> str:
        calls.append(prompt)
        outcome = queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return run, calls


def _judge(run, sleeps: list[float]) -> dict:
    return judge.judge_entry("SYSTEM", "A title", "https://example.com/a", "s", "src", run=run, sleep=sleeps.append)


def test_judge_entry_retries_a_timed_out_cli_call_and_returns_the_judgment(capsys) -> None:
    run, calls = _scripted_run(judge.JudgeError("Claude Code CLI timed out after 300s"), _envelope())
    sleeps: list[float] = []

    assert _judge(run, sleeps) == VALID_JUDGMENT
    assert len(calls) == 2
    assert len(sleeps) == 1
    assert "timed out" in capsys.readouterr().out


def test_judge_entry_retries_a_transient_cli_error_envelope() -> None:
    overloaded = _envelope(is_error=True, api_error_status=529, result="API Error: 529 Overloaded", structured_output=None)
    run, calls = _scripted_run(overloaded, _envelope())

    assert _judge(run, []) == VALID_JUDGMENT
    assert len(calls) == 2


def test_judge_entry_retries_malformed_model_output() -> None:
    bad = _envelope(structured_output={**VALID_JUDGMENT, "decision": "maybe"})
    run, calls = _scripted_run(bad, _envelope())

    assert _judge(run, []) == VALID_JUDGMENT
    assert len(calls) == 2


def test_judge_entry_raises_the_last_error_once_attempts_are_exhausted() -> None:
    run, calls = _scripted_run(*(judge.JudgeError(f"timed out #{i}") for i in range(judge.MAX_ATTEMPTS)))
    sleeps: list[float] = []

    with pytest.raises(judge.JudgeError, match=f"timed out #{judge.MAX_ATTEMPTS - 1}"):
        _judge(run, sleeps)
    assert len(calls) == judge.MAX_ATTEMPTS == 3
    # No sleep after the final attempt.
    assert len(sleeps) == judge.MAX_ATTEMPTS - 1


def test_judge_entry_does_not_retry_an_auth_failure() -> None:
    unauthorized = _envelope(
        is_error=True,
        api_error_status=401,
        result="Failed to authenticate. API Error: 401 OAuth access token is invalid.",
        structured_output=None,
    )
    run, calls = _scripted_run(unauthorized, _envelope())
    sleeps: list[float] = []

    with pytest.raises(judge.JudgeAuthError):
        _judge(run, sleeps)
    assert len(calls) == 1
    assert sleeps == []


def _fake_cli(monkeypatch, returncode: int, stdout: str, stderr: str) -> list:
    """Stub subprocess.run inside judge so run_claude sees a canned CLI exit."""
    calls: list = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(judge.subprocess, "run", fake_run)
    return calls


def test_cli_exit_with_auth_failure_on_stderr_raises_auth_error_without_retry(monkeypatch) -> None:
    calls = _fake_cli(monkeypatch, 1, "", "Error: Failed to authenticate. OAuth token has expired.")
    sleeps: list[float] = []

    with pytest.raises(judge.JudgeAuthError, match="claude setup-token"):
        judge.judge_entry("SYSTEM", "A title", "https://example.com/a", "s", "src", sleep=sleeps.append)
    assert len(calls) == 1
    assert sleeps == []


def test_cli_exit_with_other_stderr_raises_plain_judge_error(monkeypatch) -> None:
    _fake_cli(monkeypatch, 1, "", "Error: something broke")

    with pytest.raises(judge.JudgeError, match="exited 1: Error: something broke") as exc_info:
        judge.run_claude("prompt", "SYSTEM")
    assert not isinstance(exc_info.value, judge.JudgeAuthError)


# --- Error messages reach `::error::` / `::warning::` log lines, so stay single-line ---


def _single_line(text: str) -> bool:
    return "\n" not in text and "\r" not in text


def test_cli_error_message_is_single_line() -> None:
    stdout = _envelope(is_error=True, api_error_status=529, result="Overloaded\n::error::boom\r\nx", structured_output=None)
    with pytest.raises(judge.JudgeError) as exc_info:
        judge.parse_judgment(stdout)
    assert _single_line(str(exc_info.value))
    assert "Overloaded ::error::boom x" in str(exc_info.value)


def test_auth_error_message_is_single_line() -> None:
    stdout = _envelope(is_error=True, api_error_status=401, result="Failed to authenticate.\n::add-mask::x", structured_output=None)
    with pytest.raises(judge.JudgeAuthError) as exc_info:
        judge.parse_judgment(stdout)
    assert _single_line(str(exc_info.value))


def test_cli_exit_stderr_message_is_single_line(monkeypatch) -> None:
    _fake_cli(monkeypatch, 1, "", "Error: line one\n::error::line two")
    with pytest.raises(judge.JudgeError) as exc_info:
        judge.run_claude("prompt", "SYSTEM")
    assert _single_line(str(exc_info.value))


def test_retry_warning_is_a_single_line_even_with_hostile_url_and_error(capsys) -> None:
    run, _ = _scripted_run(judge.JudgeError("bad\n::error::injected"), _envelope())

    judge.judge_entry("SYSTEM", "t", "https://x.example/a\n::add-mask::y", "s", "src", run=run, sleep=lambda _: None)

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("::warning::judge attempt 1/")


SESSION_LIMIT = "You've hit your session limit · resets 3pm (UTC)"


def test_usage_limit_envelope_raises_quota_error() -> None:
    # The message the CLI printed when the subscription's session limit ran out.
    stdout = _envelope(is_error=True, result=SESSION_LIMIT, structured_output=None)

    with pytest.raises(judge.JudgeQuotaError, match="session limit"):
        judge.parse_judgment(stdout)


@pytest.mark.parametrize("message", [
    "You've hit your usage limit",
    "You've hit your weekly limit · resets Mon 9am",
    "Claude usage limit reached. Your limit will reset at 5pm.",
    "YOU'VE HIT YOUR OPUS LIMIT",
    "5-hour limit reached ∙ resets 3pm",
    "Weekly limit reached ∙ resets Mon",
    "Session limit reached ∙ resets 3pm (UTC)",
    "Opus limit reached ∙ resets Thu",
    "Sonnet limit reached ∙ resets Thu",
])
def test_other_usage_limit_messages_raise_quota_error(message: str) -> None:
    with pytest.raises(judge.JudgeQuotaError):
        judge.parse_judgment(_envelope(is_error=True, result=message, structured_output=None))


@pytest.mark.parametrize("status, message", [
    (429, "API Error: 429 rate limit exceeded"),
    (429, "API Error: 429 Too Many Requests"),
    (429, "Rate limit reached"),
    (None, "Limit reached, try again shortly"),
])
def test_short_rate_limit_is_a_plain_judge_error(status: int, message: str) -> None:
    stdout = _envelope(is_error=True, api_error_status=status, result=message, structured_output=None)

    with pytest.raises(judge.JudgeError) as exc_info:
        judge.parse_judgment(stdout)
    assert not isinstance(exc_info.value, judge.JudgeQuotaError)


def test_auth_failure_takes_precedence_over_quota() -> None:
    stdout = _envelope(
        is_error=True,
        api_error_status=401,
        result="Failed to authenticate. You've hit your usage limit.",
        structured_output=None,
    )

    with pytest.raises(judge.JudgeAuthError):
        judge.parse_judgment(stdout)


def test_judge_entry_does_not_retry_a_usage_limit() -> None:
    limited = _envelope(is_error=True, result=SESSION_LIMIT, structured_output=None)
    run, calls = _scripted_run(limited, _envelope())
    sleeps: list[float] = []

    with pytest.raises(judge.JudgeQuotaError):
        _judge(run, sleeps)
    assert len(calls) == 1
    assert sleeps == []


def test_judge_entry_still_retries_a_short_rate_limit() -> None:
    rate_limited = _envelope(
        is_error=True, api_error_status=429, result="API Error: 429 rate limit exceeded", structured_output=None
    )
    run, calls = _scripted_run(rate_limited, _envelope())

    assert _judge(run, []) == VALID_JUDGMENT
    assert len(calls) == 2


def test_cli_exit_with_usage_limit_on_stderr_raises_quota_error_without_retry(monkeypatch) -> None:
    calls = _fake_cli(monkeypatch, 1, "", f"Error: {SESSION_LIMIT}")
    sleeps: list[float] = []

    with pytest.raises(judge.JudgeQuotaError, match="session limit"):
        judge.judge_entry("SYSTEM", "A title", "https://example.com/a", "s", "src", sleep=sleeps.append)
    assert len(calls) == 1
    assert sleeps == []


def test_quota_error_message_is_single_line() -> None:
    stdout = _envelope(is_error=True, result=f"{SESSION_LIMIT}\n::error::boom\r\nx", structured_output=None)
    with pytest.raises(judge.JudgeQuotaError) as exc_info:
        judge.parse_judgment(stdout)
    assert _single_line(str(exc_info.value))
    assert "session limit · resets 3pm (UTC) ::error::boom x" in str(exc_info.value)


# --- Long feed entries (release notes) must not overflow the CLI's argv (E2BIG) ---


def _recording_cli(monkeypatch) -> list:
    """Stub subprocess.run inside judge, recording (cmd, kwargs) and returning a valid envelope."""
    calls: list = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout=_envelope(), stderr="")

    monkeypatch.setattr(judge.subprocess, "run", fake_run)
    return calls


def test_run_claude_sends_a_huge_prompt_on_stdin_not_argv(monkeypatch) -> None:
    calls = _recording_cli(monkeypatch)
    prompt = "release notes line\n" * 20_000  # ~380 KB, past Linux's 128 KiB per-argument cap

    assert json.loads(judge.run_claude(prompt, "SYSTEM"))["structured_output"] == VALID_JUDGMENT

    [(cmd, kwargs)] = calls
    assert kwargs["input"] == prompt
    assert all("release notes line" not in arg for arg in cmd)
    assert sum(len(arg) for arg in cmd) < 5_000
    # Every other flag is unchanged: headless, no tools, no session, no settings or MCP.
    assert cmd[:2] == ["claude", "-p"]
    assert cmd[cmd.index("--system-prompt") + 1] == "SYSTEM"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    for flag in ("--no-session-persistence", "--strict-mcp-config", "--json-schema"):
        assert flag in cmd
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert kwargs["timeout"] == judge.CLI_TIMEOUT_SECONDS


def _prompt_for(summary: str) -> str:
    calls = []

    def fake_run(prompt: str, system: str) -> str:
        calls.append(prompt)
        return _envelope()

    judge.judge_entry("SYSTEM", "v1.103.0", "https://example.com/r", summary, "src", run=fake_run)
    [prompt] = calls
    return prompt


def test_judge_entry_caps_a_huge_summary_and_says_it_was_truncated() -> None:
    summary = "HEAD-MARKER " + "changelog line\n" * 10_000 + "TAIL-MARKER"  # ~150 KB of release notes

    prompt = _prompt_for(summary)

    assert "HEAD-MARKER" in prompt
    assert "TAIL-MARKER" not in prompt
    assert len(prompt) < 5_000
    assert "truncated" in prompt


def test_judge_entry_passes_a_short_summary_through_whole() -> None:
    summary = "Workflows vs. agents.\nWhen each earns its complexity."

    prompt = _prompt_for(summary)

    assert summary in prompt
    assert "truncated" not in prompt


def test_os_error_launching_the_cli_is_a_judge_error_that_is_not_retried(monkeypatch) -> None:
    calls: list = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        raise OSError(7, "Argument list too long", "claude")

    monkeypatch.setattr(judge.subprocess, "run", fake_run)
    sleeps: list[float] = []

    with pytest.raises(judge.JudgeError, match="Argument list too long") as exc_info:
        judge.judge_entry("SYSTEM", "A title", "https://example.com/a", "s", "src", sleep=sleeps.append)
    assert len(calls) == 1
    assert sleeps == []
    assert _single_line(str(exc_info.value))
