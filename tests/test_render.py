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
    # All 14 sections must render after T2
    for n in range(1, 15):
        assert f"## {n}. " in rendered_output


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


def test_multi_agent_research_system_renders_correctly(rendered_output: str) -> None:
    # top-7 uses top_7_title (short form) + top_7_author + top_7_blurb
    assert "[How we built our multi-agent research system]" in rendered_output
    assert "The best single multi-agent case study, with concrete failure modes." in rendered_output
    # section 14 uses the full title with author prefix embedded
    assert "[Anthropic — How we built our multi-agent research system]" in rendered_output
    assert "Best multi-agent case study, period." in rendered_output


def test_cluster_label_renders_as_bold_prefix(rendered_output: str) -> None:
    # benchmarks-9 cluster must render as "- **Benchmarks:** link1, link2, ..., linkN."
    assert "- **Benchmarks:** [SWE-bench]" in rendered_output
    assert "[SWE-Lancer]" in rendered_output
    # Must NOT use Oxford "and" join (cluster_label uses plain comma join)
    assert ", and [SWE-Lancer]" not in rendered_output


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
