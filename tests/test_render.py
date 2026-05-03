"""Smoke tests for scripts/render.py."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import render  # noqa: E402


REFERENCE_DATE = date(2026, 5, 3)


@pytest.fixture
def rendered_output() -> str:
    return render.render(today=REFERENCE_DATE)


def test_render_runs_without_errors(rendered_output: str) -> None:
    assert rendered_output


def test_render_is_idempotent() -> None:
    first = render.render(today=REFERENCE_DATE)
    second = render.render(today=REFERENCE_DATE)
    assert first == second


def test_top_7_section_appears(rendered_output: str) -> None:
    assert "## 0. If you only read 7 things" in rendered_output
    # All 7 numbered entries
    for i in range(1, 8):
        assert f"\n{i}. [" in rendered_output


def test_populated_sections_appear(rendered_output: str) -> None:
    assert '## 1. Foundational design & "what is an agent"' in rendered_output
    assert "## 2. Tool integration & MCP" in rendered_output


def test_unpopulated_sections_skipped(rendered_output: str) -> None:
    # Sections 3-14 have no resources in T1 → headers must not render
    for n in range(3, 15):
        assert f"## {n}. " not in rendered_output


def test_verified_resources_get_checkmark(rendered_output: str) -> None:
    # building-effective-agents has verified_at: 2026-05-03 → ✓
    assert "[Building Effective Agents](https://www.anthropic.com/research/building-effective-agents) ✓" in rendered_output


def test_unverified_resources_have_no_checkmark(rendered_output: str) -> None:
    # how-anthropic-uses-claude-code has verified_at: null → no ✓
    assert "[How Anthropic teams use Claude Code](https://www.anthropic.com/news/how-anthropic-teams-use-claude-code) —" in rendered_output


def test_cluster_collapses_into_single_bullet(rendered_output: str) -> None:
    # awesome-mcp-servers cluster: two URLs, one bullet, joined by " and "
    line = (
        "- [wong2/awesome-mcp-servers](https://github.com/wong2/awesome-mcp-servers) ✓ "
        "and [appcypher/awesome-mcp-servers](https://github.com/appcypher/awesome-mcp-servers) ✓ "
        "— Two best-maintained registries."
    )
    assert line in rendered_output


def test_top_7_uses_override_blurb(rendered_output: str) -> None:
    # building-effective-agents has a different top_7_blurb than its section blurb
    assert 'The "workflows vs agents" mental model that everything else builds on.' in rendered_output


def test_hidden_resource_only_in_top_7(rendered_output: str) -> None:
    # multi-agent-research-system is hidden + assigned to backend-patterns;
    # it should appear in the top-7 but section 14 must not render.
    assert "[How we built our multi-agent research system]" in rendered_output
    assert "## 14. " not in rendered_output


def test_compilation_date_in_header(rendered_output: str) -> None:
    assert "Compiled May 2026." in rendered_output


def test_render_script_exits_cleanly(tmp_path: Path) -> None:
    """`uv run python scripts/render.py` succeeds with no errors."""
    result = subprocess.run(
        ["uv", "run", "python", "scripts/render.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
