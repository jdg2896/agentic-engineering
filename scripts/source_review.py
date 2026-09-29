#!/usr/bin/env python3
"""Source review: the monthly job that curates the Source list itself.

This slice implements Source retirement: Dead Sources, read from the Source health
Scout records in sources.yaml, and Unproductive Sources, read from the Yield and rejects
Scout attributes to each Source. Source discovery extends `review_sources` later (#104,
#105); its signature is already final.
"""

from __future__ import annotations

import argparse
import calendar
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import NamedTuple

import scout
from scout import SKIPPED_LABEL, Decision, SourceHealth, round_trip_yaml, sanitize_text

# A Source is Dead-broken once its failure streak spans at least this many days AND
# this many Scout runs. Both are required: a discarded or held Scout run does not
# count, so the run count can lag the span. That only ever delays a retirement.
DEAD_BROKEN_MIN_DAYS = 28
DEAD_BROKEN_MIN_RUNS = 4
# A Source that fetches fine is Dead-silent once its newest entry is older than this.
DEAD_SILENT_MONTHS = 6
# A Source is Unproductive once at least this many of its entries were judged in the
# trailing window with a Yield of zero, and its grace period has passed.
UNPRODUCTIVE_WINDOW_MONTHS = 3
UNPRODUCTIVE_MIN_JUDGED = 15
# The day Scout began attributing Resources to their Source (PR #106). Yield before it
# is unmeasurable, so it is the earliest start of any Source's grace period.
ATTRIBUTION_SHIPPED = date(2026, 9, 29)

# `fetch(url)` -> (HTTP status, headers, body); `judge` is Scout's per-entry judge.
Fetch = Callable[[str], tuple]
Judge = Callable[[str, str, str, str], dict]


class UnproductiveEvidence(NamedTuple):
    """What shows a Source is Unproductive: its judged count and Yield in the window.

    The window is `window_start` < day <= `window_end` (the review date).
    """

    judged: int  # rejects in seen.yaml + Resources attributed to the Source, in window
    yield_count: int  # Resources attributed to the Source, in window
    window_start: date  # exclusive
    window_end: date  # inclusive


class Retirement(NamedTuple):
    """A Source to retire, why, and the evidence that shows it."""

    source_id: str
    reason: str  # dead-broken | dead-silent | unproductive
    # Source health for a Dead Source; judged count and Yield for an Unproductive one.
    evidence: SourceHealth | UnproductiveEvidence


@dataclass
class ReviewPlan:
    """Everything one Source review run would change on the Source list."""

    today: date  # the review date; retirements are stamped with it
    retirements: list[Retirement] = field(default_factory=list)
    additions: list[dict] = field(default_factory=list)  # Source discovery, #104/#105
    rejected: list[dict] = field(default_factory=list)  # rejected Prospective Sources, #104/#105
    incomplete: str | None = None  # why the run stopped early (e.g. judge quota), else None

    @property
    def is_empty(self) -> bool:
        """Nothing to add or retire: the run opens no PR."""
        # #104 must also count `rejected`: rejected suggestions need a PR to close their issues.
        return not self.retirements and not self.additions


def _date_field(value) -> date | None:
    """A date read back from sources.yaml: a date, an ISO string, or null."""
    return None if value is None else date.fromisoformat(str(value))


def _recorded_health(source: dict) -> SourceHealth | None:
    """The Source health Scout recorded on `source`, or None if it has none yet."""
    if "consecutive_failures" not in source:
        return None
    return SourceHealth(
        consecutive_failures=int(source.get("consecutive_failures") or 0),
        failing_since=_date_field(source.get("failing_since")),
        last_http_status=source.get("last_http_status"),
        newest_entry_at=_date_field(source.get("newest_entry_at")),
    )


def _months_before(day: date, months: int) -> date:
    """The same day `months` calendar months earlier, clamped to that month's last day."""
    year, month0 = divmod(day.year * 12 + day.month - 1 - months, 12)
    last_day = calendar.monthrange(year, month0 + 1)[1]
    return date(year, month0 + 1, min(day.day, last_day))


def _dead_reason(health: SourceHealth, today: date) -> str | None:
    if (
        health.failing_since is not None
        and health.consecutive_failures >= DEAD_BROKEN_MIN_RUNS
        and (today - health.failing_since).days >= DEAD_BROKEN_MIN_DAYS
    ):
        return "dead-broken"
    if (
        health.consecutive_failures == 0
        and health.newest_entry_at is not None
        and health.newest_entry_at < _months_before(today, DEAD_SILENT_MONTHS)
    ):
        return "dead-silent"
    return None


def _unproductive_window(today: date) -> tuple[date, date]:
    """The trailing window as (exclusive start, inclusive end): the last 3 calendar months."""
    return _months_before(today, UNPRODUCTIVE_WINDOW_MONTHS), today


def _grace_period_passed(source: dict, window_start: date) -> bool:
    """True once the whole window lies after the Source's grace anchor.

    The anchor is the later of the attribution ship date and the Source's own
    `added_at`. The window begins the day after `window_start`, so the grace period
    ends exactly 3 calendar months after the anchor, by the same month arithmetic.
    Sources added by hand should set `added_at`; otherwise their grace period falls
    back to the attribution ship date alone.
    """
    added_at = _date_field(source.get("added_at"))
    anchor = max(ATTRIBUTION_SHIPPED, added_at) if added_at else ATTRIBUTION_SHIPPED
    return window_start >= anchor


def _attributed_dates(entries: list[dict], ids: set[str], date_key: str) -> dict[str, list[date]]:
    """Per Source id in `ids`, the `date_key` date of every entry attributed to it.

    Entries of other Sources, and unattributed ones (hand-curated Resources), are
    skipped. A missing or malformed date on an entry that counts raises: a retirement
    is never decided on data that cannot be read.
    """
    dates: dict[str, list[date]] = {i: [] for i in ids}
    for entry in entries:
        source_id = entry.get("source_id")
        if source_id not in dates:
            continue
        value = entry.get(date_key)
        if value is None:
            raise ValueError(f"an entry attributed to Source {source_id!r} has no {date_key}")
        dates[source_id].append(_date_field(value))
    return dates


def _unproductive_evidence(
    sources: list[dict], resources: list[dict], seen: list[dict], today: date
) -> dict[str, UnproductiveEvidence]:
    """Evidence for every enabled, out-of-grace Source that is Unproductive, by Source id."""
    start, end = _unproductive_window(today)
    eligible = {
        s["id"] for s in sources if s.get("enabled", True) and _grace_period_passed(s, start)
    }
    yielded = _attributed_dates(resources, eligible, "added_at")
    rejected = _attributed_dates(seen, eligible, "rejected_at")
    found = {}
    for source_id in eligible:
        yield_count = sum(start < d <= end for d in yielded[source_id])
        judged = yield_count + sum(start < d <= end for d in rejected[source_id])
        if judged >= UNPRODUCTIVE_MIN_JUDGED and yield_count == 0:
            found[source_id] = UnproductiveEvidence(judged, yield_count, start, end)
    return found


def review_sources(
    sources: list[dict],
    resources: list[dict],
    seen: list[dict],
    suggestions: list[dict],
    today: date,
    fetch: Fetch,
    judge: Judge,
) -> ReviewPlan:
    """Plan one Source review: which Sources to retire (and, from #103-#105, add).

    Pure apart from the injected `fetch` (url -> status, headers, body) and `judge`
    (Scout's per-entry judge), which Source discovery will call; this slice calls
    neither, and `suggestions` is read by later slices only. Nothing passed in is
    modified.

    An enabled Source is Dead, whatever its age (no grace period):
    - `dead-broken` when its failure streak spans >= 28 days AND >= 4 failed runs;
    - `dead-silent` when it fetches fine (no current streak) but its newest entry is
      older than 6 calendar months.
    Never retire on missing data: a Source with no Source health yet (Scout has not
    run on it since health recording shipped) is left alone, and one whose feed has
    never shown a dated entry is never judged silent. Disabled and Retired Sources
    are skipped.

    An enabled Source is Unproductive when, in the trailing window of 3 calendar months
    (after the day 3 months before `today`, up to and including `today`), at least 15
    of its entries were judged — its rejects in `seen` by `rejected_at` plus the
    Resources attributed to it by `added_at` — and its Yield (those Resources) is zero.
    It is exempt during its grace period: until the whole window lies after the later
    of ATTRIBUTION_SHIPPED and its own `added_at`. A Source both Dead and Unproductive
    is retired once, as Dead: its Source health is the more direct evidence, and a
    Dead Source can be repaired (a moved feed) where an Unproductive one cannot.

    Raises ValueError on a missing or malformed date that a decision depends on.
    """
    plan = ReviewPlan(today=today)
    unproductive = _unproductive_evidence(sources, resources, seen, today)
    for source in sources:
        if not source.get("enabled", True):
            continue  # Retired (or hand-disabled): Source retirement never touches it again
        health = _recorded_health(source)  # None (no health yet): never judged Dead
        reason = _dead_reason(health, today) if health is not None else None
        if reason:
            plan.retirements.append(Retirement(source["id"], reason, health))
        elif source["id"] in unproductive:
            plan.retirements.append(
                Retirement(source["id"], "unproductive", unproductive[source["id"]])
            )
    return plan


def apply_plan(sources: list[dict], plan: ReviewPlan) -> None:
    """Apply a plan's retirements to the Source list in place.

    A retired Source gets `enabled: false`, `retired_at` (the plan's date) and
    `retired_reason`; the two new keys go right after `enabled` on a ruamel round-trip
    mapping, so comments and layout are kept. Its Source health keys are removed: Scout
    skips disabled Sources, so that health would stay frozen, and a revived Source
    would otherwise be re-retired on its old streak before Scout fetched it again. The
    evidence lives on in the PR body (ADR 0002). Without health, a revived Source is
    left alone until Scout records fresh health. The Source itself is never deleted, a
    Source that is already disabled is left alone, and Resources are not an input:
    retiring a Source never touches what it yielded.
    """
    by_id = {s["id"]: s for s in sources if s.get("enabled", True)}
    for retirement in plan.retirements:
        source = by_id.get(retirement.source_id)
        if source is None:
            continue  # the gate holds such a plan; there is nothing enabled to retire
        for key in SourceHealth._fields:
            source.pop(key, None)
        source["enabled"] = False
        anchor = "enabled"
        for key, value in (("retired_at", plan.today), ("retired_reason", retirement.reason)):
            if key not in source and hasattr(source, "insert"):
                source.insert(list(source).index(anchor) + 1, key, value)
            else:
                source[key] = value
            anchor = key


# ── Auto-merge gate (ADR 0002) ───────────────────────────────────────────────

RETIREMENT_CAP = 3
ENABLED_FLOOR = 10
BASE_LABELS = ["automated", "source-review"]


def _enabled_ids(sources: list[dict]) -> set[str]:
    return {s["id"] for s in sources if s.get("enabled", True)}


def _mass_retirement(plan: ReviewPlan, sources: list[dict], resources: list[dict]) -> list[str]:
    n = len(plan.retirements)
    if n > RETIREMENT_CAP:
        return [f"{n} Sources would be retired, more than the cap of {RETIREMENT_CAP}"]
    return []


def _retirement_of_non_enabled_source(
    plan: ReviewPlan, sources: list[dict], resources: list[dict]
) -> list[str]:
    enabled = _enabled_ids(sources)
    return [
        f"retirement targets {sanitize_text(r.source_id, 80)}, which is not an enabled Source"
        for r in plan.retirements
        if r.source_id not in enabled
    ]


def _unproductive_retirement_with_yield(
    plan: ReviewPlan, sources: list[dict], resources: list[dict]
) -> list[str]:
    """Hold an Unproductive retirement of a Source that yielded in its window.

    Impossible by definition, so a logic-bug backstop. It recounts Yield from
    `resources` rather than reading the plan's evidence (which would only re-check
    the rule against itself), and counts more broadly than the rule: every Resource
    attributed to the Source added after the window start, with no upper bound. Dead
    retirements are exempt: a feed that broke last month may well have yielded before.
    """
    start, _ = _unproductive_window(plan.today)
    reasons = []
    for r in plan.retirements:
        if r.reason != "unproductive":
            continue
        n = sum(
            1
            for res in resources
            if res.get("source_id") == r.source_id and _date_field(res.get("added_at")) > start
        )
        if n:
            reasons.append(
                f"Unproductive retirement of {sanitize_text(r.source_id, 80)}, "
                f"which has a Yield of {n} in its window"
            )
    return reasons


def _enabled_floor(plan: ReviewPlan, sources: list[dict], resources: list[dict]) -> list[str]:
    retiring = {r.source_id for r in plan.retirements}
    after = len(_enabled_ids(sources) - retiring) + len(plan.additions)
    if after < ENABLED_FLOOR:
        return [f"the plan would leave {after} enabled Sources, fewer than the floor of {ENABLED_FLOOR}"]
    return []


# Each breaker returns its hold reasons (empty when it passes). Mass addition and the
# addition structural checks (unsafe url, duplicate host) join with #104/#105.
_BREAKERS: tuple[Callable[[ReviewPlan, list[dict], list[dict]], list[str]], ...] = (
    _mass_retirement,
    _retirement_of_non_enabled_source,
    _unproductive_retirement_with_yield,
    _enabled_floor,
)


def evaluate_source_review_automerge(
    plan: ReviewPlan, sources: list[dict], resources: list[dict]
) -> Decision:
    """Circuit-breaker gate for Source review PRs (ADR 0002); pure, anomalies only.

    `sources` is the Source list before the plan is applied, and `resources` the
    Resources the plan was made from (for the independent Yield recount). Reasons are
    joined into the PR body, so any Source id in them is sanitized.
    """
    reasons = [reason for breaker in _BREAKERS for reason in breaker(plan, sources, resources)]
    labels = list(BASE_LABELS) if not reasons else [*BASE_LABELS, SKIPPED_LABEL]
    return Decision(not reasons, reasons, labels)


# ── PR body ──────────────────────────────────────────────────────────────────


def _evidence(retirement: Retirement, today: date) -> str:
    """One line of evidence for a retirement; built only from dates and integers."""
    if isinstance(retirement.evidence, UnproductiveEvidence):
        e = retirement.evidence
        return (
            f"{e.judged} entries judged and a Yield of {e.yield_count} in the window after "
            f"{e.window_start} up to and including {e.window_end}"
        )
    h = retirement.evidence
    status = "none (the fetch raised)" if h.last_http_status is None else str(h.last_http_status)
    newest = "never seen" if h.newest_entry_at is None else str(h.newest_entry_at)
    if retirement.reason == "dead-broken":
        # Both the span and the run count: the count can lag the span (see DEAD_BROKEN_MIN_RUNS).
        days = (today - h.failing_since).days
        return (
            f"feed failing since {h.failing_since} ({days} days, {h.consecutive_failures} failed "
            f"Scout runs in a row); last HTTP status {status}; newest entry {newest}"
        )
    if retirement.reason == "dead-silent":
        days = (today - h.newest_entry_at).days
        return (
            f"newest entry {newest} ({days} days ago) while the feed fetches fine; "
            f"last HTTP status {status}"
        )
    return f"last HTTP status {status}; newest entry {newest}"


def pr_body(plan: ReviewPlan, decision: dict, sources: list[dict]) -> str:
    """The Source review PR body: an auto-merge banner, then each retirement with its evidence.

    Pure. Everything that came from sources.yaml or a feed (Source ids, feed urls) is
    sanitized, and each gate reason is collapsed onto one line, so no text can inject
    Markdown links, HTML or a `::` workflow command.
    """
    if decision["auto_merge_ok"]:
        prefix = "> **Auto-merge enabled** — this PR will land once required checks pass.\n"
    else:
        reasons = "; ".join(" ".join(str(r).split()) for r in decision["reasons"])
        prefix = f"> **Auto-merge skipped:** {reasons}.\n"
    urls = {s["id"]: s.get("url") for s in sources}
    rows = []
    for r in plan.retirements:
        rows.append(
            f"- **{sanitize_text(r.source_id, 120)}** — `{sanitize_text(r.reason, 40)}`: "
            f"{_evidence(r, plan.today)}\n"
            f"  Feed: {sanitize_text(str(urls.get(r.source_id)), 300)}"
        )
    sections = [prefix]
    if rows:
        sections.append(
            f"## Retired Sources ({len(rows)})\n\n"
            "Each stays in sources.yaml with `enabled: false`, `retired_at` and `retired_reason`; "
            "its Source health is cleared and its Resources are untouched. Revive one by "
            "setting `enabled: true` (and fixing its `url` if the feed moved).\n\n"
            + "\n".join(rows)
        )
    return "\n".join(sections)


# ── Entry point ──────────────────────────────────────────────────────────────


def _not_wired(what: str, ticket: str) -> Callable:
    """A stand-in for an injected dependency this slice never calls."""

    def unavailable(*args, **kwargs):
        raise RuntimeError(f"Source review has no {what} yet; {ticket} wires it in")

    return unavailable


def main() -> None:
    parser = argparse.ArgumentParser(description="Source review: retire Dead and Unproductive Sources")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan and gate; write nothing")
    parser.add_argument(
        "--summary", type=Path, default=None, metavar="JSON",
        help="Write the PR title, body, labels and gate decision here (for the workflow)",
    )
    args = parser.parse_args()

    ryaml = round_trip_yaml()
    with open(scout.SOURCES_PATH) as f:
        sources_data = ryaml.load(f)
    with open(scout.RESOURCES_PATH) as f:
        resources = ryaml.load(f)["resources"]
    with open(scout.SEEN_PATH) as f:
        seen = ryaml.load(f)["seen"] or []
    sources = sources_data["sources"]

    today = date.today()
    plan = review_sources(
        sources, resources, seen, [], today,
        # Neither is called until discovery lands; the Scout judge (and its OAuth
        # secret, scoped to its step in the workflow) is wired in with #104.
        fetch=_not_wired("fetch", "#104"),
        judge=_not_wired("judge", "#104"),
    )
    decision = evaluate_source_review_automerge(plan, sources, resources)

    enabled = sum(1 for s in sources if s.get("enabled", True))
    print(f"Reviewed {enabled} enabled Source(s): {len(plan.retirements)} to retire.")
    for r in plan.retirements:
        # Sanitized: a Source id with a line break could otherwise emit a `::` workflow command.
        print(f"  [{r.reason}] {sanitize_text(r.source_id, 120)}: {_evidence(r, today)}")
    print(json.dumps(decision._asdict()))

    if args.dry_run:
        print("Dry run — no files written.")
        return

    summary: dict = {"empty": plan.is_empty}
    if plan.is_empty:
        print("Nothing to retire; no PR.")
    else:
        apply_plan(sources, plan)
        with open(scout.SOURCES_PATH, "w") as f:
            ryaml.dump(sources_data, f)
        print(f"Updated {scout.SOURCES_PATH}")
        n = len(plan.retirements)
        summary |= {
            "title": f"Source review — {today:%Y-%m} — {n} retirement{'s' if n != 1 else ''}",
            "body": pr_body(plan, decision._asdict(), sources),
            **decision._asdict(),
        }
    if args.summary is not None:
        args.summary.write_text(json.dumps(summary))


if __name__ == "__main__":
    main()
