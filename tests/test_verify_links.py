"""Unit tests for scripts/verify_links.py helpers."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import verify_links  # noqa: E402


def test_soft_404_detects_claude_docs_title() -> None:
    html = "<html><head><title>Not Found - Claude API Docs</title></head><body>x</body></html>"
    assert verify_links._looks_like_soft_404(html)


def test_soft_404_detects_page_not_found_anywhere_in_title() -> None:
    html = "<html><head><title>Acme Docs · Page Not Found</title></head></html>"
    assert verify_links._looks_like_soft_404(html)


def test_soft_404_detects_404_prefix() -> None:
    html = "<title>404 — This page does not exist</title>"
    assert verify_links._looks_like_soft_404(html)


def test_soft_404_ignores_legitimate_pages() -> None:
    # Real page titles should not trip the heuristic.
    assert not verify_links._looks_like_soft_404(
        "<title>How we handle 404s in production - Acme Engineering</title>"
    )
    assert not verify_links._looks_like_soft_404("<title>Welcome to Acme</title>")


def test_soft_404_handles_missing_title() -> None:
    assert not verify_links._looks_like_soft_404("<html><body>no title here</body></html>")


def test_collect_targets_includes_resources_and_worth_following() -> None:
    data = {
        "resources": [
            {"id": "alpha", "url": "https://a.example/", "paywall": True},
            {"id": "beta", "url": "https://b.example/"},
        ],
        "worth_following": [
            {"name": "Cool Person", "url": "https://cool.example/", "blurb": "x"},
        ],
    }
    targets = verify_links.collect_targets(data)

    assert [t["id"] for t in targets] == ["alpha", "beta", "wf:cool-person"]
    assert [t["kind"] for t in targets] == ["resource", "resource", "worth_following"]
    assert targets[0]["paywall"] is True
    assert targets[1]["paywall"] is False
    assert targets[2]["paywall"] is False


def test_collect_targets_handles_missing_sections() -> None:
    assert verify_links.collect_targets({}) == []
    assert verify_links.collect_targets({"resources": None, "worth_following": None}) == []


def test_slugify_normalizes_punctuation_and_case() -> None:
    assert verify_links._slugify("Simon Willison") == "simon-willison"
    assert verify_links._slugify("Cloudflare AI agents tag") == "cloudflare-ai-agents-tag"
    assert verify_links._slugify("Hamel's blog") == "hamel-s-blog"
    assert verify_links._slugify("!!!") == "wf"
