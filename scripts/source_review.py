#!/usr/bin/env python3
"""Source review: the monthly job that curates the Source list itself.

Source retirement retires Dead Sources, read from the Source health Scout records in
sources.yaml, and Unproductive Sources, read from the Yield and rejects Scout attributes
to each Source. Source discovery adds Prospective Sources that pass a Trial through the
Scout judge; Source suggestions (GitHub issues) are its first channel, and Citation
mining (#105) feeds the same pipeline.
"""

from __future__ import annotations

import argparse
import calendar
import functools
import io
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urljoin, urlsplit

import feedparser
import idna

import judge
import scout
from scout import (
    SKIPPED_LABEL,
    Decision,
    SourceHealth,
    is_safe_url,
    round_trip_yaml,
    sanitize_text,
)

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
# A Trial judges up to this many of a Prospective Source's entries, newest first, from
# the trailing window; a Source added by it starts `last_checked_at` that far back.
TRIAL_SIZE = 10
TRIAL_WINDOW_MONTHS = 6

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


class ProspectiveSource(NamedTuple):
    """A feed (or a site that may advertise one) Source discovery is considering adding.

    Every channel produces these and they all go through the same Trial: a Source
    suggestion (`channel="suggestion"`, with its issue number) here, Citation mining
    (#105) next. `url` is untrusted until checked with `is_safe_url`.
    """

    url: str
    channel: str  # suggestion | citation
    suggestion: int | None = None  # the Source suggestion's issue number


class TrialEvidence(NamedTuple):
    """What a Trial found: its entries in the window and the judge's verdicts on them."""

    in_window: int  # dated entries in the 6-month window (the Trial judges up to 10)
    judged: int
    # (title, url) of each entry judged `include`; the title is sanitized judge text and
    # the url passed `is_safe_url`. Evidence only: never written to resources.yaml.
    included: tuple[tuple[str, str], ...]


class Addition(NamedTuple):
    """A Prospective Source whose Trial passed, as the new sources.yaml entry it becomes."""

    source: dict  # the new Source entry
    prospect: ProspectiveSource
    trial: TrialEvidence


class Rejection(NamedTuple):
    """A Prospective Source that is not added, and why."""

    prospect: ProspectiveSource
    # no-url | unsafe-url | duplicate | fetch-failed | no-feed | no-recent-entries |
    # no-include | judge-error
    reason: str
    detail: str = ""  # sanitized, one line
    trial: TrialEvidence | None = None

    @property
    def decided(self) -> bool:
        """False for a judge error: nothing was learnt, so its suggestion stays open."""
        return self.reason != "judge-error"


@dataclass
class ReviewPlan:
    """Everything one Source review run would change on the Source list."""

    today: date  # the review date; retirements are stamped with it
    retirements: list[Retirement] = field(default_factory=list)
    additions: list[Addition] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)  # rejected Prospective Sources
    incomplete: str | None = None  # why Trials stopped early (quota or judge error); sanitized

    @property
    def is_empty(self) -> bool:
        """Nothing to add, retire or decide: the run opens no PR.

        A decided rejection counts: its PR records it and closes its suggestion issue.
        A judge-error rejection does not: nothing was decided, and nothing is written.
        """
        return (
            not self.retirements
            and not self.additions
            and not any(r.decided for r in self.rejected)
        )


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


# ── Source discovery ─────────────────────────────────────────────────────────


def host_key(url: object) -> str | None:
    """The host two Sources are compared by: IDNA-normalised, lower-case, `www.`-stripped.

    None when `url` has no parseable host.
    """
    try:
        host = urlsplit(str(url)).hostname or ""
        host = idna.encode(host, uts46=True).decode("ascii") if host else ""
    except (ValueError, idna.IDNAError, UnicodeError):
        return None
    host = host.rstrip(".").lower()
    host = host.removeprefix("www.")
    return host or None


_FEED_TYPES = frozenset({"application/rss+xml", "application/atom+xml"})


class _FeedLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        rels = a.get("rel", "").lower().split()
        kind = a.get("type", "").split(";")[0].strip().lower()
        if "alternate" in rels and kind in _FEED_TYPES and a.get("href", "").strip():
            self.hrefs.append(a["href"].strip())


def discover_feed_links(html: str, page_url: str) -> list[str]:
    """RSS/Atom autodiscovery: the `<link rel="alternate">` feed urls a page advertises.

    Relative hrefs are resolved against `page_url`. Returned in page order and
    unvalidated: the caller checks each with `is_safe_url`. Never raises on bad HTML.
    """
    parser = _FeedLinkParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — hostile markup must not crash the review
        pass
    return [urljoin(page_url, href) for href in parser.hrefs]


def _text(body: object) -> str:
    return body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)


def _parse_feed(status: int, body: object):
    """Parse a fetched body as a feed. Never pass feedparser a str or bytes: it would
    treat one that looks like a url or a path as something to fetch or open."""
    raw = body if isinstance(body, bytes) else str(body).encode("utf-8")
    parsed = feedparser.parse(io.BytesIO(raw))
    parsed["status"] = status
    return parsed


class _TrialStop(Exception):
    """The judge stopped Trials; carries the rejection (or None) and the incomplete note."""

    def __init__(self, rejection: Rejection | None, note: str) -> None:
        self.rejection = rejection
        self.note = note


def _source_id(feed_url: str, taken: set[str]) -> str:
    """A safe, unique Source id from the feed's host: `[a-z0-9-]` only, at most 60 chars."""
    base = re.sub(r"[^a-z0-9]+", "-", host_key(feed_url) or "").strip("-")[:60].strip("-")
    return scout.safe_slug(base or "source", taken)


def _resolve_feed(prospect: ProspectiveSource, fetch: Fetch):
    """Find the Prospective Source's feed: (feed url, parsed feed) or a Rejection.

    The url itself if it is a feed, else the first safe feed its page advertises.
    """
    url = prospect.url
    try:
        status, _headers, body = fetch(url)
    except Exception as exc:  # noqa: BLE001 — any fetch failure rejects, never crashes
        return Rejection(prospect, "fetch-failed", sanitize_text(str(exc), 200))
    if status >= 400:
        return Rejection(prospect, "fetch-failed", f"HTTP status {int(status)}")
    parsed = _parse_feed(status, body)
    if parsed.get("version"):
        return url, parsed
    links = discover_feed_links(_text(body), url)
    safe = [link for link in links if is_safe_url(link)]
    if not safe:
        return Rejection(prospect, "unsafe-url" if links else "no-feed")
    feed_url = safe[0]
    try:
        status, _headers, body = fetch(feed_url)
    except Exception as exc:  # noqa: BLE001
        return Rejection(prospect, "fetch-failed", sanitize_text(str(exc), 200))
    parsed = _parse_feed(status, body)
    if status < 400 and not parsed.get("version"):
        return Rejection(prospect, "no-feed", "the advertised feed is not RSS or Atom")
    return feed_url, parsed


def _trial(
    prospect: ProspectiveSource, feed_url: str, parsed, source_id: str, today: date, judge: Judge
) -> TrialEvidence | Rejection:
    """Judge up to TRIAL_SIZE of the feed's entries from the last 6 months, newest first.

    Pass iff the feed fetched cleanly (Scout's `read_source` rule), has an entry in the
    window, and the judge includes at least one. Raises `_TrialStop` when the judge
    fails, with Scout's semantics.
    """
    window_start = _months_before(today, TRIAL_WINDOW_MONTHS)
    read = scout.read_source({"id": source_id, "last_checked_at": window_start}, parsed, today)
    if read.health.consecutive_failures:
        status = read.health.last_http_status
        return Rejection(prospect, "fetch-failed", f"HTTP status {status}" if status else "unparseable feed")
    in_window = sorted(
        (e for e in read.new_entries if scout.entry_date(e) <= today),
        key=scout.entry_date,
        reverse=True,
    )
    if not in_window:
        return Rejection(prospect, "no-recent-entries")
    run = scout.judge_sources({source_id: in_window[:TRIAL_SIZE]}, judge, set(), set())
    evidence = TrialEvidence(
        len(in_window), run.evaluated, tuple((c["title"], c["url"]) for c in run.candidates)
    )
    if run.quota_exhausted is not None:
        raise _TrialStop(
            None,
            "Trials stopped on the Claude usage limit; the remaining Prospective Sources "
            f"are tried next run. {sanitize_text(run.quota_exhausted, 300)}",
        )
    if run.errors:
        raise _TrialStop(
            # judge_sources sanitized the error when it recorded it.
            Rejection(prospect, "judge-error", run.errors[0], evidence),
            "Trials stopped on a judge error; the remaining Prospective Sources are tried "
            f"next run. {scout.judge_failure_hint(run)}",
        )
    if not evidence.included:
        return Rejection(prospect, "no-include", trial=evidence)
    return evidence


def _feed_type(parsed) -> str:
    return "atom" if str(parsed.get("version", "")).startswith("atom") else "rss"


def _new_source(source_id: str, feed_url: str, parsed, prospect: ProspectiveSource, today: date) -> dict:
    channel = (
        f"Source suggestion #{prospect.suggestion}" if prospect.channel == "suggestion"
        else "Citation mining"
    )
    return {
        "id": source_id,
        "type": _feed_type(parsed),
        "url": feed_url,
        "cadence": "weekly",
        # Six months back, so Scout's next run picks up the Source's recent good entries.
        "last_checked_at": _months_before(today, TRIAL_WINDOW_MONTHS),
        "enabled": True,
        "notes": f"Added by Source review from {channel}",
        "added_at": today,
        "added_by": "source-review",
    }


SUGGESTION_AUTHORS = frozenset({"OWNER", "COLLABORATOR"})
# Up to the first character that cannot be part of a url in running text or Markdown.
_BODY_URL_RE = re.compile(r"https?://[^\s<>()\[\]\"'`]+", re.IGNORECASE)


def suggestions_from_issues(issues: list) -> list[dict]:
    """Source suggestions from open `source-suggestion` issues, as the GitHub REST API lists them.

    The repo is public, so an issue body is untrusted input: only issues (not pull
    requests) whose author is the repo OWNER or a COLLABORATOR are kept, and nothing
    of the body is used but its first http(s) url (None when it has none), which
    Source discovery then checks with `is_safe_url` before any fetch. Returns
    `{"number": int, "url": str | None}` dicts in issue order.
    """
    suggestions = []
    for issue in issues:
        if not isinstance(issue, dict) or "pull_request" in issue:
            continue
        if issue.get("author_association") not in SUGGESTION_AUTHORS:
            continue
        number = issue.get("number")
        if type(number) is not int or number <= 0:
            continue
        match = _BODY_URL_RE.search(str(issue.get("body") or ""))
        url = match.group(0).rstrip(".,;:!?") if match else None
        suggestions.append({"number": number, "url": url})
    return suggestions


def _suggested_prospects(suggestions: list[dict]) -> list[ProspectiveSource]:
    """Source suggestions as Prospective Sources. The workflow already kept only the
    owner's and collaborators' (see `suggestions_from_issues`); a suggestion with no
    url keeps an empty one and is rejected `no-url`."""
    return [
        ProspectiveSource(str(s.get("url") or ""), "suggestion", suggestion=int(s["number"]))
        for s in suggestions
    ]


def _discover(
    plan: ReviewPlan,
    prospects: list[ProspectiveSource],
    sources: list[dict],
    today: date,
    fetch: Fetch,
    judge: Judge,
) -> None:
    """Trial each Prospective Source in turn, adding passes and recording rejections on `plan`.

    Duplicates (same host as any Source on the list, enabled or Retired, or as a
    Source added earlier this run) are rejected before and after feed resolution.
    A judge failure stops all further Trials (see `_trial`); Prospective Sources left
    untried are neither added nor rejected, so their suggestions stay open.
    """
    taken_hosts = {h for h in (host_key(s.get("url")) for s in sources) if h}
    taken_ids = {str(s.get("id")) for s in sources}

    def duplicate(url: str) -> bool:
        return host_key(url) in taken_hosts

    for prospect in prospects:
        if not prospect.url:
            plan.rejected.append(Rejection(prospect, "no-url"))
            continue
        if not is_safe_url(prospect.url):
            plan.rejected.append(Rejection(prospect, "unsafe-url"))
            continue
        if duplicate(prospect.url):
            plan.rejected.append(Rejection(prospect, "duplicate"))
            continue
        resolved = _resolve_feed(prospect, fetch)
        if isinstance(resolved, Rejection):
            plan.rejected.append(resolved)
            continue
        feed_url, parsed = resolved
        if duplicate(feed_url):
            plan.rejected.append(Rejection(prospect, "duplicate", "its feed is on a Source's host"))
            continue
        source_id = _source_id(feed_url, taken_ids)
        try:
            result = _trial(prospect, feed_url, parsed, source_id, today, judge)
        except _TrialStop as stop:
            if stop.rejection is not None:
                plan.rejected.append(stop.rejection)
            plan.incomplete = stop.note
            return
        if isinstance(result, Rejection):
            plan.rejected.append(result)
            continue
        plan.additions.append(
            Addition(_new_source(source_id, feed_url, parsed, prospect, today), prospect, result)
        )
        taken_ids.add(source_id)
        taken_hosts.add(host_key(feed_url))
        taken_hosts.add(host_key(prospect.url))


def review_sources(
    sources: list[dict],
    resources: list[dict],
    seen: list[dict],
    suggestions: list[dict],
    today: date,
    fetch: Fetch,
    judge: Judge,
) -> ReviewPlan:
    """Plan one Source review: which Sources to retire and which to add.

    Pure apart from the injected `fetch` (url -> status, headers, body) and `judge`
    (Scout's per-entry judge, with Scout's system prompt), which only Source discovery
    calls. Nothing passed in is modified.

    Source discovery: each Source suggestion (`{"number", "url"}`, already limited to
    the owner's and collaborators', see `suggestions_from_issues`) is a Prospective
    Source. Its url must pass `is_safe_url` and not share a host (www-stripped) with
    any Source on the list, enabled or Retired; its feed is the url itself or, for a
    page, the first safe RSS/Atom feed the page advertises (`no-feed` when none). Its
    Trial judges up to 10 of the feed's entries from the last 6 calendar months,
    newest first, dated by Scout's `entry_date`; it passes iff the feed fetched
    cleanly (Scout's `read_source` rule), has an entry in the window, and at least one
    is judged `include`. A pass becomes a new Source entry with `added_at`,
    `added_by: source-review` and `last_checked_at` 6 months back; its included
    entries are evidence only (nothing is written to resources.yaml). A judge error
    rejects that Prospective Source (`judge-error`, not decided) and a usage limit
    leaves it untried; either stops all further Trials and sets `plan.incomplete`,
    while retirements and completed Trials stand (Scout's semantics).

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
    _discover(plan, _suggested_prospects(suggestions), sources, today, fetch, judge)
    return plan


def apply_plan(sources: list[dict], plan: ReviewPlan, document: dict | None = None) -> None:
    """Apply a plan to the Source list in place: retirements, additions, decided rejections.

    Each addition is appended to `sources` as its new Source entry. Each decided
    rejection is appended to `rejected_prospective_sources` in `document` (the whole
    sources.yaml mapping, when given): the record of what Source discovery turned down,
    and the change that lets a run whose only outcome is a rejection open the PR that
    closes its suggestion issue. A judge-error rejection decided nothing and is not
    recorded. Unsafe urls are recorded as null.

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
    def fresh(day: date) -> date:
        # A new object per write: the YAML dumper would turn one shared date into an
        # anchor and aliases (`&id001` / `*id001`).
        return date(day.year, day.month, day.day)

    by_id = {s["id"]: s for s in sources if s.get("enabled", True)}
    for retirement in plan.retirements:
        source = by_id.get(retirement.source_id)
        if source is None:
            continue  # the gate holds such a plan; there is nothing enabled to retire
        for key in SourceHealth._fields:
            source.pop(key, None)
        source["enabled"] = False
        anchor = "enabled"
        for key, value in (("retired_at", fresh(plan.today)), ("retired_reason", retirement.reason)):
            if key not in source and hasattr(source, "insert"):
                source.insert(list(source).index(anchor) + 1, key, value)
            else:
                source[key] = value
            anchor = key
    for addition in plan.additions:
        sources.append({k: fresh(v) if isinstance(v, date) else v for k, v in addition.source.items()})
        if hasattr(sources, "yaml_set_comment_before_after_key"):
            # A blank line before it, like the hand-written entries in sources.yaml.
            sources.yaml_set_comment_before_after_key(len(sources) - 1, before="\n")
    decided = [r for r in plan.rejected if r.decided]
    if decided and document is not None:
        key = "rejected_prospective_sources"
        if document.get(key) is None:
            document[key] = []
            if hasattr(document, "yaml_set_comment_before_after_key"):
                document.yaml_set_comment_before_after_key(key, before="\n")
        for r in decided:
            document[key].append({
                "url": r.prospect.url if is_safe_url(r.prospect.url) else None,
                "channel": r.prospect.channel,
                "suggestion": r.prospect.suggestion,
                "reason": r.reason,
                "rejected_at": fresh(plan.today),
            })


# ── Auto-merge gate (ADR 0002) ───────────────────────────────────────────────

RETIREMENT_CAP = 3
ADDITION_CAP = 3
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


def _mass_addition(plan: ReviewPlan, sources: list[dict], resources: list[dict]) -> list[str]:
    n = len(plan.additions)
    if n > ADDITION_CAP:
        return [f"{n} Sources would be added, more than the cap of {ADDITION_CAP}"]
    return []


def _addition_with_unsafe_url(
    plan: ReviewPlan, sources: list[dict], resources: list[dict]
) -> list[str]:
    return [
        f"new Source {sanitize_text(a.source.get('id'), 80)} has an unsafe feed url"
        for a in plan.additions
        if not is_safe_url(a.source.get("url"))
    ]


def _addition_duplicating_a_source(
    plan: ReviewPlan, sources: list[dict], resources: list[dict]
) -> list[str]:
    """Hold an addition whose host (or id) is already on the list, Retired Sources included.

    Recomputed from the Source list rather than trusted from discovery's own dedup; an
    addition is also checked against the additions before it in the same plan.
    """
    reasons = []
    hosts = {h: str(s.get("id")) for s in sources if (h := host_key(s.get("url")))}
    ids = {str(s.get("id")) for s in sources}
    for a in plan.additions:
        new_id = str(a.source.get("id"))
        host = host_key(a.source.get("url"))
        if new_id in ids:
            reasons.append(
                f"new Source {sanitize_text(new_id, 80)} duplicates the id of an existing Source"
            )
        if host in hosts:
            reasons.append(
                f"new Source {sanitize_text(new_id, 80)} duplicates the host of Source "
                f"{sanitize_text(hosts[host], 80)}"
            )
        ids.add(new_id)
        if host:
            hosts.setdefault(host, new_id)
    return reasons


# Each breaker returns its hold reasons (empty when it passes).
_BREAKERS: tuple[Callable[[ReviewPlan, list[dict], list[dict]], list[str]], ...] = (
    _mass_retirement,
    _mass_addition,
    _retirement_of_non_enabled_source,
    _unproductive_retirement_with_yield,
    _addition_with_unsafe_url,
    _addition_duplicating_a_source,
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
    if plan.incomplete:
        prefix += f"\n> **Partial run:** {_one_line(plan.incomplete)}\n"
    sections = [prefix]
    if rows:
        sections.append(
            f"## Retired Sources ({len(rows)})\n\n"
            "Each stays in sources.yaml with `enabled: false`, `retired_at` and `retired_reason`; "
            "its Source health is cleared and its Resources are untouched. Revive one by "
            "setting `enabled: true` (and fixing its `url` if the feed moved).\n\n"
            + "\n".join(rows)
        )
    if plan.additions:
        sections.append(
            f"\n## Added Sources ({len(plan.additions)})\n\n"
            "Each passed its Trial through the Scout judge. The included entries are evidence "
            "only; Scout picks them up on its next run (`last_checked_at` is 6 months back).\n\n"
            + "\n".join(_addition_row(a) for a in plan.additions)
        )
    if plan.rejected:
        sections.append(
            f"\n## Rejected Prospective Sources ({len(plan.rejected)})\n\n"
            + "\n".join(_rejection_row(r) for r in plan.rejected)
        )
    closes = [a.prospect.suggestion for a in plan.additions] + [
        r.prospect.suggestion for r in plan.rejected if r.decided
    ]
    closes = [n for n in closes if type(n) is int]
    if closes:
        sections.append("\n" + "\n".join(f"Closes #{n}" for n in closes))
    return "\n".join(sections)


def _one_line(text: object) -> str:
    return " ".join(str(text).split())


def _url_span(url: object) -> str:
    """A url as an inert code span, or a placeholder when it is not safe to show.

    A safe url contains no backtick, whitespace or Markdown-significant bracket.
    """
    return f"`{url}`" if is_safe_url(url) else "(unsafe url withheld)"


def _channel(prospect: ProspectiveSource) -> str:
    if prospect.channel == "suggestion" and type(prospect.suggestion) is int:
        return f"Source suggestion #{prospect.suggestion}"
    return sanitize_text(prospect.channel, 40)


def _trial_line(trial: TrialEvidence) -> str:
    return (
        f"Trial: {trial.in_window} entries in the last {TRIAL_WINDOW_MONTHS} months, "
        f"{trial.judged} judged, {len(trial.included)} included"
    )


def _addition_row(a: Addition) -> str:
    lines = [
        f"- **{sanitize_text(a.source.get('id'), 120)}** — from {_channel(a.prospect)}",
        f"  Feed: {_url_span(a.source.get('url'))} ({sanitize_text(a.source.get('type'), 10)})",
        f"  {_trial_line(a.trial)}:",
    ]
    # Titles were sanitized by judge_sources; sanitizing again would double the escapes,
    # so only keep each on one line.
    lines += [f"  - {_one_line(title)} — {_url_span(url)}" for title, url in a.trial.included]
    return "\n".join(lines)


def _rejection_row(r: Rejection) -> str:
    url = _url_span(r.prospect.url) if r.prospect.url else "(no url)"
    row = f"- {_channel(r.prospect)}: {url} — `{sanitize_text(r.reason, 40)}`"
    if r.detail:
        row += f": {_one_line(r.detail)}"  # sanitized when the Rejection was made
    if r.trial is not None:
        row += f" ({_trial_line(r.trial)})"
    return row


# ── Entry point ──────────────────────────────────────────────────────────────


FETCH_TIMEOUT_SECONDS = 20  # per socket operation
FETCH_DEADLINE_SECONDS = 60  # for the whole response, redirects included
FETCH_MAX_BYTES = 2_000_000
FETCH_MAX_REDIRECTS = 5
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_ACCEPT = (
    "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.9, "
    "text/html;q=0.8, */*;q=0.5"
)


class FetchError(Exception):
    """A url Source discovery refused to fetch, or a response it refused to read."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface every redirect as an HTTPError so `http_fetch` can check its target first."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _check_target(url: str) -> None:
    """Refuse a url that is not a public web url (SSRF): `is_safe_url`, and every address
    its host resolves to must be globally routable. The resolver is asked again when
    urllib connects, so a DNS answer that changes in between is not caught; the inputs
    are the owner's suggestions and the pages they lead to, not arbitrary callers."""
    if not is_safe_url(url):
        raise FetchError("refused an unsafe url")
    host = urlsplit(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise FetchError(f"cannot resolve the host: {exc}") from exc
    for info in infos:
        address = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        if not address.is_global:
            raise FetchError("refused a host that resolves to a non-public address")


def http_fetch(url: str) -> tuple[int, dict, bytes]:
    """HTTP GET for Source discovery: (status, headers, body). The injected `fetch`.

    Follows up to FETCH_MAX_REDIRECTS redirects, checking the first url and every
    redirect target with `_check_target` before connecting. An HTTP error status is
    returned, not raised; a body over FETCH_MAX_BYTES, a response slower than
    FETCH_DEADLINE_SECONDS, a refused url and any network error raise.
    """
    deadline = time.monotonic() + FETCH_DEADLINE_SECONDS
    for _ in range(FETCH_MAX_REDIRECTS + 1):
        _check_target(url)
        request = urllib.request.Request(
            url, headers={"User-Agent": scout.USER_AGENT, "Accept": _ACCEPT}
        )
        try:
            with _OPENER.open(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
                chunks, size = [], 0
                while chunk := response.read(64 * 1024):
                    size += len(chunk)
                    if size > FETCH_MAX_BYTES:
                        raise FetchError(f"response larger than {FETCH_MAX_BYTES} bytes")
                    if time.monotonic() > deadline:
                        raise FetchError(f"response slower than {FETCH_DEADLINE_SECONDS}s")
                    chunks.append(chunk)
                return response.status, dict(response.headers), b"".join(chunks)
        except urllib.error.HTTPError as exc:
            headers = dict(exc.headers or {})
            exc.close()
            location = headers.get("Location") if exc.code in _REDIRECT_CODES else None
            if not location:
                return exc.code, headers, b""
            url = urljoin(url, location)
    raise FetchError(f"more than {FETCH_MAX_REDIRECTS} redirects")


def _plan_title(plan: ReviewPlan, today: date) -> str:
    def count(n: int, noun: str) -> str:
        return f"{n} {noun}{'s' if n != 1 else ''}"

    parts = []
    if plan.retirements:
        parts.append(count(len(plan.retirements), "retirement"))
    if plan.additions:
        parts.append(count(len(plan.additions), "addition"))
    decided = sum(r.decided for r in plan.rejected)
    if decided:
        parts.append(f"{decided} rejected")
    partial = " (partial run)" if plan.incomplete else ""
    return f"Source review — {today:%Y-%m} — {', '.join(parts)}{partial}"


def _load_suggestions(path: Path | None) -> list[dict]:
    """Source suggestions from the workflow's `gh api` issue list; none when absent."""
    if path is None:
        return []
    issues = json.loads(path.read_text())
    if not isinstance(issues, list):
        raise ValueError(f"{path} is not a JSON list of issues")
    return suggestions_from_issues(issues)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Source review: retire Dead and Unproductive Sources, add suggested ones"
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the plan and gate; write nothing")
    parser.add_argument(
        "--summary", type=Path, default=None, metavar="JSON",
        help="Write the PR title, body, labels and gate decision here (for the workflow)",
    )
    parser.add_argument(
        "--suggestions", type=Path, default=None, metavar="JSON",
        help="Open source-suggestion issues as the GitHub REST API lists them (from the workflow)",
    )
    args = parser.parse_args()

    ryaml = round_trip_yaml()
    with open(scout.SOURCES_PATH) as f:
        sources_data = ryaml.load(f)
    with open(scout.RESOURCES_PATH) as f:
        resources_data = ryaml.load(f)
    with open(scout.SEEN_PATH) as f:
        seen = ryaml.load(f)["seen"] or []
    sources = sources_data["sources"]
    resources = resources_data["resources"]
    suggestions = _load_suggestions(args.suggestions)

    # The unchanged Scout judge, with the system prompt Scout builds.
    system = scout.build_system_prompt(resources_data["sections"], resources)
    today = date.today()
    plan = review_sources(
        sources, resources, seen, suggestions, today,
        fetch=http_fetch, judge=functools.partial(judge.judge_entry, system),
    )
    decision = evaluate_source_review_automerge(plan, sources, resources)

    # Everything printed is sanitized: an id, url or error with a line break could
    # otherwise emit a `::` workflow command.
    enabled = sum(1 for s in sources if s.get("enabled", True))
    print(
        f"Reviewed {enabled} enabled Source(s) and {len(suggestions)} Source suggestion(s): "
        f"{len(plan.retirements)} to retire, {len(plan.additions)} to add, "
        f"{len(plan.rejected)} rejected."
    )
    for r in plan.retirements:
        print(f"  [{r.reason}] {sanitize_text(r.source_id, 120)}: {_evidence(r, today)}")
    for a in plan.additions:
        print(f"  [add] {sanitize_text(a.source['id'], 120)} from {_channel(a.prospect)}: "
              f"{_trial_line(a.trial)}")
    for r in plan.rejected:
        print(f"  [{sanitize_text(r.reason, 40)}] {_channel(r.prospect)}: "
              f"{sanitize_text(r.prospect.url, 200)} {_one_line(r.detail)}")
    if plan.incomplete:
        print(f"::warning::{_one_line(plan.incomplete)}", flush=True)
    print(json.dumps(decision._asdict()))

    if args.dry_run:
        print("Dry run — no files written.")
        return

    summary: dict = {"empty": plan.is_empty}
    if plan.is_empty:
        print("Nothing to add, retire or decide; no PR.")
    else:
        apply_plan(sources, plan, sources_data)
        with open(scout.SOURCES_PATH, "w") as f:
            ryaml.dump(sources_data, f)
        print(f"Updated {scout.SOURCES_PATH}")
        summary |= {
            "title": _plan_title(plan, today),
            "body": pr_body(plan, decision._asdict(), sources),
            **decision._asdict(),
        }
    if args.summary is not None:
        args.summary.write_text(json.dumps(summary))


if __name__ == "__main__":
    main()
