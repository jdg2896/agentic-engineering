"""Tests for scripts/source_review.py at its seams: review_sources and the auto-merge gate."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import source_review  # noqa: E402

TODAY = date(2026, 9, 29)


def _source(
    source_id: str = "s",
    *,
    failures: int | None = 0,
    failing_since: date | None = None,
    status: int | None = 200,
    newest: date | None = TODAY,
    **extra,
) -> dict:
    """A Source with Source health as Scout records it; failures=None means no health yet."""
    source = {
        "id": source_id,
        "type": "rss",
        "url": f"https://{source_id}.example/feed",
        "cadence": "weekly",
        "last_checked_at": TODAY,
    }
    if failures is not None:
        source |= {
            "consecutive_failures": failures,
            "failing_since": failing_since,
            "last_http_status": status,
            "newest_entry_at": newest,
        }
    source |= {"enabled": True, "notes": None}
    return source | extra


def _unused_fetch(url):
    raise AssertionError("this slice never fetches")


def _unused_judge(title, url, summary, source_id):
    raise AssertionError("this slice never judges")


def _review(sources, resources=None, today=TODAY, seen=None):
    return source_review.review_sources(
        sources, resources or [], seen or [], [], today, _unused_fetch, _unused_judge
    )


def _retired(plan) -> dict[str, str]:
    return {r.source_id: r.reason for r in plan.retirements}


# ── Dead-broken ──────────────────────────────────────────────────────────────


def test_retires_a_source_broken_for_exactly_4_weeks_and_4_runs() -> None:
    broken = _source("b", failures=4, failing_since=TODAY - timedelta(days=28), status=404)

    plan = _review([broken])

    assert _retired(plan) == {"b": "dead-broken"}


def test_keeps_a_source_broken_for_4_runs_but_one_day_short_of_4_weeks() -> None:
    broken = _source("b", failures=4, failing_since=TODAY - timedelta(days=27), status=404)

    assert _review([broken]).retirements == []


def test_keeps_a_source_broken_for_4_weeks_but_only_3_counted_runs() -> None:
    # Discarded or held Scout runs don't count, so the run count can lag the span.
    broken = _source("b", failures=3, failing_since=TODAY - timedelta(days=60), status=404)

    assert _review([broken]).retirements == []


def test_keeps_a_source_whose_failure_streak_has_reset() -> None:
    recovered = _source("b", failures=0, failing_since=None, newest=TODAY - timedelta(days=3))

    assert _review([recovered]).retirements == []


def test_dead_broken_evidence_carries_the_streak_span_and_run_count() -> None:
    since = TODAY - timedelta(days=35)
    broken = _source("b", failures=5, failing_since=since, status=None, newest=date(2026, 7, 1))

    (retirement,) = _review([broken]).retirements

    assert retirement.evidence == source_review.SourceHealth(
        consecutive_failures=5, failing_since=since, last_http_status=None,
        newest_entry_at=date(2026, 7, 1),
    )


# ── Dead-silent ──────────────────────────────────────────────────────────────


def test_retires_a_fetching_source_whose_newest_entry_is_older_than_6_months() -> None:
    silent = _source("q", newest=date(2026, 3, 28))  # 6 months + 1 day before 2026-09-29

    assert _retired(_review([silent])) == {"q": "dead-silent"}


def test_keeps_a_fetching_source_whose_newest_entry_is_exactly_6_months_old() -> None:
    quiet = _source("q", newest=date(2026, 3, 29))

    assert _review([quiet]).retirements == []


def test_six_months_is_calendar_months_clamped_to_month_end() -> None:
    # 6 months before 2026-08-31 is 2026-02-28 (no Feb 31st).
    assert _review([_source("q", newest=date(2026, 2, 28))], today=date(2026, 8, 31)).retirements == []
    assert _retired(_review([_source("q", newest=date(2026, 2, 27))], today=date(2026, 8, 31))) == {
        "q": "dead-silent"
    }


def test_a_failing_source_is_not_silent_until_it_is_dead_broken() -> None:
    # Old newest entry, but the feed is failing: a broken streak in progress, not silence.
    failing = _source(
        "q", failures=2, failing_since=TODAY - timedelta(days=14), status=500, newest=date(2025, 1, 1)
    )

    assert _review([failing]).retirements == []


def test_a_dead_broken_source_with_an_old_newest_entry_is_retired_once_as_broken() -> None:
    broken = _source(
        "b", failures=6, failing_since=TODAY - timedelta(days=42), status=404, newest=date(2025, 1, 1)
    )

    assert _retired(_review([broken])) == {"b": "dead-broken"}


# ── What is never retired ────────────────────────────────────────────────────


def test_never_retires_a_source_with_no_recorded_health() -> None:
    # Sources Scout has not yet run on since Source health shipped carry no health keys.
    assert _review([_source("n", failures=None)]).retirements == []


def test_a_revived_source_is_not_retired_until_scout_records_fresh_health() -> None:
    # Retirement drops the health keys; the owner revives by setting enabled: true.
    revived = _source(
        "v", failures=None, retired_at=date(2026, 6, 1), retired_reason="dead-broken"
    )

    assert _review([revived]).retirements == []


def test_never_retires_as_silent_when_no_dated_entry_was_ever_seen() -> None:
    assert _review([_source("n", newest=None)]).retirements == []


def test_already_retired_and_disabled_sources_are_not_re_retired() -> None:
    long_dead = {"failures": 9, "failing_since": date(2026, 1, 1), "status": 404}
    retired = _source(
        "r", **long_dead, enabled=False, retired_at=date(2026, 6, 1), retired_reason="dead-broken"
    )
    disabled = _source("d", **long_dead, enabled=False)
    silent_retired = _source("s", newest=date(2025, 1, 1), enabled=False)

    assert _review([retired, disabled, silent_retired]).retirements == []


def test_review_leaves_sources_and_resources_unmodified() -> None:
    broken = _source("b", failures=4, failing_since=TODAY - timedelta(days=28), status=404)
    resources = [{"id": "r1", "url": "https://b.example/post", "source_id": "b"}]
    sources_before = [dict(broken)]
    resources_before = [dict(r) for r in resources]

    plan = _review([broken], resources)

    assert _retired(plan) == {"b": "dead-broken"}
    assert [broken] == sources_before
    assert resources == resources_before


def test_plan_has_no_additions_rejections_or_incomplete_note_in_this_slice() -> None:
    plan = _review([_source("ok")])

    assert (plan.retirements, plan.additions, plan.rejected, plan.incomplete) == ([], [], [], None)
    assert plan.today == TODAY
    assert plan.is_empty


def test_a_plan_with_a_retirement_is_not_empty() -> None:
    broken = _source("b", failures=4, failing_since=TODAY - timedelta(days=28), status=404)

    assert not _review([broken]).is_empty


# ── Unproductive ─────────────────────────────────────────────────────────────

# Attribution shipped 2026-09-29, so the grace period of every Source that predates it
# ends 2026-12-29. LATER is well past that: its trailing 3-month window is
# (2026-10-15, 2027-01-15] — after the start, up to and including the review date.
LATER = date(2027, 1, 15)
IN_WINDOW = date(2026, 12, 1)


def _rejects(source_id: str, n: int, on: date = IN_WINDOW) -> list[dict]:
    """`n` seen.yaml rejects of `source_id`, stored as Scout writes them (ISO strings)."""
    return [
        {"url": f"https://{source_id}.example/r{on}-{i}", "title": "", "source_id": source_id,
         "rejected_at": str(on)}
        for i in range(n)
    ]


def _resource(source_id: str | None, added_at: date = IN_WINDOW, n: int = 0) -> dict:
    resource = {"id": f"res-{source_id}-{added_at}-{n}", "url": f"https://{source_id}.example/p{n}",
                "added_at": added_at}
    if source_id is not None:
        resource["source_id"] = source_id
    return resource


def _healthy(source_id: str = "u", **extra) -> dict:
    return _source(source_id, last_checked_at=LATER, newest=LATER - timedelta(days=2), **extra)


def test_retires_a_source_with_15_judged_and_no_yield_after_its_grace_period() -> None:
    plan = _review([_healthy("u")], seen=_rejects("u", 15), today=LATER)

    assert _retired(plan) == {"u": "unproductive"}


def test_unproductive_evidence_is_judged_count_yield_and_window() -> None:
    (retirement,) = _review([_healthy("u")], seen=_rejects("u", 17), today=LATER).retirements

    assert retirement.evidence == source_review.UnproductiveEvidence(
        judged=17, yield_count=0, window_start=date(2026, 10, 15), window_end=LATER
    )


def test_keeps_a_source_with_only_14_judged() -> None:
    assert _review([_healthy("u")], seen=_rejects("u", 14), today=LATER).retirements == []


def test_one_accepted_entry_keeps_a_source_with_15_judged() -> None:
    # 14 rejects + 1 attributed Resource = 15 judged, Yield 1.
    plan = _review([_healthy("u")], [_resource("u")], seen=_rejects("u", 14), today=LATER)

    assert plan.retirements == []


def test_window_excludes_its_start_day_and_includes_the_review_date() -> None:
    sources = [_healthy("u")]

    def judged_14_plus_one_on(day):
        return _review(sources, seen=_rejects("u", 14) + _rejects("u", 1, on=day), today=LATER)

    assert judged_14_plus_one_on(date(2026, 10, 15)).retirements == []  # 3 months before: out
    assert _retired(judged_14_plus_one_on(date(2026, 10, 16))) == {"u": "unproductive"}
    assert _retired(judged_14_plus_one_on(LATER)) == {"u": "unproductive"}
    assert judged_14_plus_one_on(LATER + timedelta(days=1)).retirements == []


def test_yield_outside_the_window_does_not_keep_a_source() -> None:
    old_hit = _resource("u", added_at=date(2026, 10, 15))

    plan = _review([_healthy("u")], [old_hit], seen=_rejects("u", 15), today=LATER)

    assert _retired(plan) == {"u": "unproductive"}


def test_only_the_sources_own_attributed_entries_count() -> None:
    others = _rejects("other", 20) + _rejects("u", 10)
    resources = [_resource(None), _resource("other")]  # hand-curated, and another Source's

    plan = _review([_healthy("u"), _healthy("other")], resources, seen=others, today=LATER)

    assert plan.retirements == []


def test_grace_period_ends_3_months_after_attribution_shipped() -> None:
    seen = _rejects("u", 15, on=date(2026, 12, 1))

    assert _review([_healthy("u")], seen=seen, today=date(2026, 12, 28)).retirements == []
    assert _retired(_review([_healthy("u")], seen=seen, today=date(2026, 12, 29))) == {
        "u": "unproductive"
    }


def test_grace_period_ends_3_months_after_a_later_added_at() -> None:
    added = _healthy("u", added_at="2026-11-10")  # as read back from YAML: string or date
    seen = _rejects("u", 15, on=date(2027, 1, 1))

    assert _review([added], seen=seen, today=date(2027, 2, 9)).retirements == []
    assert _retired(_review([added], seen=seen, today=date(2027, 2, 10))) == {"u": "unproductive"}


def test_an_added_at_before_attribution_shipped_does_not_shorten_the_grace_period() -> None:
    old = _healthy("u", added_at=date(2026, 1, 1))

    plan = _review([old], seen=_rejects("u", 15, on=date(2026, 12, 1)), today=date(2026, 12, 28))

    assert plan.retirements == []


def test_a_source_inside_its_grace_period_can_still_be_dead() -> None:
    new_and_broken = _source(
        "n", failures=4, failing_since=LATER - timedelta(days=28), status=404,
        added_at=LATER - timedelta(days=40),
    )

    plan = _review([new_and_broken], seen=_rejects("n", 30), today=LATER)

    assert _retired(plan) == {"n": "dead-broken"}


def test_a_dead_and_unproductive_source_is_retired_once_as_dead() -> None:
    both = _source("b", failures=4, failing_since=LATER - timedelta(days=28), status=404)

    plan = _review([both], seen=_rejects("b", 30), today=LATER)

    assert [(r.source_id, r.reason) for r in plan.retirements] == [("b", "dead-broken")]


def test_retired_and_disabled_sources_are_never_unproductive() -> None:
    retired = _healthy("r", enabled=False, retired_at=date(2026, 11, 1), retired_reason="dead-silent")
    disabled = _healthy("d", enabled=False)

    plan = _review([retired, disabled], seen=_rejects("r", 20) + _rejects("d", 20), today=LATER)

    assert plan.retirements == []


def test_a_source_with_no_health_yet_can_still_be_unproductive() -> None:
    plan = _review([_source("u", failures=None)], seen=_rejects("u", 15), today=LATER)

    assert _retired(plan) == {"u": "unproductive"}


def test_a_malformed_rejected_at_fails_closed() -> None:
    seen = _rejects("u", 15) + [{"url": "https://u.example/x", "source_id": "u", "rejected_at": "soon"}]

    with pytest.raises(ValueError):
        _review([_healthy("u")], seen=seen, today=LATER)


def test_an_attributed_resource_without_added_at_fails_closed() -> None:
    undated = {"id": "r", "url": "https://u.example/p", "source_id": "u"}

    with pytest.raises(ValueError):
        _review([_healthy("u")], [undated], seen=_rejects("u", 15), today=LATER)


def test_a_malformed_source_added_at_fails_closed() -> None:
    with pytest.raises(ValueError):
        _review([_healthy("u", added_at="last spring")], seen=_rejects("u", 15), today=LATER)


# ── Auto-merge gate (ADR 0002) ───────────────────────────────────────────────


def _plan_retiring(*source_ids: str):
    health = source_review.SourceHealth(4, TODAY - timedelta(days=28), 404, None)
    return source_review.ReviewPlan(
        today=TODAY,
        retirements=[source_review.Retirement(i, "dead-broken", health) for i in source_ids],
    )


def _enabled_sources(n: int) -> list[dict]:
    return [_source(f"s{i}") for i in range(n)]


def _gate(plan, sources, resources=None):
    return source_review.evaluate_source_review_automerge(plan, sources, resources or [])


def _plan_retiring_unproductive(*source_ids: str):
    # Evidence as review_sources would record it (Yield 0) — the gate must not trust it.
    evidence = source_review.UnproductiveEvidence(20, 0, date(2026, 10, 15), LATER)
    return source_review.ReviewPlan(
        today=LATER,
        retirements=[source_review.Retirement(i, "unproductive", evidence) for i in source_ids],
    )


def test_gate_passes_an_unproductive_retirement_with_no_yield_in_window() -> None:
    resources = [
        _resource("s0", added_at=date(2026, 10, 15)),  # the window's excluded start day
        _resource("s1"),  # another Source's Yield
        _resource(None),  # hand-curated
    ]

    decision = _gate(_plan_retiring_unproductive("s0"), _enabled_sources(20), resources)

    assert decision.auto_merge_ok is True


def test_gate_holds_an_unproductive_retirement_of_a_source_with_yield_in_window() -> None:
    # Logic-bug backstop: the gate recounts Yield from resources.yaml, not from the plan.
    hostile = "[x](https://evil)"
    resources = [_resource("s0", added_at=date(2026, 10, 16)), _resource(hostile)]
    sources = [*_enabled_sources(20), _source(hostile)]

    decision = _gate(_plan_retiring_unproductive("s0", hostile), sources, resources)

    assert decision.auto_merge_ok is False
    assert decision.reasons == [
        "Unproductive retirement of s0, which has a Yield of 1 in its window",
        "Unproductive retirement of \\[x\\](https://evil), which has a Yield of 1 in its window",
    ]


def test_gate_does_not_hold_a_dead_retirement_of_a_source_with_recent_yield() -> None:
    # A feed that broke last month may well have yielded before it broke: Dead ignores Yield.
    decision = _gate(_plan_retiring("s0"), _enabled_sources(20), [_resource("s0", added_at=TODAY)])

    assert decision.auto_merge_ok is True


def test_gate_passes_3_retirements() -> None:
    decision = _gate(
        _plan_retiring("s0", "s1", "s2"), _enabled_sources(20)
    )

    assert decision == (True, [], ["automated", "source-review"])


def test_gate_holds_4_retirements() -> None:
    decision = _gate(
        _plan_retiring("s0", "s1", "s2", "s3"), _enabled_sources(20)
    )

    assert decision.auto_merge_ok is False
    assert decision.reasons == ["4 Sources would be retired, more than the cap of 3"]
    assert decision.labels == ["automated", "source-review", "auto-merge-skipped"]


def test_gate_passes_a_plan_leaving_exactly_10_enabled_sources() -> None:
    decision = _gate(_plan_retiring("s0"), _enabled_sources(11))

    assert decision.auto_merge_ok is True


def test_gate_holds_a_plan_leaving_9_enabled_sources() -> None:
    decision = _gate(_plan_retiring("s0"), _enabled_sources(10))

    assert decision.auto_merge_ok is False
    assert decision.reasons == ["the plan would leave 9 enabled Sources, fewer than the floor of 10"]


def test_gate_floor_does_not_count_already_disabled_sources() -> None:
    sources = [*_enabled_sources(10), _source("old", enabled=False, retired_reason="dead-silent")]

    decision = _gate(_plan_retiring("s0"), sources)

    assert decision.reasons == ["the plan would leave 9 enabled Sources, fewer than the floor of 10"]


def test_gate_reports_every_tripped_breaker() -> None:
    decision = _gate(
        _plan_retiring("s0", "s1", "s2", "s3"), _enabled_sources(12)
    )

    assert decision.reasons == [
        "4 Sources would be retired, more than the cap of 3",
        "the plan would leave 8 enabled Sources, fewer than the floor of 10",
    ]


def test_gate_holds_a_retirement_of_a_source_that_is_not_enabled() -> None:
    # Backstop: review_sources never plans this; a hostile id is neutralised for the PR body.
    sources = [*_enabled_sources(12), _source("gone", enabled=False)]

    decision = _gate(
        _plan_retiring("gone", "[x](https://evil)"), sources
    )

    assert decision.auto_merge_ok is False
    assert decision.reasons == [
        "retirement targets gone, which is not an enabled Source",
        "retirement targets \\[x\\](https://evil), which is not an enabled Source",
    ]


# ── Applying a plan to sources.yaml ──────────────────────────────────────────

SOURCES_YAML = """\
sources:
  # ── Author / blog feeds ──
  - id: alive
    type: rss
    url: https://alive.example/feed
    cadence: weekly
    last_checked_at: 2026-09-29
    consecutive_failures: 0
    failing_since: null
    last_http_status: 200
    newest_entry_at: 2026-09-20
    enabled: true
    notes: null

  - id: broken  # moved to a new host?
    type: rss
    url: https://broken.example/feed
    cadence: weekly
    last_checked_at: 2026-08-01
    consecutive_failures: 5
    failing_since: 2026-08-25
    last_http_status: 404
    newest_entry_at: 2026-07-30
    enabled: true
    notes: null
"""


def _apply_to_yaml(text: str, plan) -> str:
    import io

    ryaml = source_review.round_trip_yaml()
    data = ryaml.load(text)
    source_review.apply_plan(data["sources"], plan)
    out = io.StringIO()
    ryaml.dump(data, out)
    return out.getvalue()


def test_applying_a_retirement_disables_the_source_and_records_date_and_reason() -> None:
    plan = _review([dict(s) for s in source_review.round_trip_yaml().load(SOURCES_YAML)["sources"]])
    assert _retired(plan) == {"broken": "dead-broken"}

    out = _apply_to_yaml(SOURCES_YAML, plan)

    # Only the retired Source changes; comments and layout are kept. Its frozen health
    # is dropped (the evidence lives in the PR body), so a revival starts clean.
    assert out == SOURCES_YAML.replace(
        "    last_checked_at: 2026-08-01\n"
        "    consecutive_failures: 5\n"
        "    failing_since: 2026-08-25\n"
        "    last_http_status: 404\n"
        "    newest_entry_at: 2026-07-30\n"
        "    enabled: true\n",
        "    last_checked_at: 2026-08-01\n"
        "    enabled: false\n"
        "    retired_at: 2026-09-29\n"
        "    retired_reason: dead-broken\n",
    )
    assert out.count("enabled: true") == 1


def test_applying_an_empty_plan_changes_nothing() -> None:
    assert _apply_to_yaml(SOURCES_YAML, source_review.ReviewPlan(today=TODAY)) == SOURCES_YAML


def test_applying_never_touches_a_source_that_is_already_disabled() -> None:
    retired = _source("r", enabled=False, retired_at=date(2026, 6, 1), retired_reason="dead-silent")
    before = dict(retired)

    source_review.apply_plan([retired], _plan_retiring("r"))

    assert retired == before


# ── PR body ──────────────────────────────────────────────────────────────────

PASSING = {"auto_merge_ok": True, "reasons": [], "labels": ["automated", "source-review"]}


def test_pr_body_lists_dead_broken_evidence_with_streak_span_and_run_count() -> None:
    sources = [_source("broken", failures=5, failing_since=date(2026, 8, 25), status=404,
                       newest=date(2026, 7, 30))]
    plan = _review(sources)

    body = source_review.pr_body(plan, PASSING, sources)

    assert body.startswith("> **Auto-merge enabled**")
    assert "## Retired Sources (1)" in body
    assert (
        "- **broken** — `dead-broken`: feed failing since 2026-08-25 (35 days, 5 failed Scout runs"
        " in a row); last HTTP status 404; newest entry 2026-07-30" in body
    )
    assert "Feed: https://broken.example/feed" in body


def test_pr_body_lists_dead_silent_evidence() -> None:
    sources = [_source("quiet", newest=date(2026, 1, 15), status=200)]

    body = source_review.pr_body(_review(sources), PASSING, sources)

    assert (
        "- **quiet** — `dead-silent`: newest entry 2026-01-15 (257 days ago) while the feed"
        " fetches fine; last HTTP status 200" in body
    )


def test_pr_body_lists_unproductive_evidence_with_judged_count_yield_and_window() -> None:
    sources = [_healthy("openai-news")]
    plan = _review(sources, seen=_rejects("openai-news", 42), today=LATER)

    body = source_review.pr_body(plan, PASSING, sources)

    assert (
        "- **openai-news** — `unproductive`: 42 entries judged and a Yield of 0 in the window"
        " after 2026-10-15 up to and including 2027-01-15" in body
    )
    assert "Feed: https://openai-news.example/feed" in body


def test_pr_body_sanitizes_the_id_of_an_unproductive_source() -> None:
    hostile_id = "u\n::warning::pwned [click](https://evil)"
    sources = [_healthy(hostile_id)]

    body = source_review.pr_body(
        _review(sources, seen=_rejects(hostile_id, 15), today=LATER), PASSING, sources
    )

    assert "`unproductive`" in body
    assert "\n::" not in body and "[click](" not in body


def test_pr_body_says_when_a_broken_feed_raised_instead_of_returning_a_status() -> None:
    sources = [_source("b", failures=4, failing_since=TODAY - timedelta(days=28), status=None,
                       newest=None)]

    body = source_review.pr_body(_review(sources), PASSING, sources)

    assert "last HTTP status none (the fetch raised); newest entry never seen" in body


def test_pr_body_shows_the_hold_reasons_when_the_gate_holds() -> None:
    held = {"auto_merge_ok": False, "reasons": ["4 Sources would be\nretired", "floor"], "labels": []}

    body = source_review.pr_body(source_review.ReviewPlan(today=TODAY), held, [])

    assert body.startswith("> **Auto-merge skipped:** 4 Sources would be retired; floor.\n")


def test_pr_body_sanitizes_source_ids_and_urls() -> None:
    hostile_id = "x\n::error::pwned [click](https://evil) <b>"
    sources = [
        _source(
            hostile_id, failures=4, failing_since=TODAY - timedelta(days=28), status=404,
            url="https://ok.example/feed\n::warning::injected [a](b)",
        )
    ]

    body = source_review.pr_body(_review(sources), PASSING, sources)

    assert "\n::" not in body
    assert "[click](" not in body and "<b>" not in body and "[a](b)" not in body
    assert "x ::error::pwned \\[click\\](https://evil) \\<b\\>" in body
