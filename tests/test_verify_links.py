"""Unit tests for scripts/verify_links.py helpers."""

from __future__ import annotations

import sys
from datetime import date
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


# --- State machine -----------------------------------------------------------


def _yaml(*, resources=None, worth_following=None, top_7=None) -> dict:
    return {
        "resources": resources or [],
        "worth_following": worth_following or [],
        "top_7": top_7 or [],
    }


def _result(*, id, url, outcome, status_code=None, error=None, kind="resource", final_url=None) -> dict:
    return {
        "id": id, "url": url, "outcome": outcome,
        "status_code": status_code, "error": error,
        "kind": kind, "final_url": final_url,
    }


TODAY = date(2026, 5, 3)


def test_first_dead_sets_first_dead_at() -> None:
    data = _yaml(resources=[{"id": "a", "url": "https://a/"}])
    results = [_result(id="a", url="https://a/", outcome="dead", status_code=404)]
    [ch] = verify_links.compute_state_changes(data, results, TODAY)
    assert ch["set"] == {"first_dead_at": TODAY}
    assert ch["clear"] == []
    assert ch["transition"] == "first_dead"


def test_dead_within_grace_window_does_not_quarantine() -> None:
    data = _yaml(resources=[{"id": "a", "url": "https://a/", "first_dead_at": date(2026, 4, 20)}])  # 13 days ago
    results = [_result(id="a", url="https://a/", outcome="dead", status_code=404)]
    [ch] = verify_links.compute_state_changes(data, results, TODAY)
    assert ch["set"] == {}
    assert ch["clear"] == []
    assert ch["transition"] == "no_change"


def test_dead_at_grace_threshold_quarantines() -> None:
    data = _yaml(resources=[{"id": "a", "url": "https://a/", "first_dead_at": date(2026, 4, 12)}])  # 21 days ago
    results = [_result(id="a", url="https://a/", outcome="dead", status_code=404)]
    [ch] = verify_links.compute_state_changes(data, results, TODAY)
    assert ch["set"]["quarantined_at"] == TODAY
    assert ch["set"]["quarantine_reason"] == "404"
    assert ch["transition"] == "quarantine"


def test_dead_quarantines_with_error_string_when_no_status() -> None:
    data = _yaml(resources=[{"id": "a", "url": "https://a/", "first_dead_at": date(2026, 4, 12)}])
    results = [_result(id="a", url="https://a/", outcome="dead", error="timeout")]
    [ch] = verify_links.compute_state_changes(data, results, TODAY)
    assert ch["set"]["quarantine_reason"] == "timeout"


def test_dead_on_already_quarantined_is_noop() -> None:
    data = _yaml(resources=[{
        "id": "a", "url": "https://a/",
        "first_dead_at": date(2026, 4, 1),
        "quarantined_at": date(2026, 4, 22),
        "quarantine_reason": "404",
    }])
    results = [_result(id="a", url="https://a/", outcome="dead", status_code=404)]
    [ch] = verify_links.compute_state_changes(data, results, TODAY)
    assert ch["set"] == {}
    assert ch["clear"] == []
    assert ch["transition"] == "no_change"


def test_ok_on_quarantined_entry_is_recovery() -> None:
    data = _yaml(resources=[{
        "id": "a", "url": "https://a/",
        "first_dead_at": date(2026, 4, 1),
        "quarantined_at": date(2026, 4, 22),
        "quarantine_reason": "404",
    }])
    results = [_result(id="a", url="https://a/", outcome="ok", status_code=200)]
    [ch] = verify_links.compute_state_changes(data, results, TODAY)
    assert ch["set"] == {"verified_at": TODAY}
    assert set(ch["clear"]) == {"first_dead_at", "quarantined_at", "quarantine_reason"}
    assert ch["transition"] == "recovery"


def test_ok_with_no_prior_dead_is_plain_verified() -> None:
    data = _yaml(resources=[{"id": "a", "url": "https://a/"}])
    results = [_result(id="a", url="https://a/", outcome="ok", status_code=200)]
    [ch] = verify_links.compute_state_changes(data, results, TODAY)
    assert ch["set"] == {"verified_at": TODAY}
    assert ch["clear"] == []
    assert ch["transition"] == "verified"


def test_migrated_rewrites_url_stamps_verified_and_clears_first_dead_at() -> None:
    data = _yaml(resources=[{
        "id": "a", "url": "https://a/",
        "first_dead_at": date(2026, 4, 20),
        "verified_at": date(2026, 3, 1),
    }])
    results = [_result(
        id="a", url="https://a/", outcome="migrated", status_code=200,
        final_url="https://a-new/",
    )]
    [ch] = verify_links.compute_state_changes(data, results, TODAY)
    assert ch["set"] == {"url": "https://a-new/", "verified_at": TODAY}
    assert ch["clear"] == ["first_dead_at"]
    assert ch["transition"] == "migrated"


def test_migrated_without_final_url_still_stamps_verified() -> None:
    # Defensive: outcome=migrated implies final_url is set, but tolerate missing.
    data = _yaml(resources=[{"id": "a", "url": "https://a/"}])
    results = [_result(id="a", url="https://a/", outcome="migrated", status_code=200)]
    [ch] = verify_links.compute_state_changes(data, results, TODAY)
    assert ch["set"] == {"verified_at": TODAY}
    assert ch["clear"] == []
    assert ch["transition"] == "migrated"


def test_paywall_skipped_treated_like_ok() -> None:
    data = _yaml(resources=[{"id": "a", "url": "https://a/", "first_dead_at": date(2026, 4, 20)}])
    results = [_result(id="a", url="https://a/", outcome="paywall_skipped", status_code=403)]
    [ch] = verify_links.compute_state_changes(data, results, TODAY)
    assert ch["set"]["verified_at"] == TODAY
    assert "first_dead_at" in ch["clear"]


def test_worth_following_matched_by_url() -> None:
    data = _yaml(worth_following=[{"name": "X", "url": "https://x/", "blurb": "y"}])
    results = [_result(id="wf:x", url="https://x/", outcome="dead", status_code=404, kind="worth_following")]
    [ch] = verify_links.compute_state_changes(data, results, TODAY)
    assert ch["entry"]["name"] == "X"
    assert ch["set"] == {"first_dead_at": TODAY}
    assert ch["kind"] == "worth_following"


def test_top_7_flag_set_when_id_is_in_top_7() -> None:
    data = _yaml(
        resources=[{"id": "marquee", "url": "https://m/"}, {"id": "other", "url": "https://o/"}],
        top_7=["marquee"],
    )
    results = [
        _result(id="marquee", url="https://m/", outcome="dead", status_code=404),
        _result(id="other", url="https://o/", outcome="dead", status_code=404),
    ]
    changes = verify_links.compute_state_changes(data, results, TODAY)
    by_id = {ch["id"]: ch for ch in changes}
    assert by_id["marquee"]["top_7"] is True
    assert by_id["other"]["top_7"] is False


def test_apply_state_changes_writes_and_clears_fields() -> None:
    entry = {"id": "a", "url": "https://a/", "first_dead_at": date(2026, 4, 1)}
    changes = [{
        "entry": entry, "kind": "resource", "id": "a", "url": "https://a/",
        "top_7": False, "transition": "verified", "reason": None, "first_dead_at": None,
        "set": {"verified_at": TODAY}, "clear": ["first_dead_at"],
    }]
    verify_links.apply_state_changes(changes)
    assert entry["verified_at"] == TODAY
    assert "first_dead_at" not in entry


def test_report_transitions_classifies_events_with_top_7_flag() -> None:
    changes = [
        {"transition": "quarantine", "id": "a", "kind": "resource", "url": "https://a/",
         "reason": "404", "top_7": True, "first_dead_at": "2026-04-12",
         "set": {}, "clear": []},
        {"transition": "quarantine", "id": "b", "kind": "resource", "url": "https://b/",
         "reason": "timeout", "top_7": False, "first_dead_at": "2026-04-12",
         "set": {}, "clear": []},
        {"transition": "recovery", "id": "c", "kind": "resource", "url": "https://c/",
         "reason": None, "top_7": False, "first_dead_at": "2026-04-01",
         "set": {}, "clear": []},
        {"transition": "first_dead", "id": "d", "kind": "resource", "url": "https://d/",
         "reason": None, "top_7": False, "first_dead_at": None,
         "set": {"first_dead_at": TODAY}, "clear": []},
    ]
    t = verify_links.report_transitions(changes)
    assert len(t["newly_quarantined"]) == 2
    assert t["newly_quarantined"][0]["top_7"] is True
    assert t["newly_quarantined"][1]["top_7"] is False
    assert t["recovered"] == [{"id": "c", "kind": "resource", "url": "https://c/"}]
    assert len(t["accumulating_dead"]) == 1
    assert t["accumulating_dead"][0]["id"] == "d"


def test_compute_state_changes_skips_results_with_no_yaml_match() -> None:
    data = _yaml(resources=[{"id": "exists", "url": "https://e/"}])
    results = [_result(id="ghost", url="https://g/", outcome="dead", status_code=404)]
    assert verify_links.compute_state_changes(data, results, TODAY) == []
