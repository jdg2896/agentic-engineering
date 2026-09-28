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
