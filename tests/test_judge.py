"""Unit tests for scripts/judge.py: parsing and validating Claude Code CLI output."""

from __future__ import annotations

import json
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
