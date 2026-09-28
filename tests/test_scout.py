"""Unit tests for scripts/scout.py fail-closed judging logic."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import scout  # noqa: E402


def _entry(url: str, title: str = "t") -> dict:
    return {"link": url, "title": title, "summary": "s"}


def _judgment(decision: str, slug: str = "slug") -> dict:
    return {
        "decision": decision,
        "section": "patterns",
        "slug": slug,
        "title": "T",
        "author": "A",
        "type": "article",
        "license": None,
        "blurb": "b",
        "tags": [],
        "rationale": "r",
    }


def _stub_judge(judgments: dict):
    """Judge returning canned judgments by URL; an Exception value is raised."""

    def judge(title, url, summary, source_id):
        result = judgments[url]
        if isinstance(result, Exception):
            raise result
        return result

    return judge


def test_source_is_fully_judged_when_every_new_entry_is_judged() -> None:
    judge = _stub_judge({
        "https://a/1": _judgment("include", "one"),
        "https://a/2": _judgment("reject"),
    })
    run = scout.judge_sources(
        {"src-a": [_entry("https://a/1"), _entry("https://a/2")]},
        judge,
        known_urls=set(),
        existing_slugs=set(),
    )

    assert run.errors == []
    assert run.fully_judged == {"src-a"}
    assert [c["url"] for c in run.candidates] == ["https://a/1"]
    assert [r["url"] for r in run.rejected] == ["https://a/2"]


def test_first_judge_error_stops_judging_and_blocks_every_later_bump() -> None:
    stub = _stub_judge({
        "https://a/1": _judgment("reject"),
        "https://a/2": RuntimeError("credit balance is too low"),
        "https://a/3": _judgment("reject"),
        "https://b/1": _judgment("reject"),
    })
    called: list[str] = []

    def judge(title, url, summary, source_id):
        called.append(url)
        return stub(title, url, summary, source_id)

    run = scout.judge_sources(
        {
            "src-z": [_entry("https://z/1")],
            "src-a": [_entry("https://a/1"), _entry("https://a/2", "Broken"), _entry("https://a/3")],
            "src-b": [_entry("https://b/1")],
        },
        judge,
        known_urls={"https://z/1"},
        existing_slugs=set(),
    )

    assert called == ["https://a/1", "https://a/2"]
    assert len(run.errors) == 1
    assert "src-a" in run.errors[0]
    assert "credit balance is too low" in run.errors[0]
    assert run.fully_judged == {"src-z"}


def test_non_auth_judge_error_hints_at_a_rerun_not_the_credential() -> None:
    run = scout.judge_sources(
        {"src-a": [_entry("https://a/1")]},
        _stub_judge({"https://a/1": scout.judge.JudgeError("Claude Code CLI timed out after 300s")}),
        known_urls=set(),
        existing_slugs=set(),
    )

    assert len(run.errors) == 1
    assert run.auth_failed is False
    hint = scout.judge_failure_hint(run)
    assert "re-run" in hint
    assert "0001-oauth-token-for-ci" not in hint


def test_judge_auth_error_hints_at_the_credential() -> None:
    run = scout.judge_sources(
        {"src-a": [_entry("https://a/1")]},
        _stub_judge({"https://a/1": scout.judge.JudgeAuthError("401 OAuth access token is invalid.")}),
        known_urls=set(),
        existing_slugs=set(),
    )

    assert run.auth_failed is True
    assert "docs/adr/0001-oauth-token-for-ci.md" in scout.judge_failure_hint(run)


def test_limit_leaves_unfinished_sources_unbumped() -> None:
    judge = _stub_judge({url: _judgment("reject") for url in ("https://a/1", "https://b/1", "https://b/2", "https://c/1")})
    run = scout.judge_sources(
        {
            "src-a": [_entry("https://a/1")],
            "src-b": [_entry("https://b/1"), _entry("https://b/2")],
            "src-c": [_entry("https://c/1")],
        },
        judge,
        known_urls=set(),
        existing_slugs=set(),
        limit=2,
    )

    assert run.evaluated == 2
    assert run.errors == []
    assert run.fully_judged == {"src-a"}


def test_known_urls_are_skipped_without_blocking_the_bump() -> None:
    judge = _stub_judge({"https://a/2": _judgment("reject")})
    run = scout.judge_sources(
        {"src-a": [_entry("https://a/1"), _entry("https://a/2")], "src-b": []},
        judge,
        known_urls={"https://a/1"},
        existing_slugs=set(),
    )

    assert run.evaluated == 1
    assert run.fully_judged == {"src-a", "src-b"}


# --- Scout auto-merge gate -------------------------------------------------

SECTIONS = {"patterns", "foundations"}
TYPES = {"article", "paper"}


def _candidate(section: str = "patterns", type_: str = "article", slug: str = "c") -> dict:
    return {"slug": slug, "section": section, "type": type_, "url": f"https://x/{slug}"}


def test_gate_passes_clean_candidates() -> None:
    decision = scout.evaluate_scout_automerge(
        [_candidate(slug="a"), _candidate("foundations", "paper", "b")], SECTIONS, TYPES
    )

    assert decision.auto_merge_ok is True
    assert decision.reasons == []
    assert decision.labels == ["automated", "scout"]


def test_gate_holds_when_candidate_count_exceeds_default_cap_of_8() -> None:
    candidates = [_candidate(slug=str(i)) for i in range(9)]

    decision = scout.evaluate_scout_automerge(candidates, SECTIONS, TYPES)

    assert decision.auto_merge_ok is False
    assert decision.reasons == ["9 candidates exceed cap of 8"]
    assert decision.labels == ["automated", "scout", "auto-merge-skipped"]


def test_gate_passes_exactly_at_cap() -> None:
    candidates = [_candidate(slug=str(i)) for i in range(8)]

    assert scout.evaluate_scout_automerge(candidates, SECTIONS, TYPES).auto_merge_ok is True


def test_gate_cap_override_raises_the_limit_for_one_run() -> None:
    candidates = [_candidate(slug=str(i)) for i in range(20)]

    assert scout.evaluate_scout_automerge(candidates, SECTIONS, TYPES, cap=20).auto_merge_ok is True
    assert scout.evaluate_scout_automerge(candidates, SECTIONS, TYPES, cap=19).reasons == [
        "20 candidates exceed cap of 19"
    ]


def test_gate_passes_zero_candidates_even_with_a_zero_cap() -> None:
    decision = scout.evaluate_scout_automerge([], SECTIONS, TYPES, cap=0)

    assert decision == (True, [], ["automated", "scout"])


def test_gate_holds_when_a_candidate_section_is_not_a_known_section_id() -> None:
    decision = scout.evaluate_scout_automerge(
        [_candidate(slug="ok"), _candidate("hallucinated", slug="bad")], SECTIONS, TYPES
    )

    assert decision.auto_merge_ok is False
    assert decision.reasons == ["candidate `bad` has unknown section `hallucinated`"]
    assert decision.labels == ["automated", "scout", "auto-merge-skipped"]


def test_gate_holds_when_a_candidate_type_is_outside_the_enum() -> None:
    decision = scout.evaluate_scout_automerge([_candidate(type_="podcast", slug="pod")], SECTIONS, TYPES)

    assert decision.auto_merge_ok is False
    assert decision.reasons == ["candidate `pod` has unknown type `podcast`"]
    assert decision.labels == ["automated", "scout", "auto-merge-skipped"]


def test_gate_default_types_are_the_judgment_schema_enum() -> None:
    known = scout.evaluate_scout_automerge([_candidate(type_="repo")], SECTIONS)
    unknown = scout.evaluate_scout_automerge([_candidate(type_="tweet")], SECTIONS)

    assert known.auto_merge_ok is True
    assert unknown.auto_merge_ok is False


def test_appending_candidates_never_touches_top_7() -> None:
    data = {"top_7": ["keep-me"], "resources": [{"id": "keep-me"}]}
    candidate = {**_candidate(slug="new"), "title": "T", "author": "A", "blurb": "b", "tags": ["x"]}

    scout.append_candidates(data, [candidate], "2026-09-28")

    assert data["top_7"] == ["keep-me"]
    assert [r["id"] for r in data["resources"]] == ["keep-me", "new"]
    assert not any(k.startswith("top_7") for k in data["resources"][1])
    assert data["resources"][1]["added_at"] == "2026-09-28"


def test_automerge_decision_reads_candidates_file_and_section_ids(tmp_path) -> None:
    resources = tmp_path / "resources.yaml"
    resources.write_text("sections:\n  - id: patterns\ntop_7: []\nresources: []\n")
    candidates = tmp_path / "candidates.yaml"
    candidates.write_text(
        "candidates:\n"
        "  - {slug: a, section: patterns, type: article}\n"
        "  - {slug: b, section: nope, type: article}\n"
    )

    held = scout.automerge_decision(candidates, resources, cap=8)
    over_cap = scout.automerge_decision(candidates, resources, cap=1)
    bookkeeping = scout.automerge_decision(tmp_path / "missing.yaml", resources, cap=0)

    assert held == {
        "auto_merge_ok": False,
        "reasons": ["candidate `b` has unknown section `nope`"],
        "labels": ["automated", "scout", "auto-merge-skipped"],
    }
    assert over_cap["reasons"][0] == "2 candidates exceed cap of 1"
    assert bookkeeping["auto_merge_ok"] is True
