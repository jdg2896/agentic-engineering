"""Smoke tests for scripts/render.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import render  # noqa: E402


@pytest.fixture
def rendered_output() -> str:
    return render.render()


def test_render_runs_without_errors(rendered_output: str) -> None:
    assert rendered_output


def test_render_is_idempotent() -> None:
    first = render.render()
    second = render.render()
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


def test_no_checkmark_marks(rendered_output: str) -> None:
    # ✓ marks were dropped — the verifier process is the contract, not per-link badges
    assert " ✓" not in rendered_output


def test_cluster_collapses_into_single_bullet(rendered_output: str) -> None:
    # awesome-mcp-servers cluster: two URLs, one bullet, joined by " and "
    line = (
        "- [wong2/awesome-mcp-servers](https://github.com/wong2/awesome-mcp-servers) "
        "and [appcypher/awesome-mcp-servers](https://github.com/appcypher/awesome-mcp-servers) "
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


def test_header_subtitle(rendered_output: str) -> None:
    # The compile-date framing is gone; subtitle names the maintenance model
    assert "Continuously maintained — links verified weekly." in rendered_output
    assert "Compiled" not in rendered_output


def test_worth_following_section_renders(rendered_output: str) -> None:
    assert "## Worth following for ongoing signal" in rendered_output
    # Entries are derived from Yield, each with its generated line
    assert " Resources in the guide, mostly " in rendered_output
    # No legacy "Caveats" header
    assert "## Caveats" not in rendered_output


def test_opinionated_stack_section_is_gone(rendered_output: str) -> None:
    assert "Opinionated minimal stack" not in rendered_output


def test_render_script_exits_cleanly() -> None:
    """`uv run python scripts/render.py` succeeds with no errors."""
    result = subprocess.run(
        ["uv", "run", "python", "scripts/render.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


# --- Quarantine filtering ----------------------------------------------------


def _section_fixtures() -> list[dict]:
    return [{"id": "s1", "order": 1, "title": "Section One"}]


def test_build_populated_sections_excludes_quarantined() -> None:
    sections = _section_fixtures()
    resources = [
        {"id": "alive", "section": "s1", "title": "Alive", "url": "https://a/"},
        {
            "id": "dead",
            "section": "s1",
            "title": "Dead",
            "url": "https://d/",
            "quarantined_at": "2026-05-03",
        },
    ]
    populated = render.build_populated_sections(sections, resources)
    bullets = " ".join(populated[0]["bullets"])
    assert "Alive" in bullets
    assert "Dead" not in bullets


def test_build_populated_sections_still_excludes_hidden_and_superseded() -> None:
    sections = _section_fixtures()
    resources = [
        {"id": "a", "section": "s1", "title": "A", "url": "https://a/"},
        {"id": "b", "section": "s1", "title": "B", "url": "https://b/", "hidden": True},
        {"id": "c", "section": "s1", "title": "C", "url": "https://c/", "superseded_by": "a"},
    ]
    populated = render.build_populated_sections(sections, resources)
    bullets = " ".join(populated[0]["bullets"])
    assert "A" in bullets
    assert "B" not in bullets
    assert "C" not in bullets


def test_top_7_check_passes_when_nothing_quarantined() -> None:
    by_id = {
        "x": {"id": "x", "title": "X", "url": "https://x/"},
        "y": {"id": "y", "title": "Y", "url": "https://y/"},
    }
    render._check_top_7_not_quarantined(["x", "y"], by_id)  # no raise


def test_top_7_check_raises_with_useful_message_on_quarantined_slug() -> None:
    by_id = {
        "x": {"id": "x", "title": "X", "url": "https://x/"},
        "y": {
            "id": "y",
            "title": "Y",
            "url": "https://y/",
            "quarantined_at": "2026-05-03",
            "quarantine_reason": "404",
        },
    }
    with pytest.raises(ValueError, match=r"top_7 references quarantined.*y \(404\)"):
        render._check_top_7_not_quarantined(["x", "y"], by_id)


# --- Link destination escaping (last line of defense for untrusted urls) ---

INJECTED_URL = "https://good.example/post) — **Editor pick:** [Download](https://evil.example/pay"
ESCAPED_INJECTED_URL = (
    "https://good.example/post%29%20%E2%80%94%20**Editor%20pick:**%20%5BDownload%5D%28https://evil.example/pay"
)


def test_link_url_leaves_ordinary_urls_untouched() -> None:
    url = "https://example.com/a-b_c.d~e/f?x=1&y=%5B;z=a+b,c*d!e$f'g@h#frag:1"
    assert render.link_url(url) == url


def test_link_url_percent_encodes_markdown_breaking_characters() -> None:
    assert render.link_url(INJECTED_URL) == ESCAPED_INJECTED_URL
    assert render.link_url('https://x/<a b="c">\t{|}^`\\') == "https://x/%3Ca%20b=%22c%22%3E%09%7B%7C%7D%5E%60%5C"


def test_format_link_cannot_be_broken_out_of_by_its_url() -> None:
    link = render.format_link({"title": "Post", "url": INJECTED_URL})
    assert link == f"[Post]({ESCAPED_INJECTED_URL})"


def test_top_7_line_escapes_its_url() -> None:
    line = render.render_top_7_line({"title": "Post", "url": INJECTED_URL, "author": "A", "blurb": "b"})
    assert line == f"[Post]({ESCAPED_INJECTED_URL}) — A. b"


def test_worth_following_link_escapes_its_url(tmp_path, monkeypatch) -> None:
    _write_guide(
        tmp_path, monkeypatch,
        sources=[_source("feed", feed_title="Feed", site_url=INJECTED_URL)],
        resources=_yielded("feed", 3),
    )

    assert f"- [Feed]({ESCAPED_INJECTED_URL}) — 3 Resources in the guide, mostly Section One" in render.render()


# --- Worth following (derived from Yield) -------------------------------------

WF_SECTIONS = [
    {"id": "s2", "order": 2, "title": "Section Two"},
    {"id": "s1", "order": 1, "title": "Section One"},
]


def _source(sid: str, **fields: object) -> dict:
    return {"id": sid, "url": f"https://{sid}.example/feed.xml", "enabled": True, **fields}


def _yielded(sid: str, n: int, section: str = "s1", **fields: object) -> list[dict]:
    return [
        {"id": f"{sid}-{section}-{i}", "section": section, "title": "T", "url": f"https://{sid}.example/{i}",
         "source_id": sid, **fields}
        for i in range(n)
    ]


def _names(sources: list[dict], resources: list[dict]) -> list[str]:
    return [e.name for e in render.worth_following(sources, resources, WF_SECTIONS)]


def test_worth_following_needs_a_lifetime_yield_of_three() -> None:
    sources = [_source("two", feed_title="Two"), _source("three", feed_title="Three")]
    resources = _yielded("two", 2) + _yielded("three", 3)
    assert _names(sources, resources) == ["Three"]
    assert render.WORTH_FOLLOWING_MIN_YIELD == 3


def test_worth_following_excludes_retired_and_disabled_sources() -> None:
    sources = [
        _source("kept", feed_title="Kept"),
        _source("retired", feed_title="Retired", enabled=False, retired_at="2026-09-29",
                retired_reason="dead-silent"),
        _source("disabled", feed_title="Disabled", enabled=False),
        _source("null", feed_title="Null", enabled=None),
    ]
    resources = _yielded("kept", 3) + _yielded("retired", 5) + _yielded("disabled", 5) + _yielded("null", 5)
    assert _names(sources, resources) == ["Kept"]


def test_worth_following_lists_a_revived_source() -> None:
    # Reviving sets `enabled: true` and may leave the retirement fields behind.
    source = _source("revived", feed_title="Revived", retired_at="2026-09-29", retired_reason="dead-silent")
    assert _names([source], _yielded("revived", 3)) == ["Revived"]


def test_worth_following_treats_a_missing_enabled_as_enabled() -> None:
    source = _source("feed", feed_title="Feed")
    del source["enabled"]
    assert _names([source], _yielded("feed", 3)) == ["Feed"]


def test_worth_following_ignores_resources_without_a_source_id() -> None:
    resources = _yielded("feed", 2) + [
        {"id": "loose", "section": "s1", "title": "T", "url": "https://feed.example/x"},
        {"id": "null", "section": "s1", "title": "T", "url": "https://feed.example/y", "source_id": None},
    ]
    assert _names([_source("feed", feed_title="Feed")], resources) == []


def test_worth_following_counts_archived_quarantined_and_hidden_resources() -> None:
    resources = (
        _yielded("feed", 1, archived=True)
        + _yielded("feed", 1, section="s2", quarantined_at="2026-05-25")
        + [{"id": "h", "section": "s1", "title": "T", "url": "https://feed.example/h", "source_id": "feed",
            "hidden": True}]
    )
    [entry] = render.worth_following([_source("feed", feed_title="Feed")], resources, WF_SECTIONS)
    assert entry.yield_count == 3


def test_worth_following_orders_by_yield_then_name() -> None:
    sources = [
        _source("b", feed_title="beta"),
        _source("a", feed_title="Alpha"),
        _source("c", feed_title="Gamma"),
    ]
    resources = _yielded("a", 3) + _yielded("b", 3) + _yielded("c", 4)
    assert _names(sources, resources) == ["Gamma", "Alpha", "beta"]


def test_worth_following_main_section_is_the_most_frequent() -> None:
    resources = _yielded("feed", 1, section="s1") + _yielded("feed", 2, section="s2")
    [entry] = render.worth_following([_source("feed", feed_title="Feed")], resources, WF_SECTIONS)
    assert entry.main_section == "Section Two"


def test_worth_following_main_section_tie_goes_to_the_earlier_section_in_the_guide() -> None:
    # s2 is listed first and has the first Resource, but s1 comes first in the guide.
    resources = _yielded("feed", 2, section="s2") + _yielded("feed", 2, section="s1")
    [entry] = render.worth_following([_source("feed", feed_title="Feed")], resources, WF_SECTIONS)
    assert entry.main_section == "Section One"


def test_worth_following_uses_feed_title_and_site_url() -> None:
    source = _source("feed", feed_title="The Feed", site_url="https://feed.example/blog/")
    [entry] = render.worth_following([source], _yielded("feed", 3), WF_SECTIONS)
    assert (entry.name, entry.url) == ("The Feed", "https://feed.example/blog/")


def test_worth_following_falls_back_to_the_source_host() -> None:
    source = {"id": "feed", "url": "https://www.Feed.example:8443/a/rss.xml?x=1", "enabled": True,
              "feed_title": None, "site_url": None}
    [entry] = render.worth_following([source], _yielded("feed", 3), WF_SECTIONS)
    assert entry.name == "feed.example"
    assert entry.url == "https://www.feed.example:8443"


def test_worth_following_falls_back_when_title_and_link_are_absent() -> None:
    [entry] = render.worth_following([_source("feed")], _yielded("feed", 3), WF_SECTIONS)
    assert (entry.name, entry.url) == ("feed.example", "https://feed.example")


def test_worth_following_line() -> None:
    [entry] = render.worth_following([_source("feed", feed_title="Feed")], _yielded("feed", 3), WF_SECTIONS)
    assert entry.line == "3 Resources in the guide, mostly Section One"


def test_worth_following_line_is_singular_for_one_resource() -> None:
    entry = render.WorthFollowing(name="Feed", url="https://feed.example", yield_count=1,
                                  main_section="Section One")
    assert entry.line == "1 Resource in the guide, mostly Section One"


def _write_guide(tmp_path, monkeypatch, *, sources: list[dict], resources: list[dict]) -> None:
    guide = tmp_path / "resources.yaml"
    guide.write_text(yaml.safe_dump({"sections": WF_SECTIONS, "top_7": [], "resources": resources}))
    source_list = tmp_path / "sources.yaml"
    source_list.write_text(yaml.safe_dump({"sources": sources}))
    monkeypatch.setattr(render, "RESOURCES_PATH", guide)
    monkeypatch.setattr(render, "SOURCES_PATH", source_list)


def test_render_derives_the_worth_following_section(tmp_path, monkeypatch) -> None:
    _write_guide(
        tmp_path, monkeypatch,
        sources=[
            # feed_title is stored as escaped Markdown: rendered as is, not escaped again
            _source("big", feed_title=r"Big \*Feed\*", site_url="https://big.example/"),
            _source("small", feed_title="Small"),
            _source("host"),
            _source("gone", feed_title="Gone", enabled=False, retired_at="2026-09-29"),
        ],
        resources=(
            _yielded("big", 4, section="s2") + _yielded("small", 2) + _yielded("host", 3)
            + _yielded("gone", 9)
        ),
    )

    out = render.render()

    assert out.endswith(
        "## Worth following for ongoing signal\n\n"
        r"- [Big \*Feed\*](https://big.example/) — 4 Resources in the guide, mostly Section Two" "\n"
        "- [host.example](https://host.example) — 3 Resources in the guide, mostly Section One\n"
    )
