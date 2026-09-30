"""Unit tests for scripts/scout.py fail-closed judging logic."""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

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


def test_judge_error_hint_does_not_promise_a_rerun_fixes_a_deterministic_failure() -> None:
    run = scout.judge_sources(
        {"src-a": [_entry("https://a/1")]},
        _stub_judge({
            "https://a/1": scout.judge.JudgeLaunchError(
                "Claude Code CLI could not be started: [Errno 7] Argument list too long: 'claude'"
            )
        }),
        known_urls=set(),
        existing_slugs=set(),
    )

    hint = scout.judge_failure_hint(run)
    assert "usually fixes it" not in hint
    assert "repeats" in hint


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
    assert run.stopped_early is not None
    assert run.stopped_early.startswith("the Claude usage limit (") and "session limit" in run.stopped_early
    assert run.usage_limit_hit is True
    assert run.fully_judged == {"src-a"}
    assert run.evaluated == 2
    assert [c["url"] for c in run.candidates] == ["https://a/1"]
    assert [r["url"] for r in run.rejected] == ["https://b/1"]


def test_non_quota_judge_errors_are_not_an_early_stop() -> None:
    for exc in (scout.judge.JudgeError("timed out"), scout.judge.JudgeAuthError("401")):
        run = scout.judge_sources(
            {"src-a": [_entry("https://a/1")]},
            _stub_judge({"https://a/1": exc}),
            known_urls=set(),
            existing_slugs=set(),
        )

        assert len(run.errors) == 1
        assert run.stopped_early is None
        assert run.usage_limit_hit is False


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
    assert run.stopped_early is None  # a manual, local option: no `incomplete` reason


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
    candidate = {**_candidate(slug="new"), "source_id": "s", "title": "T", "author": "A", "blurb": "b", "tags": ["x"]}

    scout.append_candidates(data, [candidate], "2026-09-28")

    assert data["top_7"] == ["keep-me"]
    assert [r["id"] for r in data["resources"]] == ["keep-me", "new"]
    assert not any(k.startswith("top_7") for k in data["resources"][1])
    assert data["resources"][1]["added_at"] == "2026-09-28"


def test_appended_resources_are_attributed_to_the_source_they_were_judged_from() -> None:
    run = scout.judge_sources(
        {
            "src-a": [_entry("https://a/1")],
            "src-b": [_entry("https://b/1"), _entry("https://b/2")],
        },
        _stub_judge({
            "https://a/1": _judgment("include", "from-a"),
            "https://b/1": _judgment("reject"),
            "https://b/2": _judgment("include", "from-b"),
        }),
        known_urls=set(),
        existing_slugs={"hand-curated"},
    )
    data = {"top_7": ["hand-curated"], "resources": [{"id": "hand-curated", "section": "patterns"}]}

    scout.append_candidates(data, run.candidates, "2026-09-29")

    attribution = {r["id"]: r.get("source_id") for r in data["resources"]}
    assert attribution == {"hand-curated": None, "from-a": "src-a", "from-b": "src-b"}
    assert "source_id" not in data["resources"][0]
    assert data["top_7"] == ["hand-curated"]


def test_automerge_decision_reads_candidates_file_and_section_ids(tmp_path) -> None:
    resources = tmp_path / "resources.yaml"
    resources.write_text("sections:\n  - id: patterns\ntop_7: []\nresources: []\n")
    candidates = tmp_path / "candidates.yaml"
    candidates.write_text(
        "candidates:\n"
        "  - {slug: a, section: patterns, type: article, url: 'https://x/a'}\n"
        "  - {slug: b, section: nope, type: article, url: 'https://x/b'}\n"
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


def test_sanitize_marks_a_cut_with_an_ellipsis_within_max_len() -> None:
    assert scout.sanitize_text("x" * 600) == "x" * 499 + "…"
    assert scout.sanitize_text("abcdef", max_len=3) == "ab…"


def test_sanitize_keeps_text_of_exactly_max_len_uncut() -> None:
    assert scout.sanitize_text("x" * 500) == "x" * 500
    assert scout.sanitize_text("abc", max_len=3) == "abc"


def test_sanitize_leaves_no_space_before_the_ellipsis() -> None:
    assert scout.sanitize_text("ab cdef", max_len=4) == "ab…"


def test_sanitize_truncates_before_escaping_so_no_escape_is_split() -> None:
    assert scout.sanitize_text("ab[cd", max_len=4) == "ab\\[…"


def test_sanitize_escapes_backslashes_so_pre_escaped_links_stay_dead() -> None:
    out = scout.sanitize_text("\\[x\\](https://evil) \\<b>")
    # Each backslash is doubled and each [ < still carries its own escape.
    assert out == "\\\\\\[x\\\\\\](https://evil) \\\\\\<b\\>"


@pytest.mark.parametrize(
    "raw", ["&#64;octocat", "&#35;12", "Fixes &#35;12", "&commat;x", "&num;12", "Fixes &num;12"]
)
def test_sanitized_entities_cannot_decode_into_mentions_or_references(raw: str) -> None:
    # GitHub decodes HTML entities in PR bodies, so a named entity such as
    # `&commat;x` or `&num;12` would render as `@x` or `#12` after defuse_references
    # had already looked for a literal `@` or `#`.
    out = scout.defuse_references(scout.sanitize_text(raw))
    # `\&` is a CommonMark backslash escape: it renders as a literal `&`, so the
    # entity text shows as typed instead of decoding.
    assert out.replace("\u200b", "") == raw.replace("&", "\\&")
    assert re.search(r"(?<!\\)&", out) is None


def test_sanitize_escapes_ampersands_readably() -> None:
    assert scout.sanitize_text("AT&T & friends") == "AT\\&T \\& friends"


def test_sanitize_truncates_before_escaping_an_ampersand() -> None:
    assert scout.sanitize_text("ab&cd", max_len=4) == "ab\\&…"


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
        {"src-a": [_entry("https://a/1?x=%5B1%5D&y=*")]},
        _stub_judge({"https://a/1?x=%5B1%5D&y=*": judgment}),
        known_urls=set(),
        existing_slugs=set(),
    )

    [c] = run.candidates
    assert c["url"] == "https://a/1?x=%5B1%5D&y=*"
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


# --- Feed URL safety ---

INJECTED_URL = "https://good.example/post) — **Editor pick:** [Download](https://evil.example/pay"


def test_safe_url_accepts_ordinary_http_and_https_links() -> None:
    assert scout.is_safe_url("https://example.com/blog/post?x=1&y=%5B#frag")
    assert scout.is_safe_url("http://www.example.co.uk:8080/a/b")
    assert scout.is_safe_url("https://a/1")


def test_safe_url_rejects_markdown_link_injection() -> None:
    assert not scout.is_safe_url(INJECTED_URL)


def test_safe_url_rejects_non_http_schemes() -> None:
    for url in ("javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", "ftp://example.com/x", "//example.com/x"):
        assert not scout.is_safe_url(url), url


def test_safe_url_rejects_markdown_html_and_quote_characters() -> None:
    for ch in "()[]<>\"'`\\":
        assert not scout.is_safe_url(f"https://example.com/a{ch}b"), ch


def test_safe_url_rejects_whitespace_and_control_characters() -> None:
    for url in ("https://example.com/a b", "https://example.com/a\nb", "https://example.com/a\tb", "https://example.com/\x00"):
        assert not scout.is_safe_url(url), repr(url)


def test_safe_url_rejects_userinfo_and_missing_host() -> None:
    assert not scout.is_safe_url("https://good.example@evil.example/")
    assert not scout.is_safe_url("https:///path-only")
    assert not scout.is_safe_url("https://example.com:notaport/")


def test_safe_url_rejects_ip_literal_and_internal_hosts() -> None:
    for url in (
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1/",
        "http://2130706433/",
        "http://0x7f.1/",
        "http://[::1]/",
        "http://localhost:8000/",
        "http://api.localhost/",
        "http://printer.local/",
        "http://metadata.google.internal/",
        "http://LOCALHOST./",
        "http://router.home.arpa/",
        "http://nas.lan/",
        "http://lan/",
    ):
        assert not scout.is_safe_url(url), url


def test_safe_url_rejects_hosts_that_only_normalise_to_internal_ones() -> None:
    for url in (
        "https://%6c%6fcalhost/admin",
        "https://127%2E0%2E0%2E1/",
        "https://127。0。0。1/",
        "https://ｌｏｃａｌｈｏｓｔ/",
        "https://ⓛⓞⓒⓐⓛⓗⓞⓢⓣ/",
        "https://localhost。/",
    ):
        assert not scout.is_safe_url(url), url


def test_safe_url_rejects_empty_and_hyphen_only_labels() -> None:
    for url in ("https://-/", "https://a..b/", "https://-.example.com/"):
        assert not scout.is_safe_url(url), url


def test_safe_url_rejects_invisible_format_characters() -> None:
    for ch in ("​", "‎", "‮", "﻿"):
        assert not scout.is_safe_url(f"https://example.com/a{ch}b"), repr(ch)


def test_safe_url_accepts_internationalised_domains() -> None:
    assert scout.is_safe_url("https://bücher.example/katalog")
    assert scout.is_safe_url("https://xn--bcher-kva.example/katalog")


def test_safe_url_rejects_non_strings() -> None:
    assert not scout.is_safe_url(None)
    assert not scout.is_safe_url(["https://example.com/"])


def test_unsafe_feed_url_is_skipped_before_the_judge_without_blocking_the_bump(capsys) -> None:
    called: list[str] = []
    stub = _stub_judge({"https://a/2": _judgment("include", "two")})

    def judge(title, url, summary, source_id):
        called.append(url)
        return stub(title, url, summary, source_id)

    run = scout.judge_sources(
        {"src-a": [_entry(INJECTED_URL), _entry("javascript:alert(1)\n::error::x"), _entry("https://a/2")]},
        judge,
        known_urls=set(),
        existing_slugs=set(),
    )

    assert called == ["https://a/2"]
    assert run.errors == []
    assert run.evaluated == 1
    assert [c["url"] for c in run.candidates] == ["https://a/2"]
    assert run.rejected == []
    assert run.fully_judged == {"src-a"}
    out = capsys.readouterr().out
    assert "skip (unsafe url)" in out
    assert not any(line.startswith("::") for line in out.splitlines())


def test_gate_holds_a_candidate_with_an_unsafe_url() -> None:
    bad = {**_candidate(slug="bad"), "url": INJECTED_URL}
    ip = {**_candidate(slug="ip"), "url": "http://169.254.169.254/"}

    decision = scout.evaluate_scout_automerge([_candidate(slug="ok"), bad, ip], SECTIONS, TYPES)

    assert decision.auto_merge_ok is False
    assert decision.reasons == ["candidate `bad` has an unsafe url", "candidate `ip` has an unsafe url"]
    assert decision.labels == ["automated", "scout", "auto-merge-skipped"]


def test_gate_holds_a_candidate_with_no_url() -> None:
    no_url = {k: v for k, v in _candidate(slug="nourl").items() if k != "url"}

    assert scout.evaluate_scout_automerge([no_url], SECTIONS, TYPES).reasons == [
        "candidate `nourl` has an unsafe url"
    ]


def test_judge_error_is_logged_on_one_line(capsys) -> None:
    run = scout.judge_sources(
        {"src-a": [_entry("https://a/1", title="t\n::warning::w")]},
        _stub_judge({"https://a/1": RuntimeError("boom\n::add-mask::secret")}),
        known_urls=set(),
        existing_slugs=set(),
    )

    [err] = run.errors
    assert "\n" not in err and "\r" not in err
    assert "boom" in err


def test_safe_slug_increments_past_taken_suffixes() -> None:
    assert scout.safe_slug("a", set()) == "a"
    assert scout.safe_slug("a", {"a"}) == "a-2"
    assert scout.safe_slug("a", {"a", "a-2", "a-3"}) == "a-4"


def test_same_slug_three_times_in_one_run_gets_unique_ids() -> None:
    judge = _stub_judge({f"https://a/{i}": _judgment("include", "dup") for i in range(3)})
    run = scout.judge_sources(
        {"src-a": [_entry(f"https://a/{i}") for i in range(3)]},
        judge,
        known_urls=set(),
        existing_slugs={"dup-2"},
    )

    assert [c["slug"] for c in run.candidates] == ["dup", "dup-3", "dup-4"]


# --- Gate reasons and the Scout PR body carry model text, so must stay inert ---

HOSTILE_SECTION = "nope`\n\n## INJECTED heading\n[Download SDK](https://evil.example/pay)\n::warning::x"


def test_gate_reason_for_a_hostile_section_is_single_line_and_escaped() -> None:
    bad = {**_candidate(section=HOSTILE_SECTION, type_="po`d\ncast", slug="bad`\nslug"), "url": "https://x/bad"}

    reasons = scout.evaluate_scout_automerge([bad], SECTIONS, TYPES).reasons

    assert reasons == [
        "candidate `bad' slug` has unknown section "
        "`nope' ## INJECTED heading \\[Download SDK\\](https://evil.example/pay) ::warning::x`",
        "candidate `bad' slug` has unknown type `po'd cast`",
    ]


def _full_candidate(**overrides) -> dict:
    c = {
        "slug": "good", "source_id": "src-a", "url": "https://ok.example/p", "title": "Good",
        "author": "A", "section": "patterns", "type": "article", "license": None,
        "blurb": "b", "tags": [], "rationale": "r",
    }
    c.update(overrides)
    return c


def test_pr_body_lists_candidates_under_an_auto_merge_banner() -> None:
    body = scout.pr_body([_full_candidate()], {"auto_merge_ok": True, "reasons": [], "labels": []})

    assert body == (
        "> **Auto-merge enabled** — this PR will land once required checks pass.\n"
        "\n"
        "## Candidates (1)\n"
        "\n"
        "- [ ] **[Good](https://ok.example/p)** — `patterns` · `article`\n"
        "      Source: `src-a` | Proposed slug: `good`\n"
        "      Blurb: _b_\n"
        "      Rationale: _r_"
    )


def test_pr_body_with_no_candidates_is_bookkeeping() -> None:
    body = scout.pr_body([], {"auto_merge_ok": True, "reasons": [], "labels": []})

    assert body.startswith("> **Auto-merge enabled**")
    assert "_No new candidates this week." in body
    assert "## Candidates" not in body


def test_pr_body_withholds_unsafe_urls_and_neutralises_hostile_fields() -> None:
    bad = _full_candidate(slug="bad", url=INJECTED_URL, section=HOSTILE_SECTION, type="x`\ny")
    decision = scout.evaluate_scout_automerge([bad], SECTIONS, TYPES)._asdict()
    decision["reasons"].append("smuggled\n## heading\n::error::x")

    body = scout.pr_body([bad], decision)

    # No live link at all: the only `](` left are backslash-escaped model text.
    assert not re.search(r"(?<!\\)\]\(", body)
    assert "**Good (unsafe url withheld)** — " in body
    assert not any(line.startswith(("## INJECTED", "## heading", "::", "[")) for line in body.splitlines())
    banner, blank, *_ = body.splitlines()
    assert banner.startswith("> **Auto-merge skipped:** candidate `bad` has unknown section `nope' ## INJECTED")
    assert banner.endswith("; smuggled ## heading ::error::x.")
    assert blank == ""
    assert "`x' y`" in body


CLOSING_OR_MENTION = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b|#\d|@\w", re.IGNORECASE
)


def test_pr_body_defuses_closing_keywords_and_mentions_in_feed_and_judge_text() -> None:
    hostile = _full_candidate(
        title="Fixes #42 by @octocat",
        blurb="Closes jdg2896/agentic-engineering#7; RESOLVED https://github.com/o/r/issues/9",
        rationale="fixed #1, cc @team",
    )

    body = scout.pr_body([hostile], {"auto_merge_ok": True, "reasons": [], "labels": []})

    assert not CLOSING_OR_MENTION.search(body)
    # Still readable: only zero-width spaces were added.
    assert "Fixes #42 by @octocat" in body.replace("​", "")


def test_defuse_references_leaves_ordinary_text_alone() -> None:
    assert scout.defuse_references("Prefix caching for agents") == "Prefix caching for agents"


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
    run = scout.ScoutRun(candidates=[{"slug": "a"}], stopped_early="limit\n::error::[x](y)")

    scout.write_candidates(path, run)

    assert scout.load_candidates(path) == [{"slug": "a"}]
    assert scout.load_incomplete_reason(path) == "limit ::error::\\[x\\](y)"


def test_complete_run_writes_no_incomplete_key(tmp_path) -> None:
    path = tmp_path / "candidates.yaml"

    scout.write_candidates(path, scout.ScoutRun())

    assert "incomplete" not in path.read_text()
    assert scout.load_incomplete_reason(path) is None


# --- judge_sources: the per-run judge budget ---


def _counting_judge(calls: list[str], on_call=None):
    """Judge that rejects everything and records each call's url; `on_call` runs after each."""

    def judge(title, url, summary, source_id):
        calls.append(url)
        if on_call is not None:
            on_call()
        return _judgment("reject")

    return judge


BUDGET_ENTRIES = {
    "src-a": [_entry("https://a/1"), _entry("https://a/2")],
    "src-b": [_entry("https://b/1"), _entry("https://b/2")],
    "src-c": [_entry("https://c/1")],
}


def test_call_budget_stops_before_the_next_call_and_keeps_the_judgments_made() -> None:
    calls: list[str] = []

    run = scout.judge_sources(BUDGET_ENTRIES, _counting_judge(calls), set(), set(), max_calls=3)

    assert calls == ["https://a/1", "https://a/2", "https://b/1"]
    assert run.evaluated == 3
    assert [r["url"] for r in run.rejected] == calls
    assert run.fully_judged == {"src-a"}
    assert run.errors == []
    assert run.stopped_early == "Scout's call budget of 3 judge calls"
    assert run.usage_limit_hit is False


def _ticking_clock(start: float, per_call: float):
    """A fake clock reading `start` when judging begins, advanced `per_call` seconds by each judge call."""
    now = [start]

    def tick() -> None:
        now[0] += per_call

    return (lambda: now[0]), tick


def test_time_budget_stops_once_the_clock_passes_it_even_with_calls_to_spare() -> None:
    # Judging starts at t=5000 (feeds were fetched before), and each call takes 10 minutes.
    clock, tick = _ticking_clock(5000.0, 600.0)
    calls: list[str] = []

    run = scout.judge_sources(
        BUDGET_ENTRIES, _counting_judge(calls, tick), set(), set(),
        max_calls=100, max_seconds=1500, clock=clock,
    )

    # Calls start at 0, 10 and 20 minutes of judging; at 30 minutes no call may start.
    assert calls == ["https://a/1", "https://a/2", "https://b/1"]
    assert run.evaluated == 3
    assert run.fully_judged == {"src-a"}
    assert run.stopped_early == "Scout's time budget of 25 minutes of judging"
    assert run.usage_limit_hit is False


def test_whichever_budget_runs_out_first_stops_the_run() -> None:
    # Calls every 10 minutes: 2 calls run out before 60 minutes, 60 minutes before 10 calls.
    clock, tick = _ticking_clock(0.0, 600.0)
    calls: list[str] = []
    run = scout.judge_sources(
        BUDGET_ENTRIES, _counting_judge(calls, tick), set(), set(),
        max_calls=2, max_seconds=3600, clock=clock,
    )
    assert run.evaluated == 2
    assert run.stopped_early == "Scout's call budget of 2 judge calls"

    clock, tick = _ticking_clock(0.0, 600.0)
    calls = []
    run = scout.judge_sources(
        BUDGET_ENTRIES, _counting_judge(calls, tick), set(), set(),
        max_calls=10, max_seconds=1200, clock=clock,
    )
    assert run.evaluated == 2
    assert run.stopped_early == "Scout's time budget of 20 minutes of judging"


def test_entries_skipped_before_the_judge_do_not_use_up_the_call_budget() -> None:
    calls: list[str] = []
    entries = {
        "src-a": [
            _entry("https://a/known"),
            {"link": INJECTED_URL, "title": "t"},
            {"title": "no link"},
            _entry("https://a/1"),
        ],
        "src-b": [_entry("https://b/known"), _entry("https://b/1")],
    }

    run = scout.judge_sources(
        entries, _counting_judge(calls), {"https://a/known", "https://b/known"}, set(), max_calls=2
    )

    assert calls == ["https://a/1", "https://b/1"]
    assert run.fully_judged == {"src-a", "src-b"}
    assert run.stopped_early is None


def test_a_budget_that_is_never_reached_changes_nothing() -> None:
    calls: list[str] = []

    run = scout.judge_sources(BUDGET_ENTRIES, _counting_judge(calls), set(), set(), max_calls=5)

    assert run.evaluated == 5
    assert run.fully_judged == set(BUDGET_ENTRIES)
    assert run.stopped_early is None


def test_with_no_budget_every_entry_is_judged_and_the_clock_is_never_read() -> None:
    calls: list[str] = []

    def clock() -> float:
        raise AssertionError("no budget, so the clock must not be read")

    run = scout.judge_sources(BUDGET_ENTRIES, _counting_judge(calls), set(), set(), clock=clock)

    assert run.evaluated == 5
    assert run.fully_judged == set(BUDGET_ENTRIES)
    assert run.stopped_early is None


def test_limit_still_stops_without_an_incomplete_reason_even_under_a_budget() -> None:
    calls: list[str] = []

    run = scout.judge_sources(BUDGET_ENTRIES, _counting_judge(calls), set(), set(), limit=2, max_calls=2)

    assert run.evaluated == 2
    assert run.fully_judged == {"src-a"}
    assert run.stopped_early is None


# --- main(): the quota path end to end ---


def _feed_entry(url: str) -> dict:
    return scout.feedparser.FeedParserDict(
        link=url, title=url, summary="s", published_parsed=(2026, 9, 1, 0, 0, 0, 0, 0, 0)
    )


def _scout_repo(tmp_path, monkeypatch, judgments: list) -> dict[str, Path]:
    """Point scout's data files at tmp copies, stub the feeds and script the judge's calls in order."""
    paths = {
        "sources": tmp_path / "sources.yaml",
        "seen": tmp_path / "seen.yaml",
        "resources": tmp_path / "resources.yaml",
        "candidates": tmp_path / "candidates.yaml",
    }
    paths["sources"].write_text(
        "sources:\n"
        "  - {id: src-a, url: 'https://a/feed', last_checked_at: 2026-08-01}\n"
        "  - {id: src-b, url: 'https://b/feed', last_checked_at: 2026-08-01}\n"
    )
    paths["seen"].write_text("seen: []\n")
    paths["resources"].write_text(
        "sections:\n  - {id: patterns, title: Patterns, description: d}\n"
        "resources:\n  - {id: old, url: 'https://old', type: article, blurb: b}\n"
    )
    for name, path in paths.items():
        monkeypatch.setattr(scout, f"{name.upper()}_PATH", path)
    feeds = {
        "https://a/feed": [_feed_entry("https://a/1"), _feed_entry("https://a/2")],
        "https://b/feed": [_feed_entry("https://b/1"), _feed_entry("https://b/2")],
    }
    monkeypatch.setattr(scout.feedparser, "parse", lambda url, agent=None: SimpleNamespace(entries=feeds[url]))
    queue = list(judgments)

    def judge_entry(system, title, url, summary, source_id):
        outcome = queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(scout.judge, "judge_entry", judge_entry)
    monkeypatch.setattr(sys, "argv", ["scout.py"])
    return paths


QUOTA = scout.judge.JudgeQuotaError("Claude Code CLI error: You've hit your session limit · resets 3pm (UTC)")


def test_main_keeps_partial_progress_when_the_usage_limit_is_hit(tmp_path, monkeypatch, capsys) -> None:
    paths = _scout_repo(
        tmp_path,
        monkeypatch,
        [_judgment("include", "one"), _judgment("reject"), _judgment("reject"), QUOTA],
    )

    scout.main()  # returns normally: exit 0

    candidates = yaml.safe_load(paths["candidates"].read_text())
    assert [c["url"] for c in candidates["candidates"]] == ["https://a/1"]
    assert "session limit" in candidates["incomplete"]
    assert [s["url"] for s in yaml.safe_load(paths["seen"].read_text())["seen"]] == ["https://a/2", "https://b/1"]
    bumped = {s["id"]: str(s["last_checked_at"]) for s in yaml.safe_load(paths["sources"].read_text())["sources"]}
    assert bumped["src-a"] != "2026-08-01"
    assert bumped["src-b"] == "2026-08-01"
    assert "::warning::" in capsys.readouterr().out


def test_main_keeps_partial_progress_when_the_call_budget_is_spent(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(scout, "JUDGE_CALL_BUDGET", 3)
    paths = _scout_repo(
        tmp_path,
        monkeypatch,
        [_judgment("include", "one"), _judgment("reject"), _judgment("reject"), _judgment("reject")],
    )

    scout.main()  # returns normally: exit 0

    candidates = yaml.safe_load(paths["candidates"].read_text())
    assert [c["url"] for c in candidates["candidates"]] == ["https://a/1"]
    assert candidates["incomplete"] == "Scout's call budget of 3 judge calls"
    assert [s["url"] for s in yaml.safe_load(paths["seen"].read_text())["seen"]] == ["https://a/2", "https://b/1"]
    stored = _stored_sources(paths)
    assert str(stored["src-a"]["last_checked_at"]) != "2026-08-01"
    assert str(stored["src-b"]["last_checked_at"]) == "2026-08-01"
    for source_id in ("src-a", "src-b"):
        assert stored[source_id]["consecutive_failures"] == 0
        assert str(stored[source_id]["newest_entry_at"]) == "2026-09-01"
    lines = capsys.readouterr().out.splitlines()
    assert [line for line in lines if line.startswith("::")] == [
        "::warning::Judging stopped on Scout's call budget of 3 judge calls. The 3 judgment(s) made are "
        "saved; unfinished Sources keep their last_checked_at, so the unjudged remainder is picked up next run."
    ]


def test_main_time_budget_stop_is_not_a_failure_even_with_nothing_judged(tmp_path, monkeypatch, capsys) -> None:
    # Unlike the usage limit, a spent budget says nothing about the quota, so it never fails the run.
    monkeypatch.setattr(scout, "JUDGE_TIME_BUDGET_SECONDS", 0)
    paths = _scout_repo(tmp_path, monkeypatch, [])

    scout.main()

    assert yaml.safe_load(paths["candidates"].read_text())["incomplete"] == (
        "Scout's time budget of 0 minutes of judging"
    )
    stored = _stored_sources(paths)
    assert {str(s["last_checked_at"]) for s in stored.values()} == {"2026-08-01"}
    assert all(s["consecutive_failures"] == 0 for s in stored.values())
    out = capsys.readouterr().out
    assert "::warning::Judging stopped on Scout's time budget of 0 minutes of judging. The 0 judgment(s)" in out


def test_main_fails_when_the_usage_limit_is_hit_before_any_judgment(tmp_path, monkeypatch, capsys) -> None:
    paths = _scout_repo(tmp_path, monkeypatch, [QUOTA])
    before = {name: path.read_text() for name, path in paths.items() if path.exists()}

    with pytest.raises(SystemExit) as exc_info:
        scout.main()

    assert exc_info.value.code == 1
    assert not paths["candidates"].exists()
    assert {name: path.read_text() for name, path in paths.items() if path.exists()} == before
    lines = capsys.readouterr().err.splitlines()
    assert [line for line in lines if line.startswith("::")] == [
        "::error::Nothing was judged: judging stopped on the Claude usage limit (Claude Code CLI error: "
        "You've hit your session limit · resets 3pm (UTC)); no files written. The subscription quota is "
        "shared with interactive use (docs/adr/0001-oauth-token-for-ci.md); re-run once it resets."
    ]


def test_a_long_usage_limit_message_is_trimmed_so_the_reason_keeps_its_closing_bracket(tmp_path) -> None:
    def judge(title, url, summary, source_id):
        raise scout.judge.JudgeQuotaError("You've hit your session limit " + "x" * 2000)

    run = scout.judge_sources(BUDGET_ENTRIES, judge, set(), set())
    path = tmp_path / "candidates.yaml"
    scout.write_candidates(path, run)

    reason = scout.load_incomplete_reason(path)
    assert reason.startswith("the Claude usage limit (You've hit your session limit xxx")
    assert reason.endswith("x)")


def test_a_call_budget_of_one_reads_in_the_singular() -> None:
    run = scout.judge_sources(BUDGET_ENTRIES, _counting_judge([]), set(), set(), max_calls=1)

    assert run.stopped_early == "Scout's call budget of 1 judge call"


# --- Usage-limit stop meets the hardening ---


def test_pr_body_adds_the_partial_run_note_after_the_banner() -> None:
    body = scout.pr_body(
        [], {"auto_merge_ok": True, "reasons": [], "labels": []}, "the Claude usage limit (hit the\nlimit)"
    )

    banner, blank, note, advice, *_ = body.splitlines()
    assert banner.startswith("> **Auto-merge enabled**")
    assert blank == ""
    assert note == (
        "> **Partial run:** judging stopped on the Claude usage limit (hit the limit),"
        " so some entries were left for the next run."
    )
    assert advice.startswith("> Merge this PR before the next scheduled run")
    assert "_No new candidates this week." in body


def _call_budget_stop() -> scout.ScoutRun:
    return scout.judge_sources(BUDGET_ENTRIES, _counting_judge([]), set(), set(), max_calls=1)


def _time_budget_stop() -> scout.ScoutRun:
    clock, tick = _ticking_clock(0.0, 3600.0)
    return scout.judge_sources(
        BUDGET_ENTRIES, _counting_judge([], tick), set(), set(), max_seconds=3600, clock=clock
    )


def _usage_limit_stop() -> scout.ScoutRun:
    def judge(title, url, summary, source_id):
        raise HOSTILE_QUOTA

    return scout.judge_sources(BUDGET_ENTRIES, judge, set(), set())


@pytest.mark.parametrize(
    ("stopped_run", "expected"),
    [
        (_call_budget_stop, "judging stopped on Scout's call budget of 1 judge call,"),
        (_time_budget_stop, "judging stopped on Scout's time budget of 60 minutes of judging,"),
        (_usage_limit_stop, "judging stopped on the Claude usage limit (You've hit your session limit ::error::"),
    ],
    ids=["call-budget", "time-budget", "usage-limit"],
)
def test_pr_body_partial_run_note_names_what_stopped_the_run(tmp_path, stopped_run, expected) -> None:
    run = stopped_run()
    path = tmp_path / "candidates.yaml"
    scout.write_candidates(path, run)

    body = scout.pr_body([], {"auto_merge_ok": True, "reasons": [], "labels": []}, scout.load_incomplete_reason(path))

    note = next(line for line in body.splitlines() if line.startswith("> **Partial run:**"))
    assert note.startswith(f"> **Partial run:** {expected}")
    assert note.endswith("so some entries were left for the next run.")
    assert not re.search(r"(?<!\\)\]\(", note)
    assert "\n::" not in body
    assert "> Merge this PR before the next scheduled run" in body


def test_pr_body_has_no_partial_run_note_for_a_complete_run() -> None:
    assert "Partial run" not in scout.pr_body([], {"auto_merge_ok": True, "reasons": [], "labels": []})


def test_quota_stop_still_skips_unsafe_urls_before_judging() -> None:
    calls: list[str] = []

    def judge(title, url, summary, source_id):
        calls.append(url)
        raise QUOTA

    entries = {"src-a": [{"link": INJECTED_URL, "title": "t"}, {"link": "https://ok.example/p", "title": "t"}]}
    run = scout.judge_sources(entries, judge, set(), set())

    assert calls == ["https://ok.example/p"]
    assert run.stopped_early is not None
    assert run.errors == []


HOSTILE_QUOTA = scout.judge.JudgeQuotaError("You've hit your session limit\n::error::injected\r\n[x](https://evil)")


def test_main_quota_warning_and_incomplete_reason_are_single_line(tmp_path, monkeypatch, capsys) -> None:
    paths = _scout_repo(tmp_path, monkeypatch, [_judgment("reject"), HOSTILE_QUOTA])

    scout.main()

    lines = capsys.readouterr().out.splitlines()
    assert not any(line.startswith("::error::") for line in lines)
    assert [line for line in lines if line.startswith("::")] == [
        next(line for line in lines if line.startswith("::warning::Judging stopped"))
    ]
    reason = yaml.safe_load(paths["candidates"].read_text())["incomplete"]
    assert "\n" not in reason and "\r" not in reason
    assert not re.search(r"(?<!\\)\]\(", reason)


def test_main_quota_error_with_no_progress_is_single_line(tmp_path, monkeypatch, capsys) -> None:
    _scout_repo(tmp_path, monkeypatch, [HOSTILE_QUOTA])

    with pytest.raises(SystemExit):
        scout.main()

    lines = capsys.readouterr().err.splitlines()
    assert [line for line in lines if line.startswith("::")] == [
        next(line for line in lines if line.startswith("::error::Nothing was judged: judging stopped on the Claude usage limit"))
    ]


# --- read_source: one Source's parsed feed -> its new entries ---


def _dated_entry(url: str, published=None, updated=None) -> dict:
    """A feedparser entry stand-in; dates are (y, m, d), padded like feedparser's struct_time."""
    fields = {"link": url, "title": url, "summary": "s"}
    if published is not None:
        fields["published_parsed"] = (*published, 0, 0, 0, 0, 0, 0)
    if updated is not None:
        fields["updated_parsed"] = (*updated, 0, 0, 0, 0, 0, 0)
    return scout.feedparser.FeedParserDict(fields)


def _read(entries: list, last_checked_at: str = "2026-08-01") -> list[str]:
    source = {"id": "src-a", "url": "https://a/feed", "last_checked_at": last_checked_at}
    read = scout.read_source(source, SimpleNamespace(entries=entries), scout.date(2026, 9, 29))
    return [e["link"] for e in read.new_entries]


def test_read_source_returns_published_entries_newer_than_last_checked_at() -> None:
    assert _read([_dated_entry("https://a/new", published=(2026, 9, 1))]) == ["https://a/new"]


def test_read_source_drops_entries_on_or_before_last_checked_at() -> None:
    entries = [
        _dated_entry("https://a/same-day", published=(2026, 8, 1)),
        _dated_entry("https://a/older", published=(2026, 7, 1)),
    ]
    assert _read(entries) == []


def test_read_source_dates_a_release_entry_by_updated_when_it_has_no_published() -> None:
    # GitHub release Atom feeds carry only <updated>.
    entries = [
        _dated_entry("https://a/releases/v2", updated=(2026, 9, 1)),
        _dated_entry("https://a/releases/v1", updated=(2026, 7, 1)),
    ]
    assert _read(entries) == ["https://a/releases/v2"]


def test_read_source_prefers_published_even_when_updated_is_later() -> None:
    # An old post edited after last_checked_at is not new.
    entries = [_dated_entry("https://a/edited", published=(2026, 7, 1), updated=(2026, 9, 1))]
    assert _read(entries) == []


def test_read_source_skips_entries_with_no_date() -> None:
    entries = [_dated_entry("https://a/undated"), _dated_entry("https://a/dated", updated=(2026, 9, 1))]
    assert _read(entries) == ["https://a/dated"]


# --- read_source: Source health ---

TODAY = scout.date(2026, 9, 29)


def _health(parsed_feed, **prior) -> scout.SourceHealth:
    source = {"id": "src-a", "url": "https://a/feed", "last_checked_at": "2026-08-01", **prior}
    return scout.read_source(source, parsed_feed, TODAY).health


def _parsed(entries=(), status=200, bozo=False) -> SimpleNamespace:
    """A feedparser result stand-in with the fields Source health reads."""
    return SimpleNamespace(entries=list(entries), status=status, bozo=bozo)


def test_read_source_reports_a_healthy_feed() -> None:
    feed = _parsed([_dated_entry("https://a/1", published=(2026, 9, 1))])

    assert _health(feed) == scout.SourceHealth(
        consecutive_failures=0,
        failing_since=None,
        last_http_status=200,
        newest_entry_at=scout.date(2026, 9, 1),
    )


def test_read_source_counts_an_http_error_as_a_failed_fetch_with_no_new_entries() -> None:
    feed = _parsed([_dated_entry("https://a/1", published=(2026, 9, 1))], status=404)
    source = {"id": "src-a", "url": "https://a/feed", "last_checked_at": "2026-08-01"}

    read = scout.read_source(source, feed, TODAY)

    assert read.new_entries == []
    assert read.health == scout.SourceHealth(
        consecutive_failures=1, failing_since=TODAY, last_http_status=404, newest_entry_at=None
    )


def test_read_source_counts_a_malformed_feed_with_no_entries_as_failed() -> None:
    # feedparser does not raise on e.g. an HTML error page served with 200: it sets bozo.
    health = _health(_parsed([], status=200, bozo=True))

    assert health == scout.SourceHealth(
        consecutive_failures=1, failing_since=TODAY, last_http_status=200, newest_entry_at=None
    )


def test_read_source_counts_a_malformed_feed_that_still_has_entries_as_healthy() -> None:
    feed = _parsed([_dated_entry("https://a/1", published=(2026, 9, 1))], bozo=True)

    assert _health(feed).consecutive_failures == 0


def test_read_source_counts_a_fetch_that_raised_as_failed_with_no_http_status() -> None:
    source = {"id": "src-a", "url": "https://a/feed", "last_checked_at": "2026-08-01"}

    read = scout.read_source(source, OSError("connection reset"), TODAY)

    assert read.new_entries == []
    assert read.health == scout.SourceHealth(
        consecutive_failures=1, failing_since=TODAY, last_http_status=None, newest_entry_at=None
    )


def test_read_source_extends_a_failure_streak_keeping_its_start_date_and_newest_entry() -> None:
    health = _health(
        _parsed([], status=410),
        consecutive_failures=2,
        failing_since=scout.date(2026, 9, 15),
        last_http_status=404,
        newest_entry_at=scout.date(2026, 3, 2),
    )

    assert health == scout.SourceHealth(
        consecutive_failures=3,
        failing_since=scout.date(2026, 9, 15),
        last_http_status=410,
        newest_entry_at=scout.date(2026, 3, 2),
    )


def test_read_source_resets_the_failure_streak_on_the_first_successful_fetch() -> None:
    health = _health(
        _parsed([_dated_entry("https://a/1", published=(2026, 9, 1))]),
        consecutive_failures=5,
        failing_since="2026-08-25",
        last_http_status=503,
    )

    assert (health.consecutive_failures, health.failing_since, health.last_http_status) == (0, None, 200)


def test_read_source_newest_entry_date_counts_old_entries_and_updated_only_entries() -> None:
    # Entries at or before last_checked_at are not new, but still show the feed is alive.
    entries = [
        _dated_entry("https://a/old", published=(2026, 5, 1)),
        _dated_entry("https://a/release", updated=(2026, 6, 10)),
        _dated_entry("https://a/undated"),
    ]

    assert _health(_parsed(entries)).newest_entry_at == scout.date(2026, 6, 10)


def test_read_source_keeps_the_stored_newest_entry_date_when_the_feed_shows_nothing_newer() -> None:
    health = _health(_parsed([_dated_entry("https://a/undated")]), newest_entry_at="2026-04-01")

    assert health.newest_entry_at == scout.date(2026, 4, 1)


def test_read_source_newest_entry_date_ignores_future_dated_entries() -> None:
    # A scheduled post or a typo like 2099 would otherwise pin the date forever.
    entries = [
        _dated_entry("https://a/typo", published=(2099, 1, 1)),
        _dated_entry("https://a/tomorrow", published=(2026, 9, 30)),
        _dated_entry("https://a/today", published=(2026, 9, 29)),
    ]

    assert _health(_parsed(entries)).newest_entry_at == TODAY


# --- record_health: Source health onto a sources.yaml entry ---

SOURCES_YAML = """\
sources:
  # ── Author / blog feeds ──
  - id: src-a
    type: rss
    url: https://a/feed
    last_checked_at: 2026-09-22
    enabled: true
    notes: null
"""


def test_record_health_writes_fields_beside_last_checked_at_and_keeps_comments() -> None:
    ryaml = scout.round_trip_yaml()
    data = ryaml.load(SOURCES_YAML)
    health = scout.SourceHealth(3, scout.date(2026, 9, 15), 404, scout.date(2026, 3, 2))

    scout.record_health(data["sources"][0], health)
    out = io.StringIO()
    ryaml.dump(data, out)

    assert out.getvalue() == (
        "sources:\n"
        "  # ── Author / blog feeds ──\n"
        "  - id: src-a\n"
        "    type: rss\n"
        "    url: https://a/feed\n"
        "    last_checked_at: 2026-09-22\n"
        "    consecutive_failures: 3\n"
        "    failing_since: 2026-09-15\n"
        "    last_http_status: 404\n"
        "    newest_entry_at: 2026-03-02\n"
        "    enabled: true\n"
        "    notes: null\n"
    )


def test_record_health_overwrites_previous_health_in_place() -> None:
    ryaml = scout.round_trip_yaml()
    data = ryaml.load(SOURCES_YAML)
    scout.record_health(data["sources"][0], scout.SourceHealth(3, scout.date(2026, 9, 15), 404, None))

    scout.record_health(data["sources"][0], scout.SourceHealth(0, None, 200, scout.date(2026, 9, 28)))
    out = io.StringIO()
    ryaml.dump(data, out)

    assert "    consecutive_failures: 0\n    failing_since: null\n    last_http_status: 200\n" in out.getvalue()
    assert "    newest_entry_at: 2026-09-28\n    enabled: true\n" in out.getvalue()


# --- read_source: the feed's own title and site link ---


def _meta(parsed_feed, url="https://a.example/feed", **prior) -> scout.FeedMeta | None:
    source = {"id": "src-a", "url": url, "last_checked_at": "2026-08-01", **prior}
    return scout.read_source(source, parsed_feed, TODAY).meta


def _titled(title=None, link=None, status=200) -> SimpleNamespace:
    """A successfully parsed feed whose channel has the given title and link."""
    channel = {k: v for k, v in (("title", title), ("link", link)) if v is not None}
    feed = _parsed([_dated_entry("https://a/1", published=(2026, 9, 1))], status=status)
    feed.feed = scout.feedparser.FeedParserDict(channel)
    return feed


def test_read_source_records_the_feed_title_and_site_link() -> None:
    meta = _meta(_titled("Hamel's Blog", "https://hamel.dev/"), url="https://hamel.dev/index.xml")

    assert meta == scout.FeedMeta(feed_title="Hamel's Blog", site_url="https://hamel.dev/")


def test_read_source_keeps_a_plain_http_site_link() -> None:
    assert _meta(_titled("t", "http://a.example/")).site_url == "http://a.example/"


@pytest.mark.parametrize(
    ("feed_url", "link"),
    [
        ("https://simonwillison.net/tags/coding-agents.atom", "https://simonwillison.net/tags/coding-agents/"),
        ("https://www.langchain.com/blog/rss.xml", "https://langchain.com/blog"),
        ("https://langchain.com/blog/rss.xml", "https://www.langchain.com/"),
        ("https://blog.cloudflare.com/tag/ai-agents/rss", "https://cloudflare.com/"),
        ("https://x.com/feed", "https://blog.x.com/"),
        ("https://github.com/langchain-ai/langgraph/releases.atom", "https://github.com/langchain-ai/langgraph/releases"),
        ("https://medium.com/feed/@alice", "https://medium.com/@alice?source=rss"),
    ],
)
def test_read_source_keeps_a_site_link_on_the_sources_own_site(feed_url, link) -> None:
    assert _meta(_titled("Blog", link), url=feed_url).site_url == link


@pytest.mark.parametrize(
    ("feed_url", "link"),
    [
        ("https://hamel.dev/index.xml", "https://hamel-dev.example/"),  # a foreign host
        ("https://blog.x.com/feed", "https://evil.x.com/"),  # a sibling, not the site itself
        ("https://github.com/langchain-ai/langgraph/releases.atom", "https://github.com/evil/langgraph"),
        ("https://github.com/langchain-ai/langgraph/releases.atom", "https://github.com/"),
        ("https://medium.com/feed/@alice", "https://medium.com/@mallory"),
        ("https://alice.github.io/feed.xml", "https://mallory.github.io/"),
        ("https://alice.github.io/feed.xml", "https://github.io/"),
        ("https://alice.substack.com/feed", "https://substack.com/"),
        ("https://huggingface.co/blog/feed.xml", "https://evil.huggingface.co/"),
    ],
)
def test_read_source_leaves_a_site_link_off_the_sources_own_site_unset(feed_url, link) -> None:
    assert _meta(_titled("Blog", link), url=feed_url) == scout.FeedMeta(feed_title="Blog", site_url=None)


def test_read_source_records_nothing_for_a_feed_with_no_title_or_link() -> None:
    assert _meta(_titled()) == scout.FeedMeta(feed_title=None, site_url=None)
    assert _meta(_titled("  \n ", "")) == scout.FeedMeta(feed_title=None, site_url=None)
    # A result with no channel at all.
    assert _meta(_parsed([_dated_entry("https://a/1", published=(2026, 9, 1))])) == scout.FeedMeta(None, None)


def test_read_source_sanitizes_a_hostile_feed_title_to_one_capped_line() -> None:
    hostile = "Blog\n::error::pwned\r\n[x](https://evil.example) <script>`x`</script> &#64;me"

    title = _meta(_titled(hostile, "https://a.example/")).feed_title

    assert "\n" not in title and "\r" not in title
    assert title == (
        "Blog ::error::pwned \\[x\\](https://evil.example) \\<script\\>\\`x\\`\\</script\\> \\&#64;me"
    )
    capped = _meta(_titled("x" * 1000)).feed_title
    assert len(capped) == scout.FEED_TITLE_MAX_LEN and capped.endswith("…")
    assert _meta(_titled("x" * scout.FEED_TITLE_MAX_LEN)).feed_title == "x" * scout.FEED_TITLE_MAX_LEN


@pytest.mark.parametrize(
    "link",
    [
        "javascript:alert(1)",
        "http://localhost/",
        "https://127.0.0.1/",
        "/",
        "https://a.example/)[x](https://evil",
        "https://user@a.example/",
    ],
)
def test_read_source_leaves_an_unsafe_site_link_unset(link) -> None:
    assert _meta(_titled("Blog", link)) == scout.FeedMeta(feed_title="Blog", site_url=None)


@pytest.mark.parametrize("failure", ["raised", "http-404", "malformed-empty"])
def test_read_source_records_no_feed_meta_when_the_read_failed(failure) -> None:
    if failure == "raised":
        parsed_feed = OSError("connection reset")
    elif failure == "http-404":
        parsed_feed = _titled("New name", "https://new.example/", status=404)
    else:
        parsed_feed = _parsed([], bozo=True)
        parsed_feed.feed = scout.feedparser.FeedParserDict(title="New name")

    assert _meta(parsed_feed, feed_title="Old name", site_url="https://old.example/") is None


# --- record_feed_meta: feed title and site link onto a sources.yaml entry ---


def test_record_feed_meta_writes_fields_after_the_health_facts() -> None:
    ryaml = scout.round_trip_yaml()
    data = ryaml.load(SOURCES_YAML)
    source = data["sources"][0]
    scout.record_health(source, scout.SourceHealth(0, None, 200, scout.date(2026, 9, 28)))

    scout.record_feed_meta(source, scout.FeedMeta("A Blog", "https://a.example/"))
    out = io.StringIO()
    ryaml.dump(data, out)

    assert out.getvalue() == (
        "sources:\n"
        "  # ── Author / blog feeds ──\n"
        "  - id: src-a\n"
        "    type: rss\n"
        "    url: https://a/feed\n"
        "    last_checked_at: 2026-09-22\n"
        "    consecutive_failures: 0\n"
        "    failing_since: null\n"
        "    last_http_status: 200\n"
        "    newest_entry_at: 2026-09-28\n"
        "    feed_title: A Blog\n"
        "    site_url: https://a.example/\n"
        "    enabled: true\n"
        "    notes: null\n"
    )


def test_record_feed_meta_overwrites_previous_values_in_place() -> None:
    ryaml = scout.round_trip_yaml()
    data = ryaml.load(SOURCES_YAML)
    source = data["sources"][0]
    scout.record_health(source, scout.SourceHealth(0, None, 200, None))
    scout.record_feed_meta(source, scout.FeedMeta("Old", "https://old.example/"))

    scout.record_feed_meta(source, scout.FeedMeta("New", None))
    out = io.StringIO()
    ryaml.dump(data, out)

    assert "    newest_entry_at: null\n    feed_title: New\n    site_url: null\n    enabled: true\n" in out.getvalue()


# --- main(): Source health on every run ---


def _stored_sources(paths) -> dict[str, dict]:
    return {s["id"]: s for s in yaml.safe_load(paths["sources"].read_text())["sources"]}


def test_main_records_health_even_for_a_source_whose_judging_stopped_early(tmp_path, monkeypatch) -> None:
    paths = _scout_repo(tmp_path, monkeypatch, [_judgment("reject"), _judgment("reject"), QUOTA])

    scout.main()

    stored = _stored_sources(paths)
    assert str(stored["src-b"]["last_checked_at"]) == "2026-08-01"  # not fully judged, not bumped
    for source_id in ("src-a", "src-b"):
        assert stored[source_id]["consecutive_failures"] == 0
        assert stored[source_id]["failing_since"] is None
        assert str(stored[source_id]["newest_entry_at"]) == "2026-09-01"


def test_main_records_failed_fetches_and_does_not_bump_or_judge_those_sources(tmp_path, monkeypatch) -> None:
    paths = _scout_repo(tmp_path, monkeypatch, [_judgment("reject"), _judgment("reject")])
    paths["sources"].write_text(
        "sources:\n"
        "  - {id: src-a, url: 'https://a/feed', last_checked_at: 2026-08-01, enabled: true}\n"
        "  - {id: gone, url: 'https://gone/feed', last_checked_at: 2026-08-01, enabled: true,"
        " consecutive_failures: 1, failing_since: 2026-09-22}\n"
        "  - {id: down, url: 'https://down/feed', last_checked_at: 2026-08-01, enabled: true}\n"
    )
    entries = [_feed_entry("https://a/1"), _feed_entry("https://a/2")]

    def parse(url, agent=None):
        if url == "https://down/feed":
            raise OSError("connection reset")
        if url == "https://gone/feed":
            return SimpleNamespace(entries=[_feed_entry("https://gone/1")], status=404, bozo=False)
        return SimpleNamespace(entries=entries, status=200, bozo=False)

    monkeypatch.setattr(scout.feedparser, "parse", parse)

    scout.main()  # both src-a entries judged; nothing from gone or down reaches the judge

    stored = _stored_sources(paths)
    assert str(stored["src-a"]["last_checked_at"]) != "2026-08-01"
    assert stored["src-a"]["last_http_status"] == 200
    assert (
        stored["gone"]["consecutive_failures"],
        str(stored["gone"]["failing_since"]),
        stored["gone"]["last_http_status"],
        str(stored["gone"]["last_checked_at"]),
    ) == (2, "2026-09-22", 404, "2026-08-01")
    assert (stored["down"]["consecutive_failures"], stored["down"]["last_http_status"]) == (1, None)
    assert str(stored["down"]["last_checked_at"]) == "2026-08-01"
    assert all(s["enabled"] is True for s in stored.values())


def test_main_warns_about_every_failed_fetch_in_one_single_line_format(tmp_path, monkeypatch, capsys) -> None:
    paths = _scout_repo(tmp_path, monkeypatch, [])
    paths["sources"].write_text(
        "sources:\n"
        "  - {id: gone, url: 'https://gone/feed', last_checked_at: 2026-08-01, consecutive_failures: 2}\n"
        "  - {id: down, url: 'https://down/feed', last_checked_at: 2026-08-01}\n"
    )

    def parse(url, agent=None):
        if url == "https://down/feed":
            raise OSError("reset\n::error::injected")
        return SimpleNamespace(entries=[], status=404, bozo=False)

    monkeypatch.setattr(scout.feedparser, "parse", parse)

    scout.main()

    out = capsys.readouterr()
    lines = [line for line in (out.out + out.err).splitlines() if line.startswith("::")]
    assert lines == [
        "::warning::source gone: feed fetch failed (HTTP status 404); 3 failed run(s) in a row",
        "::warning::source down: feed fetch failed (reset ::error::injected); 1 failed run(s) in a row",
    ]


def test_main_records_feed_meta_on_success_and_keeps_it_on_a_failed_read(tmp_path, monkeypatch) -> None:
    paths = _scout_repo(tmp_path, monkeypatch, [_judgment("reject"), _judgment("reject")])
    paths["sources"].write_text(
        "sources:\n"
        "  - {id: src-a, url: 'https://a.example/feed', last_checked_at: 2026-08-01, enabled: true}\n"
        "  - {id: down, url: 'https://down/feed', last_checked_at: 2026-08-01, enabled: true,"
        " feed_title: Down Blog, site_url: 'https://down.example/'}\n"
        "  - {id: gone, url: 'https://gone/feed', last_checked_at: 2026-08-01, enabled: true,"
        " feed_title: Gone Blog, site_url: 'https://gone.example/'}\n"
        "  - {id: retired, url: 'https://retired/feed', last_checked_at: 2026-08-01, enabled: false,"
        " feed_title: Retired Blog, site_url: 'https://retired.example/'}\n"
    )

    def parse(url, agent=None):
        if url == "https://down/feed":
            raise OSError("connection reset")
        if url == "https://gone/feed":
            return _titled("Renamed", "https://renamed.example/", status=404)
        if url == "https://retired/feed":
            return _titled("Revived", "https://revived.example/")
        feed = SimpleNamespace(entries=[_feed_entry("https://a/1"), _feed_entry("https://a/2")], status=200, bozo=False)
        feed.feed = scout.feedparser.FeedParserDict(title="A\nBlog", link="https://a.example/")
        return feed

    monkeypatch.setattr(scout.feedparser, "parse", parse)

    scout.main()

    stored = _stored_sources(paths)
    assert (stored["src-a"]["feed_title"], stored["src-a"]["site_url"]) == ("A Blog", "https://a.example/")
    assert (stored["down"]["feed_title"], stored["down"]["site_url"]) == ("Down Blog", "https://down.example/")
    assert (stored["gone"]["feed_title"], stored["gone"]["site_url"]) == ("Gone Blog", "https://gone.example/")
    assert (stored["retired"]["feed_title"], stored["retired"]["site_url"]) == (
        "Retired Blog", "https://retired.example/"
    )


def _inclusion_criteria(prompt: str) -> str:
    return prompt.split("## Inclusion criteria", 1)[1]


def test_system_prompt_rejects_non_english_entries_as_an_inclusion_rule() -> None:
    criteria = _inclusion_criteria(scout.build_system_prompt([], [])).lower()

    assert "must be written in english" in criteria
    assert "non-english" in criteria
    assert "for that reason alone" in criteria


def test_system_prompt_says_author_and_source_standing_are_not_criteria() -> None:
    criteria = _inclusion_criteria(scout.build_system_prompt([], [])).lower()

    assert "author" in criteria
    assert "personal blog" in criteria
    assert "company blog" in criteria
    assert "vendor blog" in criteria
    assert "well known" in criteria
    assert "not criteria" in criteria
    assert "only the content" in criteria
