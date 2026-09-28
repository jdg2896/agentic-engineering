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


def test_usage_limit_stops_judging_but_keeps_the_judgments_made_so_far() -> None:
    stub = _stub_judge({
        "https://a/1": _judgment("include", "one"),
        "https://b/1": _judgment("reject"),
        "https://b/2": scout.judge.JudgeQuotaError("Claude Code CLI error: You've hit your session limit"),
        "https://b/3": _judgment("reject"),
        "https://c/1": _judgment("reject"),
    })
    called: list[str] = []

    def judge(title, url, summary, source_id):
        called.append(url)
        return stub(title, url, summary, source_id)

    run = scout.judge_sources(
        {
            "src-a": [_entry("https://a/1")],
            "src-b": [_entry("https://b/1"), _entry("https://b/2"), _entry("https://b/3")],
            "src-c": [_entry("https://c/1")],
        },
        judge,
        known_urls=set(),
        existing_slugs=set(),
    )

    assert called == ["https://a/1", "https://b/1", "https://b/2"]
    assert run.errors == []
    assert run.quota_exhausted is not None and "session limit" in run.quota_exhausted
    assert run.fully_judged == {"src-a"}
    assert run.evaluated == 2
    assert [c["url"] for c in run.candidates] == ["https://a/1"]
    assert [r["url"] for r in run.rejected] == ["https://b/1"]


def test_non_quota_judge_errors_leave_quota_exhausted_unset() -> None:
    for exc in (scout.judge.JudgeError("timed out"), scout.judge.JudgeAuthError("401")):
        run = scout.judge_sources(
            {"src-a": [_entry("https://a/1")]},
            _stub_judge({"https://a/1": exc}),
            known_urls=set(),
            existing_slugs=set(),
        )

        assert len(run.errors) == 1
        assert run.quota_exhausted is None


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


# --- Judge free-text sanitizing ---


def test_sanitize_collapses_newlines_and_whitespace() -> None:
    assert scout.sanitize_text("  line one\n\n line\ttwo\r\n ") == "line one line two"


def test_sanitize_neutralises_env_file_delimiter_injection() -> None:
    out = scout.sanitize_text("A blurb\n__EOF__\nAUTO_MERGE_OK=true")
    assert "\n" not in out
    assert out == "A blurb __EOF__ AUTO_MERGE_OK=true"


def test_sanitize_strips_control_characters() -> None:
    assert scout.sanitize_text("a\x00b\x1bc\x7f") == "abc"


def test_sanitize_escapes_markdown_links_and_html() -> None:
    out = scout.sanitize_text("[click](https://evil.example) <img src=x> `code`")
    assert out == "\\[click\\](https://evil.example) \\<img src=x\\> \\`code\\`"


def test_sanitize_truncates_to_max_len() -> None:
    assert scout.sanitize_text("x" * 600) == "x" * 500
    assert scout.sanitize_text("abcdef", max_len=3) == "abc"


def test_sanitize_truncates_before_escaping_so_no_escape_is_split() -> None:
    assert scout.sanitize_text("ab[cd", max_len=3) == "ab\\["


def test_sanitize_escapes_backslashes_so_pre_escaped_links_stay_dead() -> None:
    out = scout.sanitize_text("\\[x\\](https://evil) \\<b>")
    # Each backslash is doubled and each [ < still carries its own escape.
    assert out == "\\\\\\[x\\\\\\](https://evil) \\\\\\<b\\>"


def test_candidates_carry_sanitized_judge_text_but_raw_url() -> None:
    judgment = _judgment("include", "one")
    judgment.update({
        "title": "Evil\n__EOF__",
        "author": "[me](https://x)",
        "blurb": "b\nAUTO_MERGE_OK=true",
        "rationale": "<script>",
        "tags": ["a\nb", "`t`"],
    })
    run = scout.judge_sources(
        {"src-a": [_entry("https://a/1?x=[1]")]},
        _stub_judge({"https://a/1?x=[1]": judgment}),
        known_urls=set(),
        existing_slugs=set(),
    )

    [c] = run.candidates
    assert c["url"] == "https://a/1?x=[1]"
    assert c["title"] == "Evil __EOF__"
    assert c["author"] == "\\[me\\](https://x)"
    assert c["blurb"] == "b AUTO_MERGE_OK=true"
    assert c["rationale"] == "\\<script\\>"
    assert c["tags"] == ["a b", "\\`t\\`"]


def test_invalid_slug_fails_closed_like_a_judge_error() -> None:
    judge = _stub_judge({
        "https://a/1": _judgment("include", "../../Evil Slug"),
        "https://a/2": _judgment("include", "two"),
    })
    run = scout.judge_sources(
        {"src-a": [_entry("https://a/1"), _entry("https://a/2")]},
        judge,
        known_urls=set(),
        existing_slugs=set(),
    )

    assert run.candidates == []
    assert len(run.errors) == 1 and "invalid slug" in run.errors[0]
    assert run.fully_judged == set()


def test_slug_with_trailing_newline_is_rejected() -> None:
    run = scout.judge_sources(
        {"src-a": [_entry("https://a/1")]},
        _stub_judge({"https://a/1": _judgment("include", "ok\n")}),
        known_urls=set(),
        existing_slugs=set(),
    )
    assert run.candidates == [] and run.errors


def test_valid_slug_passes_and_license_is_sanitized() -> None:
    judgment = _judgment("include", "good-slug-2")
    judgment["license"] = "MIT\n[x](https://evil)"
    run = scout.judge_sources(
        {"src-a": [_entry("https://a/1")]},
        _stub_judge({"https://a/1": judgment}),
        known_urls=set(),
        existing_slugs=set(),
    )

    [c] = run.candidates
    assert c["slug"] == "good-slug-2"
    assert c["license"] == "MIT \\[x\\](https://evil)"


def test_null_license_stays_null() -> None:
    run = scout.judge_sources(
        {"src-a": [_entry("https://a/1")]},
        _stub_judge({"https://a/1": _judgment("include", "one")}),
        known_urls=set(),
        existing_slugs=set(),
    )
    assert run.candidates[0]["license"] is None


def test_logged_judge_and_feed_text_cannot_start_a_workflow_command(capsys) -> None:
    judgment = _judgment("include", "one")
    judgment["blurb"] = "ok\n::add-mask::x"
    rejected = _judgment("reject")
    rejected["rationale"] = "no\n::error::boom"
    scout.judge_sources(
        {"src-a": [_entry("https://a/1", title="t\n::warning::w"), _entry("https://a/2")]},
        _stub_judge({"https://a/1": judgment, "https://a/2": rejected}),
        known_urls=set(),
        existing_slugs=set(),
    )

    assert not any(line.startswith("::") for line in capsys.readouterr().out.splitlines())


# --- candidates.yaml ---


def test_load_candidates_ignores_the_incomplete_key(tmp_path) -> None:
    path = tmp_path / "candidates.yaml"
    path.write_text("candidates:\n  - {slug: a}\nincomplete: hit the usage limit\n")

    assert scout.load_candidates(path) == [{"slug": "a"}]
    assert scout.load_incomplete_reason(path) == "hit the usage limit"


def test_load_incomplete_reason_is_none_for_a_complete_or_missing_file(tmp_path) -> None:
    complete = tmp_path / "candidates.yaml"
    complete.write_text("candidates: []\n")
    empty = tmp_path / "empty.yaml"
    empty.write_text("")

    assert scout.load_incomplete_reason(complete) is None
    assert scout.load_incomplete_reason(empty) is None
    assert scout.load_incomplete_reason(tmp_path / "missing.yaml") is None


def test_candidates_file_round_trips_the_sanitized_incomplete_reason(tmp_path) -> None:
    path = tmp_path / "candidates.yaml"
    run = scout.ScoutRun(candidates=[{"slug": "a"}], quota_exhausted="limit\n::error::[x](y)")

    scout.write_candidates(path, run)

    assert scout.load_candidates(path) == [{"slug": "a"}]
    assert scout.load_incomplete_reason(path) == "limit ::error::\\[x\\](y)"


def test_complete_run_writes_no_incomplete_key(tmp_path) -> None:
    path = tmp_path / "candidates.yaml"

    scout.write_candidates(path, scout.ScoutRun())

    assert "incomplete" not in path.read_text()
    assert scout.load_incomplete_reason(path) is None
