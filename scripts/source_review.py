#!/usr/bin/env python3
"""Source review: the monthly job that curates the Source list itself.

Source retirement retires Dead Sources, read from the Source health Scout records in
sources.yaml, and Unproductive Sources, read from the Yield and rejects Scout attributes
to each Source. Source discovery adds Prospective Sources that pass a Trial through the
Scout judge; its two channels, Source suggestions (GitHub issues) and Citation mining
(sites the guide's Resources repeatedly link to), feed the same pipeline.
"""

from __future__ import annotations

import argparse
import calendar
import http.client
import functools
import io
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urljoin, urlsplit

import feedparser
import idna

import judge
from judge import JudgeQuotaError
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
# One run's Trial budget. Each Trial is up to TRIAL_SIZE judge calls; the time budget
# keeps a run well inside the workflow's timeout even when the judge is slow.
MAX_TRIALS = 10
TRIAL_BUDGET_SECONDS = 35 * 60


class Fetched(NamedTuple):
    """An HTTP GET's result. `url` is the final url, after redirects."""

    status: int
    headers: dict
    body: bytes | str
    url: str


# `fetch(url)` -> Fetched (a plain (status, headers, body) is read as not redirected);
# `judge` is Scout's per-entry judge; `topic_fit(feed_title, feed_url, [(title, summary)])`
# is the Topic fit judge (`judge.judge_topic_fit`), returning `{topic_fit, rationale}`.
Fetch = Callable[[str], tuple]
Judge = Callable[[str, str, str, str], dict]
TopicFit = Callable[[str, str, list[tuple[str, str]]], dict]


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
    suggestion (`channel="suggestion"`, with its issue number) or Citation mining
    (`channel="citation"`, with the ids of the Resources citing it). `url` is untrusted
    until checked with `is_safe_url`.
    """

    url: str
    channel: str  # suggestion | citation
    suggestion: int | None = None  # the Source suggestion's issue number
    citations: tuple[str, ...] = ()  # ids of the Resources citing it (Citation mining)


class TrialInclude(NamedTuple):
    """An entry a Trial's judge included: its title and why, both sanitized judge text,
    and its url, which passed `is_safe_url`."""

    title: str
    url: str
    rationale: str


class TrialEvidence(NamedTuple):
    """What a Trial found: its entries in the window, the judge's Topic fit verdict on
    the feed as a whole, and its verdicts on the entries."""

    in_window: int  # dated entries in the 6-month window (the Trial judges up to 10)
    judged: int  # entries judged; 0 when the feed lacks Topic fit
    # Each entry judged `include`. Evidence only: never written to resources.yaml.
    included: tuple[TrialInclude, ...]
    topic_fit: bool | None = None  # None: the Trial stopped before a Topic fit verdict
    topic_fit_rationale: str = ""  # sanitized judge text


class Addition(NamedTuple):
    """A Prospective Source whose Trial passed, as the new sources.yaml entry it becomes."""

    source: dict  # the new Source entry
    prospect: ProspectiveSource
    trial: TrialEvidence


# Why a Prospective Source was rejected, as recorded in the Prospective Source memory.
REJECTION_REASONS = frozenset({
    "no-url", "unsafe-url", "duplicate", "unreachable", "no-feed", "no-recent-entries",
    "off-topic", "no-include",
})


class Rejection(NamedTuple):
    """A lasting verdict: a Prospective Source that is not added, and why.

    Recorded in the Prospective Source memory; a suggestion's issue is closed.
    """

    prospect: ProspectiveSource
    reason: str  # one of REJECTION_REASONS
    detail: str = ""  # sanitized when made
    trial: TrialEvidence | None = None


class Untried(NamedTuple):
    """A Prospective Source this run could not decide: tried again next run.

    Not recorded anywhere; a suggestion's issue stays open.
    """

    prospect: ProspectiveSource
    reason: str  # fetch-failed | judge-error | usage-limit | time-budget
    detail: str = ""  # sanitized when made
    trial: TrialEvidence | None = None


@dataclass
class ReviewPlan:
    """Everything one Source review run would change on the Source list."""

    today: date  # the review date; retirements are stamped with it
    retirements: list[Retirement] = field(default_factory=list)
    additions: list[Addition] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)  # lasting verdicts
    untried: list[Untried] = field(default_factory=list)  # transient: tried again next run
    incomplete: str | None = None  # why Trials stopped early; sanitized
    # Set when a judge error stopped Trials: what to do about it (the run must go red).
    judge_failure: str | None = None
    auth_failed: bool = False
    mining: MiningStats | None = None  # what Citation mining did, for the run log

    @property
    def is_empty(self) -> bool:
        """Nothing to add, retire or reject: the run opens no PR.

        A rejection counts: its PR records it, which is what later closes its suggestion
        issue (`reconcile_suggestions`).
        """
        return not self.retirements and not self.additions and not self.rejected


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

class Platform(NamedTuple):
    """How a host that serves many people's content is divided into Sources and sites."""

    # Path-tenanted: each Source is identified by this many first path segments
    # (github.com/<owner>/<repo>, medium.com/<author>); 0 when the host is one Source.
    depth: int = 0
    # Each tenant's feed is served at /feed/<tenant>, so a leading `feed` segment is skipped.
    feed_prefix: bool = False
    # First path segments that are the platform's own pages, not a tenant.
    non_tenant: frozenset[str] = frozenset()
    # A shared parent: each subdomain (alice.github.io) belongs to a different person.
    shared_parent: bool = False
    # Only links under this path cite the host's own posts (huggingface.co/blog); its
    # other pages (models, datasets) are not citations. The Trial starts there too.
    blog_path: str | None = None


_GITHUB_PAGES = frozenset({
    "about", "account", "apps", "codespaces", "collections", "contact", "copilot",
    "customer-stories", "enterprise", "events", "explore", "features", "git-guides", "login",
    "marketplace", "new", "notifications", "open-source", "orgs", "partners", "pricing",
    "readme", "resources", "search", "security", "settings", "signup", "site", "solutions",
    "sponsors", "team", "topics", "trending", "users",
})
_SHARED = Platform(shared_parent=True)
# Hosts that serve many people's content. Every other host is one Source and one site.
PLATFORMS: dict[str, Platform] = {
    "github.com": Platform(2, non_tenant=_GITHUB_PAGES),
    "gitlab.com": Platform(2, non_tenant=frozenset({
        "-", "dashboard", "explore", "groups", "help", "projects", "search", "users",
    })),
    "medium.com": Platform(1, feed_prefix=True, shared_parent=True, non_tenant=frozenset({
        "about", "m", "me", "membership", "plans", "policy", "search", "tag", "topics",
    })),
    "dev.to": Platform(1, feed_prefix=True, non_tenant=frozenset({
        "about", "enter", "latest", "search", "settings", "t", "tags", "top",
    })),
    "feeds.feedburner.com": Platform(1),
    "huggingface.co": Platform(blog_path="/blog"),
    "cloud.google.com": Platform(blog_path="/blog"),
    **{host: _SHARED for host in (
        "github.io", "gitlab.io", "substack.com", "ghost.io", "notion.site", "micro.blog",
        "tumblr.com", "beehiiv.com", "readthedocs.io", "webflow.io", "wordpress.com",
        "blogspot.com", "hashnode.dev", "bearblog.dev", "netlify.app", "vercel.app",
        "pages.dev",
    )},
}
# Path-tenanted hosts and how many path segments identify a tenant.
PATH_TENANTED_HOSTS = {host: p.depth for host, p in PLATFORMS.items() if p.depth}


def source_key(url: object) -> str | None:
    """What makes two feeds the same Source: the host, IDNA-normalised, lower-case and
    `www.`-stripped, plus for a path-tenanted host (`PLATFORMS`) its first lower-case
    path segments. None when `url` has no parseable host.

    Used by discovery's dedup, the gate's duplicate breaker, new Source ids, and the
    Prospective Source memory, so they can never disagree.
    """
    try:
        parts = urlsplit(str(url))
        host = parts.hostname or ""
        host = idna.encode(host, uts46=True).decode("ascii") if host else ""
    except (ValueError, idna.IDNAError, UnicodeError):
        return None
    host = host.rstrip(".").lower().removeprefix("www.")
    if not host:
        return None
    platform = PLATFORMS.get(host, Platform())
    if not platform.depth:
        return host
    segments = [s.lower() for s in parts.path.split("/") if s]
    if platform.feed_prefix and segments[:1] == ["feed"]:
        segments = segments[1:]
    return "/".join([host, *segments[:platform.depth]])


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

    Relative hrefs are resolved against `page_url` (the page's final url, after
    redirects). Returned in page order and unvalidated: the caller checks each with
    `is_safe_url`. Never raises on bad HTML.
    """
    parser = _FeedLinkParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — hostile markup must not crash the review
        pass
    return [urljoin(page_url, href) for href in parser.hrefs]


_GITHUB_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _github_releases_feed(url: str) -> str | None:
    """`https://github.com/<owner>/<repo>/releases.atom` for any github.com repo url.

    A repo page advertises no feed, so a suggested repo is mapped to its releases
    feed before feed resolution. None for any other url.
    """
    parts = urlsplit(url)
    if (parts.hostname or "").lower().removeprefix("www.") != "github.com":
        return None
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) < 2 or not all(
        _GITHUB_NAME_RE.fullmatch(s) and s not in (".", "..") for s in segments[:2]
    ):
        return None
    owner, repo = segments[0], segments[1].removesuffix(".git")
    return f"https://github.com/{owner}/{repo}/releases.atom"


def _text(body: object) -> str:
    return body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)


def _parse_feed(fetched: Fetched):
    """Parse a fetched body as a feed. Never pass feedparser a str or bytes: it would
    treat one that looks like a url or a path as something to fetch or open."""
    body = fetched.body
    raw = body if isinstance(body, bytes) else str(body).encode("utf-8")
    parsed = feedparser.parse(io.BytesIO(raw))
    parsed["status"] = fetched.status
    return parsed


def _get(fetch: Fetch, prospect: ProspectiveSource, url: str) -> Fetched | Untried | Rejection:
    """Fetch `url`: the response, or why not.

    Lasting (a Rejection): the final url after redirects is not a safe public url
    (`unsafe-url`), or a 4xx status other than TRANSIENT_CLIENT_ERRORS
    (`unreachable`: gone, forbidden, not found). Transient (`fetch-failed`, Untried,
    retried next run): a 5xx, 408, 425 or 429 status, or any network error or
    refusal raised by `fetch`.
    """
    try:
        got = fetch(url)
    except Exception as exc:  # noqa: BLE001 — any fetch failure is transient, never a crash
        return Untried(prospect, "fetch-failed", sanitize_text(str(exc), 200))
    got = Fetched(*got) if len(got) == 4 else Fetched(*got, url)
    if not is_safe_url(got.url):
        return Rejection(prospect, "unsafe-url", "redirected to an unsafe url")
    status = int(got.status)
    if 400 <= status < 500 and status not in TRANSIENT_CLIENT_ERRORS:
        return Rejection(prospect, "unreachable", f"HTTP status {status}")
    if status >= 400:
        return Untried(prospect, "fetch-failed", f"HTTP status {status}")
    return got


class _Resolved(NamedTuple):
    page_url: str  # the Prospective Source's url after redirects
    feed_url: str  # the feed's url after redirects: the new Source's `url`
    parsed: object  # the parsed feed
    type: str  # rss | atom | github-releases


def _resolve_feed(prospect: ProspectiveSource, fetch: Fetch) -> _Resolved | Untried | Rejection:
    """Find the Prospective Source's feed: the url itself if it is a feed (a github.com
    repo url is first mapped to its releases feed), else the first safe RSS/Atom feed
    its page advertises."""
    releases = _github_releases_feed(prospect.url)
    page = _get(fetch, prospect, releases or prospect.url)
    if not isinstance(page, Fetched):
        return page
    parsed = _parse_feed(page)
    if parsed.get("version"):
        kind = "github-releases" if releases else _feed_type(parsed)
        return _Resolved(page.url, page.url, parsed, kind)
    if releases:
        return Rejection(prospect, "no-feed", "the repo's releases feed is not a feed")
    links = discover_feed_links(_text(page.body), page.url)
    safe = [link for link in links if is_safe_url(link)]
    if not safe:
        return Rejection(prospect, "unsafe-url" if links else "no-feed")
    feed = _get(fetch, prospect, safe[0])
    if not isinstance(feed, Fetched):
        return feed
    parsed = _parse_feed(feed)
    if not parsed.get("version"):
        return Rejection(prospect, "no-feed", "the advertised feed is not RSS or Atom")
    return _Resolved(page.url, feed.url, parsed, _feed_type(parsed))


def _feed_type(parsed) -> str:
    return "atom" if str(parsed.get("version", "")).startswith("atom") else "rss"


class _TrialStop(Exception):
    """The judge stopped Trials (Scout's semantics): what to record, and why."""

    def __init__(self, untried: Untried, note: str, judge_failure: str | None = None,
                 auth_failed: bool = False) -> None:
        self.untried = untried
        self.note = note
        self.judge_failure = judge_failure
        self.auth_failed = auth_failed


class _Budget:
    """The run's Trial budget: at most MAX_TRIALS Trials and TRIAL_BUDGET_SECONDS of
    wall clock. Checked before each Trial and before each judge call."""

    TIME = f"the run's time budget of {TRIAL_BUDGET_SECONDS // 60} minutes"

    def __init__(self, clock: Callable[[], float]) -> None:
        self.clock = clock
        self.start = clock()
        self.trials = 0
        self.ran_out = False  # set when the budgeted judge refused a call

    def out_of_time(self) -> bool:
        return self.clock() - self.start >= TRIAL_BUDGET_SECONDS

    def spent(self) -> str | None:
        """Why no further Trial may start, or None."""
        if self.trials >= MAX_TRIALS:
            return f"the run's cap of {MAX_TRIALS} Trials"
        return self.TIME if self.out_of_time() else None


class _BudgetSpent(JudgeQuotaError):
    """Raised by a budgeted judge; a Trial stops on it as on a usage limit."""


def _topic_fit_run(
    topic_fit: TopicFit, parsed, feed_url: str, sample: list, source_id: str,
) -> tuple[scout.ScoutRun, dict | None]:
    """Make the Trial's one Topic fit call over its sample, recording a failure in a
    ScoutRun exactly as `scout.judge_sources` records one on an entry call
    (`scout.record_judge_failure`). Returns the run and the verdict (None on a failure)."""
    run = scout.ScoutRun()
    feed_title = str(parsed.get("feed", {}).get("title", ""))
    try:
        verdict = topic_fit(feed_title, feed_url, [scout.entry_text(e) for e in sample])
    except Exception as exc:
        scout.record_judge_failure(
            run, exc, f"source {source_id}: Topic fit judge error for {sanitize_text(feed_url, 200)}",
        )
        return run, None
    return run, verdict


def _stop_on_judge_failure(
    prospect: ProspectiveSource, run: scout.ScoutRun, evidence: TrialEvidence, budget: _Budget,
) -> None:
    """Raise `_TrialStop` if the judge stopped (Scout's semantics): a spent time budget,
    a usage limit or a judge error, whichever call of the Trial it came on."""
    if run.stopped_early is not None:
        if budget.ran_out:
            raise _TrialStop(
                Untried(prospect, "time-budget", trial=evidence),
                f"Trials stopped at {budget.TIME}; the remaining Prospective Sources "
                "are tried next run.",
            )
        raise _TrialStop(
            Untried(prospect, "usage-limit", trial=evidence),
            f"Trials stopped on {sanitize_text(run.stopped_early, 300)}; the remaining "
            "Prospective Sources are tried next run.",
        )
    if run.errors:
        hint = scout.judge_failure_hint(run)
        raise _TrialStop(
            # Sanitized when it was recorded.
            Untried(prospect, "judge-error", run.errors[0], evidence),
            f"Trials stopped on a judge error; the remaining Prospective Sources are tried "
            f"next run. {hint}",
            judge_failure=hint,
            auth_failed=run.auth_failed,
        )


def _trial(
    prospect: ProspectiveSource, parsed, feed_url: str, source_id: str, today: date,
    judge: Judge, topic_fit: TopicFit, budget: _Budget,
) -> TrialEvidence | Rejection | Untried:
    """Judge the feed's Topic fit, then up to TRIAL_SIZE of its entries from the last 6
    months, newest first.

    Pass iff the feed fetched cleanly (Scout's `read_source` rule), has an entry in the
    window, has Topic fit, and the judge includes at least one entry. Topic fit is one
    judge call over the same sample, made first: without it the Prospective Source is
    rejected as `off-topic` and no entry is judged. Raises `_TrialStop` when the judge
    fails or the run's time budget runs out mid-Trial, on either kind of call.
    """
    window_start = _months_before(today, TRIAL_WINDOW_MONTHS)
    read = scout.read_source({"id": source_id, "last_checked_at": window_start}, parsed, today)
    if read.health.consecutive_failures:
        status = read.health.last_http_status
        return Untried(prospect, "fetch-failed", f"HTTP status {status}" if status else "unparseable feed")
    in_window = sorted(
        (e for e in read.new_entries if scout.entry_date(e) <= today),
        key=scout.entry_date,
        reverse=True,
    )
    if not in_window:
        return Rejection(prospect, "no-recent-entries")

    def budgeted(call):
        def checked(*args):
            if budget.out_of_time():
                budget.ran_out = True
                raise _BudgetSpent(budget.TIME)
            return call(*args)
        return checked

    budget.trials += 1
    sample = in_window[:TRIAL_SIZE]
    fit_run, verdict = _topic_fit_run(budgeted(topic_fit), parsed, feed_url, sample, source_id)
    _stop_on_judge_failure(prospect, fit_run, TrialEvidence(len(in_window), 0, ()), budget)
    if verdict is None:  # a failed call records a stop, so this never passes silently
        raise RuntimeError("Topic fit call returned no verdict and recorded no failure")
    # Fail closed: only a JSON `true` is Topic fit.
    fit = verdict.get("topic_fit") is True
    fit_rationale = sanitize_text(verdict.get("rationale", ""))
    if not fit:
        return Rejection(prospect, "off-topic",
                         trial=TrialEvidence(len(in_window), 0, (), False, fit_rationale))
    run = scout.judge_sources({source_id: sample}, budgeted(judge), set(), set())
    evidence = TrialEvidence(
        len(in_window), run.evaluated,
        tuple(TrialInclude(c["title"], c["url"], c["rationale"]) for c in run.candidates),
        True, fit_rationale,
    )
    _stop_on_judge_failure(prospect, run, evidence, budget)
    if not evidence.included:
        return Rejection(prospect, "no-include", trial=evidence)
    return evidence


def _source_id(key: str, taken: set[str]) -> str:
    """A safe, unique Source id from a Source key: `[a-z0-9-]` only, at most 60 chars."""
    base = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")[:60].strip("-")
    return scout.safe_slug(base or "source", taken)


def _new_source(
    source_id: str, resolved: _Resolved, prospect: ProspectiveSource, today: date
) -> dict:
    channel = (
        f"Source suggestion #{prospect.suggestion}" if prospect.channel == "suggestion"
        else "Citation mining"
    )
    return {
        "id": source_id,
        "type": resolved.type,
        "url": resolved.feed_url,
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


# The `notes` `_new_source` writes on a Source added from a Source suggestion.
_ADDED_FROM_SUGGESTION_RE = re.compile(r"^Added by Source review from Source suggestion #(\d+)$")


def reconcile_suggestions(
    suggestions: list[dict], sources: list[dict], memory: Iterable[dict]
) -> tuple[list[dict], list[dict]]:
    """Split open Source suggestions into those still to Trial and those to close.

    A Source review PR is opened and auto-merged with the workflow's GITHUB_TOKEN, so
    its `Closes #n` lines are best effort only (#136). The decision itself is on main
    once the PR merges: an Addition as a Source whose `notes` name the suggestion, a
    Rejection as a Prospective Source memory entry with its issue number. A suggestion
    still open with such a record is decided: it is closed and never Trialled again
    (to ask again, file a new suggestion). Returns `(undecided, closes)`, where each
    close is `{"number": int, "comment": str}`; the comment names only a sanitized
    Source id or rejection reason.
    """
    added: dict[int, str] = {}
    for source in sources:
        match = _ADDED_FROM_SUGGESTION_RE.match(str(source.get("notes") or ""))
        if match:
            added.setdefault(int(match.group(1)), str(source.get("id")))
    rejected: dict[int, str] = {}
    for entry in memory:
        number = entry.get("suggestion")
        if entry.get("channel") == "suggestion" and type(number) is int:
            rejected.setdefault(number, str(entry.get("reason")))
    undecided, closes = [], []
    for suggestion in suggestions:
        number = suggestion["number"]
        if number in added:
            closes.append({
                "number": number,
                "comment": f"Source review added this Source suggestion as "
                           f"`{sanitize_text(added[number], 120)}` in sources.yaml; closing it.",
            })
        elif number in rejected:
            closes.append({
                "number": number,
                "comment": f"Source review rejected this Source suggestion "
                           f"(`{sanitize_text(rejected[number], 40)}`, recorded in "
                           "scout/prospects.yaml); closing it. File a new suggestion to ask again.",
            })
        else:
            undecided.append(suggestion)
    return undecided, closes


def _suggested_prospects(suggestions: list[dict]) -> list[ProspectiveSource]:
    """Source suggestions as Prospective Sources. The workflow already kept only the
    owner's and collaborators' (see `suggestions_from_issues`); a suggestion with no
    url keeps an empty one and is rejected `no-url`."""
    return [
        ProspectiveSource(str(s.get("url") or ""), "suggestion", suggestion=int(s["number"]))
        for s in suggestions
    ]


def discover_sources(
    plan: ReviewPlan,
    prospects: list[ProspectiveSource],
    sources: list[dict],
    fetch: Fetch,
    judge: Judge,
    memory: Iterable[dict] = (),
    clock: Callable[[], float] = time.monotonic,
    *,
    topic_fit: TopicFit,
) -> None:
    """Source discovery's shared pipeline: Trial each Prospective Source in turn, adding
    passes to `plan` and recording rejections. Every channel feeds it, and every
    Trial judges Topic fit with `topic_fit` before any entry (see `_trial`).

    - No url → `no-url`; a url failing `is_safe_url` → `unsafe-url`.
    - A Prospective Source not from a Source suggestion whose Source key is in `memory`
      (lasting rejections from recent runs, see `update_prospect_memory`) is skipped
      silently. A suggestion is always tried: the owner asked again.
    - Same Source key (`source_key`) as any Source on the list, enabled or Retired, or
      as anything already considered this run → `duplicate`. Checked on the url, and
      again on the page's and feed's final urls after resolution.
    - A 4xx status (but 408, 425, 429) is lasting (`unreachable`); any other fetch
      failure is transient (`plan.untried`, retried next run). See `_get`.
    - The Trial runs within the run's budget (MAX_TRIALS, TRIAL_BUDGET_SECONDS on
      `clock`); a spent budget, a usage limit or a judge error stops all further
      Trials and sets `plan.incomplete`, and a judge error also `plan.judge_failure`.
      What was decided before stands.
    """
    remembered = {m.get("key") for m in memory if m.get("key")}
    taken = {k for k in (source_key(s.get("url")) for s in sources) if k}
    considered: set[str] = set()  # Source keys added, rejected or tried this run
    ids = {str(s.get("id")) for s in sources}
    budget = _Budget(clock)

    def duplicate_of(*urls: str, own: str | None = None) -> str | None:
        for url in urls:
            k = source_key(url)
            if k in taken:
                return "same Source as one on the list"
            if k in considered and k != own:
                return "already considered this run"
        return None

    for prospect in prospects:
        if not prospect.url:
            plan.rejected.append(Rejection(prospect, "no-url"))
            continue
        if not is_safe_url(prospect.url):
            plan.rejected.append(Rejection(prospect, "unsafe-url"))
            continue
        key = source_key(prospect.url)
        if prospect.channel != "suggestion" and key in remembered:
            continue
        if why := duplicate_of(prospect.url):
            plan.rejected.append(Rejection(prospect, "duplicate", why))
            continue
        if why := budget.spent():
            plan.incomplete = f"Trials stopped at {why}; the remaining Prospective Sources are tried next run."
            return
        considered.add(key)
        resolved = _resolve_feed(prospect, fetch)
        if isinstance(resolved, Untried):
            plan.untried.append(resolved)
            continue
        if isinstance(resolved, Rejection):
            plan.rejected.append(resolved)
            continue
        if why := duplicate_of(resolved.page_url, resolved.feed_url, own=key):
            plan.rejected.append(Rejection(prospect, "duplicate", f"after redirects: {why}"))
            continue
        feed_key = source_key(resolved.feed_url)
        considered.update({source_key(resolved.page_url), feed_key})
        source_id = _source_id(feed_key, ids)
        try:
            result = _trial(prospect, resolved.parsed, resolved.feed_url, source_id, plan.today,
                            judge, topic_fit, budget)
        except _TrialStop as stop:
            plan.untried.append(stop.untried)
            plan.incomplete = stop.note
            plan.judge_failure = stop.judge_failure
            plan.auth_failed = stop.auth_failed
            return
        if isinstance(result, (Rejection, Untried)):
            (plan.rejected if isinstance(result, Rejection) else plan.untried).append(result)
            continue
        plan.additions.append(Addition(_new_source(source_id, resolved, prospect, plan.today), prospect, result))
        ids.add(source_id)
        taken.add(feed_key)


# ── Citation mining ──────────────────────────────────────────────────────────

# A site is a Prospective Source once Resources on at least this many different sites
# (`_site`) cite it. Several Resources on one site are one voice, usually its template
# (a sidebar, an "edit this page" link). Pages are read once per url, so two sites
# always means two distinct Resources.
MIN_CITING_SITES = 2
# One run reads at most this many Resource pages, within this much wall clock (each
# fetch is bounded by FETCH_DEADLINE_SECONDS). The guide has ~150 Resources.
MAX_CITATION_FETCHES = 200
CITATION_BUDGET_SECONDS = 20 * 60
# The guide itself: its own repo is never one of its Sources.
GUIDE_REPO_KEY = "github.com/jdg2896/agentic-engineering"
# Hosts (and all their subdomains) that cannot be a Source: a citation of them points
# at one post, video, paper, package, product or page, not at an author's stream of
# posts. A Resource hosted on one is not read either: its links are the platform's.
NON_SOURCE_HOSTS = frozenset({
    # Social media, forums, chat and events
    "x.com", "twitter.com", "linkedin.com", "facebook.com", "instagram.com", "threads.net",
    "threads.com", "bsky.app", "mastodon.social", "fosstodon.org", "hachyderm.io",
    "youtube.com", "youtu.be", "vimeo.com", "tiktok.com", "reddit.com",
    "news.ycombinator.com", "lobste.rs", "discord.com", "discord.gg", "t.me", "slack.com",
    "stackoverflow.com", "stackexchange.com", "lu.ma", "luma.com", "meetup.com",
    "eventbrite.com", "maven.com",
    # Link shorteners
    "t.co", "bit.ly", "goo.gl", "goo.gle", "aka.ms", "tinyurl.com", "ow.ly", "buff.ly",
    "lnkd.in", "dub.sh", "shorturl.at", "rebrand.ly", "t.mp",
    # Code hosts' non-feed pages (a github.com / gitlab.com repo is a releases feed)
    "gist.github.com", "raw.githubusercontent.com", "githubusercontent.com",
    "bitbucket.org", "deepwiki.com",
    # Package registries, paper archives and indexes, reference, legal and file hosts
    "pypi.org", "pypi.python.org", "npmjs.com", "crates.io", "pkg.go.dev", "pepy.tech",
    "arxiv.org", "alphaxiv.org", "doi.org", "openreview.net", "aclanthology.org",
    "semanticscholar.org", "wikipedia.org", "archive.org", "creativecommons.org",
    "substackcdn.com", "website-files.com",
    # Google's products, not its blogs (blog.google, research.google, cloud.google.com/blog)
    "scholar.google.com", "policies.google.com", "play.google.com", "docs.google.com",
    "drive.google.com", "colab.research.google.com", "maps.google.com", "groups.google.com",
    "sites.google.com", "accounts.google.com", "support.google.com",
})
# Subdomains that serve a product, status page, docs or assets, not posts
# (docs.example.com, status.example.com, ...).
NON_SOURCE_FIRST_LABELS = frozenset({
    "docs", "doc", "api", "help", "support", "status", "trust", "console", "platform",
    "app", "dash", "dashboard", "login", "accounts", "auth", "cdn", "static", "assets",
    "community", "forum", "forums", "discuss", "chat", "events", "academy", "learn",
    "careers", "jobs", "shop", "store", "play", "pages", "go", "join",
})
# A page citing more sites than this is a link directory (an awesome-list), not an
# article: its links are listings, not citations, so it is not read for them.
MAX_SITES_PER_PAGE = 60
_COUNTRY_SECOND_LEVELS = frozenset({"co", "com", "org", "net", "ac", "gov", "edu"})


def _site(key: str) -> str:
    """Which site a Source key belongs to, near enough, without a public-suffix list:
    the whole key on a path-tenanted host (each tenant is its own site); else the
    host's last two labels (`blog.example.com` → `example.com`), or three under a
    country code's second level (`bbc.co.uk`) or a shared parent (`me.github.io`)."""
    host = key.partition("/")[0]
    if host in PATH_TENANTED_HOSTS:
        return key
    labels = host.split(".")
    parent = PLATFORMS.get(".".join(labels[-2:]), Platform())
    n = 2
    if len(labels) >= 3 and (
        (len(labels[-1]) == 2 and labels[-2] in _COUNTRY_SECOND_LEVELS) or parent.shared_parent
    ):
        n = 3
    return ".".join(labels[-n:])


def _on_platform(key: str) -> bool:
    """True for a Source key on a NON_SOURCE_HOSTS host or one of its subdomains."""
    host = key.partition("/")[0]
    return any(host == h or host.endswith("." + h) for h in NON_SOURCE_HOSTS)


def _citable(key: str) -> bool:
    """False for a Source key that cannot be a Source: a NON_SOURCE_HOSTS host or
    subdomain, a reference-docs host, the guide's own repo, or a path-tenanted host
    without a whole tenant (a github.com profile or site page; a repo is its releases
    feed)."""
    host, _, path = key.partition("/")
    if _on_platform(key):
        return False
    if host.count(".") >= 2 and host.split(".", 1)[0] in NON_SOURCE_FIRST_LABELS:
        return False
    if key == GUIDE_REPO_KEY:
        return False
    platform = PLATFORMS.get(host, Platform())
    segments = path.split("/") if path else []
    return not platform.depth or (
        len(segments) == platform.depth and segments[0] not in platform.non_tenant
    )


def _cites_posts(link: str, key: str) -> bool:
    """False for a link to a platform page that is not one of the host's posts: on a
    host with a `blog_path` (huggingface.co), only links under it count."""
    blog = PLATFORMS.get(key, Platform()).blog_path
    if blog is None:
        return True
    path = urlsplit(link).path
    return path == blog or path.startswith(blog + "/")


_CHROME_TAGS = frozenset({"nav", "header", "footer", "aside"})
_CONTENT_TAGS = frozenset({"main", "article"})


class _AnchorParser(HTMLParser):
    """Collects every `<a href>`, noting whether it lies in the page chrome (nav,
    header, footer, aside) and whether in the content (a `<main>` or `<article>` that
    is not itself inside the chrome)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[tuple[str, bool, bool]] = []  # (href, in chrome, in content)
        self.chrome = 0  # open chrome elements
        self.content = 0  # open content elements outside the chrome
        self.opened: list[bool] = []  # per open content element: did it count?

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _CHROME_TAGS:
            self.chrome += 1
        elif tag in _CONTENT_TAGS:
            counts = not self.chrome
            self.opened.append(counts)
            self.content += counts
        elif tag in ("a", "area"):
            href = next((v for k, v in attrs if k.lower() == "href" and v), "")
            if href.strip():
                self.hrefs.append((href.strip(), self.chrome > 0, self.content > 0))

    def handle_endtag(self, tag: str) -> None:
        if tag in _CHROME_TAGS and self.chrome:
            self.chrome -= 1
        elif tag in _CONTENT_TAGS and self.opened:
            self.content -= self.opened.pop()


def _outbound_links(page: Fetched) -> list[str]:
    """The `<a href>`s of a fetched page's own text, resolved against its final url.

    Links in its nav, header, footer or aside are the site's template, not citations;
    and when the page marks its content (`<main>` or `<article>` outside the chrome)
    and that content has links, only those count. If a chrome element is never
    closed, where the chrome ends cannot be told, so every link on the page counts
    rather than none. Unvalidated: the caller checks each with `is_safe_url`. Never
    raises on bad HTML.
    """
    parser = _AnchorParser()
    try:
        parser.feed(_text(page.body))
        parser.close()
    except Exception:  # noqa: BLE001 — hostile markup must not crash the review
        pass
    if parser.chrome:
        chosen = [href for href, _, _ in parser.hrefs]
    else:
        own = [(href, in_content) for href, in_chrome, in_content in parser.hrefs if not in_chrome]
        content = [href for href, in_content in own if in_content]
        chosen = content or [href for href, _ in own]
    links = []
    for href in chosen:
        try:
            links.append(urljoin(page.url, href))
        except ValueError:
            continue
    return links


def _read_page(fetch: Fetch, url: str) -> Fetched | None:
    """A Resource's page, or None when it cannot be read for links: the fetch raised,
    the status is not 2xx, the final url is not safe, or the body is not HTML."""
    try:
        got = fetch(url)
    except Exception:  # noqa: BLE001 — a page that fails to fetch is skipped, never fatal
        return None
    got = Fetched(*got) if len(got) == 4 else Fetched(*got, url)
    if not 200 <= int(got.status) < 300 or not is_safe_url(got.url):
        return None
    kind = next((str(v) for k, v in (got.headers or {}).items() if str(k).lower() == "content-type"), "")
    if kind and "html" not in kind.lower():
        return None
    return got


def _minable(resource: dict) -> bool:
    """A live Resource worth reading: a safe url, not on a NON_SOURCE_HOSTS platform
    (an arXiv abstract's or a video page's links are the platform's), and not archived,
    quarantined or hidden (a hidden entry repeats another Resource's page)."""
    url = resource.get("url")
    return (
        is_safe_url(url)
        and not _on_platform(source_key(url) or "")
        and not resource.get("archived")
        and not resource.get("quarantined_at")
        and not resource.get("hidden")
    )


class _PageCitations(NamedTuple):
    keys: dict[str, bool]  # Source key -> first cited with `www.`
    links: int  # links the page had, before any filtering
    directory: bool  # skipped as a link directory


def _page_citations(page: Fetched, resource_url: str, excluded: set[str]) -> _PageCitations:
    """The Source keys a Resource's page cites. Links failing `is_safe_url`, to the
    Resource's own site (before or after redirects), to a key in `excluded`, to a key
    that is not `_citable`, or to a platform page that is not a post (`_cites_posts`)
    do not count; a link directory (more than MAX_SITES_PER_PAGE keys) cites nothing."""
    own = {_site(k) for k in (source_key(resource_url), source_key(page.url)) if k}
    links = _outbound_links(page)
    found: dict[str, bool] = {}
    for link in links:
        if not is_safe_url(link):
            continue
        key = source_key(link)
        if (
            key is None or _site(key) in own or key in excluded or not _citable(key)
            or not _cites_posts(link, key)
        ):
            continue
        found.setdefault(key, (urlsplit(link).hostname or "").lower().startswith("www."))
    if len(found) > MAX_SITES_PER_PAGE:
        return _PageCitations({}, len(links), True)
    return _PageCitations(found, len(links), False)


class MiningStats(NamedTuple):
    """What one run's Citation mining did, for the run log: a mining regression (a
    parser that finds nothing, a fetch that always fails) shows up here."""

    pages_read: int  # Resource pages fetched
    pages_failed: int  # of which could not be read (fetch error, non-2xx, not HTML)
    link_directories: int  # skipped as link directories
    no_links: int  # read, but parsed to no links at all
    prospects: int  # Prospective Sources mined


def mining_summary(stats: MiningStats) -> str:
    """One log line for the run's Citation mining."""
    return (
        f"Citation mining read {stats.pages_read} Resource page(s): {stats.pages_failed} failed, "
        f"{stats.link_directories} skipped as link directories, {stats.no_links} with no "
        f"links; {stats.prospects} Prospective Source(s)."
    )


def _prospect_url(key: str, www: bool) -> str:
    """Where autodiscovery can find a mined site's feed: the blog path on a host with
    one, the tenant on a path-tenanted host, else the site root; over https and with
    `www.` if it was first cited so."""
    host = f"{'www.' if www else ''}{key}"
    blog = PLATFORMS.get(key, Platform()).blog_path
    if blog is not None:
        return f"https://{host}{blog}"
    return f"https://{host}" if "/" in key else f"https://{host}/"


def _mine_citations(
    resources: list[dict], excluded: set[str], fetch: Fetch, clock: Callable[[], float]
) -> tuple[list[ProspectiveSource], str | None, MiningStats]:
    """Citation mining: the sites the Resources' pages repeatedly link to, as Prospective
    Sources; why mining stopped early (None when it read every page); and its stats.

    Reads each live Resource's page (`_minable`; a page shared by two Resources is
    read, and counts, once), in resources.yaml order, within MAX_CITATION_FETCHES and
    CITATION_BUDGET_SECONDS; a page that cannot be read is skipped. The Source keys it
    cites are those of `_page_citations`. A key cited by Resources on at least
    MIN_CITING_SITES different sites (`_site`) is a Prospective Source, at
    `_prospect_url`, carrying the ids of every Resource citing it as evidence. Ordered
    by citing sites, then citing Resources (most first), then Source key, so the Trial
    cap takes the best-evidenced first and runs are reproducible.
    """
    start = clock()
    citing: dict[str, list[str]] = {}  # Source key -> ids of the Resources citing it (evidence)
    sites: dict[str, set[str]] = {}  # Source key -> the sites of the Resources citing it
    www: dict[str, bool] = {}  # Source key -> first cited with `www.`
    read: set[str] = set()
    failed = directories = no_links = 0
    stopped = None
    for resource in resources:
        if not _minable(resource) or resource["url"] in read:
            continue
        if len(read) >= MAX_CITATION_FETCHES:
            stopped = f"the run's cap of {MAX_CITATION_FETCHES} Resource page fetches"
            break
        if clock() - start >= CITATION_BUDGET_SECONDS:
            stopped = f"its time budget of {CITATION_BUDGET_SECONDS // 60} minutes"
            break
        read.add(resource["url"])
        page = _read_page(fetch, resource["url"])
        if page is None:
            failed += 1
            continue
        cited = _page_citations(page, resource["url"], excluded)
        directories += cited.directory
        no_links += not cited.links
        resource_id = str(resource.get("id"))
        resource_site = _site(source_key(resource["url"]) or "")
        for key, with_www in cited.keys.items():
            ids = citing.setdefault(key, [])
            if resource_id not in ids:
                ids.append(resource_id)
            sites.setdefault(key, set()).add(resource_site)
            www.setdefault(key, with_www)
    mined = sorted(
        (k for k in citing if len(sites[k]) >= MIN_CITING_SITES),
        key=lambda k: (-len(sites[k]), -len(citing[k]), k),
    )
    prospects = [
        ProspectiveSource(_prospect_url(k, www[k]), "citation", citations=tuple(citing[k]))
        for k in mined
    ]
    note = (
        f"Citation mining stopped at {stopped}; Resources after it were not read this run."
        if stopped else None
    )
    return prospects, note, MiningStats(len(read), failed, directories, no_links, len(prospects))



def review_sources(
    sources: list[dict],
    resources: list[dict],
    seen: list[dict],
    suggestions: list[dict],
    today: date,
    fetch: Fetch,
    judge: Judge,
    *,
    topic_fit: TopicFit,
    memory: Iterable[dict] = (),
    clock: Callable[[], float] = time.monotonic,
) -> ReviewPlan:
    """Plan one Source review: which Sources to retire and which to add.

    Pure apart from the injected `fetch` (url -> Fetched), `judge` (Scout's
    per-entry judge, with Scout's system prompt) and `topic_fit` (the Topic fit judge,
    with the same system prompt), which only Source discovery calls, and `clock` (the
    Trial time budget). Nothing passed in is modified.

    Source discovery: each Source suggestion (`{"number", "url"}`, already limited to
    the owner's and collaborators', see `suggestions_from_issues`) is a Prospective
    Source, run through `discover_sources` with `memory` (the Prospective Source
    memory's `rejected` entries). Its url must pass `is_safe_url` and not share a
    Source key (`source_key`) with any Source on the list, enabled or Retired; its
    feed is the url itself (a github.com repo maps to its releases feed) or, for a
    page, the first safe RSS/Atom feed the page advertises (`no-feed` when none). Its
    Trial samples up to 10 of the feed's entries from the last 6 calendar months,
    newest first, dated by Scout's `entry_date`, first judges the feed's Topic fit in
    one call over that sample (`off-topic` without it, and no entry is judged), then
    judges each sampled entry; it passes iff the feed fetched cleanly (Scout's
    `read_source` rule), has an entry in the window, has Topic fit, and at least one
    entry is judged `include`. The Topic fit call counts against the Trial budget and
    fails like an entry call. A pass becomes a new Source entry with `added_at`,
    `added_by: source-review` and `last_checked_at` 6 months back; its included
    entries are evidence only (nothing is written to resources.yaml). A fetch failure,
    judge error, usage limit or spent Trial budget leaves the Prospective Source
    untried (`plan.untried`); all but a fetch failure stop further Trials and set
    `plan.incomplete`, while retirements and completed Trials stand (Scout's
    semantics).

    Citation mining (`_mine_citations`) reads each live Resource's page with `fetch`
    and proposes every site at least 2 distinct Resources (on 2 sites) link to, bar non-Sources
    (`_citable`), Sources on the list (enabled or Retired) and suggested sites. Mined
    Prospective Sources go through the same `discover_sources` call and Trial budget,
    after the suggestions, most-cited first; a site in `memory` is skipped silently. A
    Resource page that cannot be read is skipped; running out of Resource page fetches
    (MAX_CITATION_FETCHES) or mining time (CITATION_BUDGET_SECONDS) stops mining,
    Trials what was mined so far and adds to `plan.incomplete`.

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
    suggested = _suggested_prospects(suggestions)
    # Never proposed by mining: any Source on the list (enabled or Retired) and any
    # suggested site, which is Trialled as the suggestion.
    excluded = {
        k for k in (source_key(u) for u in [*(s.get("url") for s in sources),
                                            *(p.url for p in suggested)]) if k
    }
    mined, mining_stopped, plan.mining = _mine_citations(resources, excluded, fetch, clock)
    # One pipeline and one Trial budget for both channels: suggestions first (the owner
    # asked for them), then mined Prospective Sources, best-evidenced first.
    discover_sources(plan, [*suggested, *mined], sources, fetch, judge, memory, clock,
                     topic_fit=topic_fit)
    if mining_stopped:
        plan.incomplete = f"{plan.incomplete} {mining_stopped}" if plan.incomplete else mining_stopped
    return plan


def _fresh(day: date) -> date:
    """A new date object per write: the YAML dumper would turn one shared date into an
    anchor and aliases (`&id001` / `*id001`)."""
    return date(day.year, day.month, day.day)


def update_prospect_memory(memory: list[dict], plan: ReviewPlan) -> list[dict]:
    """The Prospective Source memory (scout/prospects.yaml `rejected`) after this run.

    Entries older than TRIAL_WINDOW_MONTHS are pruned, so a rejected site can be
    reconsidered once it may have changed; each of the plan's rejections (lasting
    verdicts only: untried Prospective Sources are retried, not remembered) is
    appended. Recording a suggestion's rejection is also what gives a run whose only
    outcome is that rejection a diff, so it opens the PR that closes the issue. Unsafe
    urls are recorded as null. Raises ValueError on a malformed `rejected_at`.
    """
    cutoff = _months_before(plan.today, TRIAL_WINDOW_MONTHS)
    kept = [m for m in memory if _date_field(m.get("rejected_at")) > cutoff]
    for r in plan.rejected:
        assert r.reason in REJECTION_REASONS, f"unknown rejection reason {r.reason!r}"
        safe = is_safe_url(r.prospect.url)
        kept.append({
            "key": source_key(r.prospect.url) if safe else None,
            "url": r.prospect.url if safe else None,
            "channel": r.prospect.channel,
            "suggestion": r.prospect.suggestion,
            "reason": r.reason,
            "rejected_at": _fresh(plan.today),
        })
    return kept


def apply_plan(sources: list[dict], plan: ReviewPlan) -> None:
    """Apply a plan to the Source list in place: retirements, then additions.

    Each addition is appended to `sources` as its new Source entry, after a blank line.

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
        for key, value in (("retired_at", _fresh(plan.today)), ("retired_reason", retirement.reason)):
            if key not in source and hasattr(source, "insert"):
                source.insert(list(source).index(anchor) + 1, key, value)
            else:
                source[key] = value
            anchor = key
    for addition in plan.additions:
        sources.append({k: _fresh(v) if isinstance(v, date) else v for k, v in addition.source.items()})
        if hasattr(sources, "yaml_set_comment_before_after_key"):
            # A blank line before it, like the hand-written entries in sources.yaml.
            sources.yaml_set_comment_before_after_key(len(sources) - 1, before="\n")


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
    """Hold an addition whose Source key (or id) is already on the list, Retired
    Sources included.

    Recomputed with the same `source_key` discovery uses, from the Source list rather
    than trusted from discovery's own dedup; an addition is also checked against the
    additions before it in the same plan.
    """
    reasons = []
    keys = {k: str(s.get("id")) for s in sources if (k := source_key(s.get("url")))}
    ids = {str(s.get("id")) for s in sources}
    for a in plan.additions:
        new_id = str(a.source.get("id"))
        key = source_key(a.source.get("url"))
        if new_id in ids:
            reasons.append(
                f"new Source {sanitize_text(new_id, 80)} duplicates the id of an existing Source"
            )
        if key in keys:
            reasons.append(
                f"new Source {sanitize_text(new_id, 80)} duplicates Source "
                f"{sanitize_text(keys[key], 80)} (same Source key {sanitize_text(key, 120)})"
            )
        ids.add(new_id)
        if key:
            keys.setdefault(key, new_id)
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
    Markdown links, HTML or a `::` workflow command. Free text from feeds, the judge or
    the CLI is also defused (`scout.defuse_references`), so it cannot close an issue or
    mention anyone: the only closing keywords are the `Closes #n` lines for decided
    suggestions, one per addition and rejection.
    """
    if decision["auto_merge_ok"]:
        prefix = "> **Auto-merge enabled** — this PR will land once required checks pass.\n"
    else:
        reasons = "; ".join(_free_text(r) for r in decision["reasons"])
        prefix = f"> **Auto-merge skipped:** {reasons}.\n"
    urls = {s["id"]: s.get("url") for s in sources}
    rows = []
    for r in plan.retirements:
        rows.append(
            f"- **{sanitize_text(r.source_id, 120)}** — `{sanitize_text(r.reason, 40)}`: "
            f"{_evidence(r, plan.today)}\n"
            # A code span: a url like medium.com/feed/@user must not mention anyone.
            f"  Feed: {_url_span(urls.get(r.source_id))}"
        )
    if plan.incomplete:
        prefix += f"\n> **Partial run:** {_free_text(plan.incomplete)}\n"
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
            "Each passed its Trial through the Scout judge: Topic fit for the feed as a whole, "
            "then at least one included entry. The included entries are evidence "
            "only; Scout picks them up on its next run (`last_checked_at` is 6 months back).\n\n"
            + "\n".join(_addition_row(a) for a in plan.additions)
        )
    if plan.rejected:
        sections.append(
            f"\n## Rejected Prospective Sources ({len(plan.rejected)})\n\n"
            + _outcome_rows(plan.rejected, "rejected", "see scout/prospects.yaml")
        )
    if plan.untried:
        sections.append(
            f"\n## Not decided this run ({len(plan.untried)})\n\n"
            "Tried again next run; a suggestion's issue stays open.\n\n"
            + _outcome_rows(plan.untried, "not decided", "see the run log")
        )
    closes = [a.prospect.suggestion for a in plan.additions]
    closes += [r.prospect.suggestion for r in plan.rejected]
    closes = [n for n in closes if type(n) is int]
    return _fit_body("\n".join(sections), "\n".join(f"Closes #{n}" for n in closes))


# Mined outcomes shown in full per section; the rest are counted. Suggestions are
# always listed: each closes (or keeps open) its issue.
MINED_ROWS_SHOWN = 15
# GitHub rejects a PR body over 65,536 characters.
MAX_BODY_CHARS = 60_000


def _outcome_rows(outcomes: list, verb: str, rest: str) -> str:
    """Rows for rejected or untried Prospective Sources: every suggestion in full, then
    the mined ones summarised by reason in a collapsed block, the first
    MINED_ROWS_SHOWN of them (the best-evidenced) listed."""
    listed = [o for o in outcomes if o.prospect.channel != "citation"]
    mined = [o for o in outcomes if o.prospect.channel == "citation"]
    blocks = []
    if listed:
        blocks.append("\n".join(_outcome_row(o) for o in listed))
    if mined:
        counts = Counter(o.reason for o in mined)
        by_reason = ", ".join(
            f"{sanitize_text(reason, 40)} {n}"
            for reason, n in sorted(counts.items(), key=lambda c: (-c[1], c[0]))
        )
        rows = [_outcome_row(o) for o in mined[:MINED_ROWS_SHOWN]]
        if len(mined) > MINED_ROWS_SHOWN:
            rows.append(f"- and {len(mined) - MINED_ROWS_SHOWN} more ({rest})")
        blocks.append(
            f"<details><summary>Citation mining: {len(mined)} {verb} ({by_reason})</summary>\n\n"
            + "\n".join(rows)
            + "\n\n</details>"
        )
    return "\n\n".join(blocks)


def _fit_body(body: str, closes: str) -> str:
    """`body` then the `closes` lines, within MAX_BODY_CHARS: an over-long body is cut
    at a line break with a notice (and any open `<details>` closed), never the
    closing lines (best effort: the next run closes decided suggestions anyway, see
    `reconcile_suggestions`)."""
    tail = f"\n\n{closes}" if closes else ""
    if len(body) + len(tail) <= MAX_BODY_CHARS:
        return body + tail
    notice = (
        f"\n\n> **Truncated:** this PR body passed {MAX_BODY_CHARS} characters; the full "
        "plan is in the run log and scout/prospects.yaml."
    )
    close_details = "\n\n</details>"
    cut = body[: max(0, MAX_BODY_CHARS - len(tail) - len(notice) - len(close_details))]
    cut = cut[: cut.rfind("\n")] if "\n" in cut else ""
    if cut.count("<details>") > cut.count("</details>"):
        cut += close_details
    return cut + notice + tail


def _one_line(text: object) -> str:
    return " ".join(str(text).split())


def _free_text(text: object) -> str:
    """Already-sanitized free text, on one line and unable to close issues or mention."""
    return scout.defuse_references(_one_line(text))


# A judge rationale shown in the PR body is cut to this many characters (before
# defusing), so an Addition with many included entries cannot crowd out the rest.
# 500 is `sanitize_text`'s default length: judge rationales often run 350-500
# characters and end on their verdict, so only the escapes it adds are ever cut.
RATIONALE_MAX_CHARS = 500


def _rationale_line(text: object, label: str = "Rationale") -> str:
    """`label: _text_` for a judge rationale, or "" when there is none.

    Precondition: `text` was already sanitized by `sanitize_text` (as `judge_sources`
    does), which escapes Markdown links and HTML; this does not sanitize again. It is
    put on one line, cut to RATIONALE_MAX_CHARS (never through a backslash escape), and
    defused, so it cannot close an issue, mention anyone or start a `::` workflow command.
    """
    text = _one_line(text)
    if len(text) > RATIONALE_MAX_CHARS:
        cut = text[:RATIONALE_MAX_CHARS]
        if (len(cut) - len(cut.rstrip("\\"))) % 2:
            cut = cut[:-1]  # a lone backslash would escape whatever follows it
        text = cut.rstrip() + "…"
    return f"{label}: _{scout.defuse_references(text)}_" if text else ""


def _url_span(url: object) -> str:
    """A url as an inert code span, or a placeholder when it is not safe to show.

    A safe url contains no backtick, whitespace or Markdown-significant bracket.
    """
    return f"`{url}`" if is_safe_url(url) else "(unsafe url withheld)"


def _channel(prospect: ProspectiveSource) -> str:
    if prospect.channel == "suggestion" and type(prospect.suggestion) is int:
        return f"Source suggestion #{prospect.suggestion}"
    if prospect.channel == "citation":
        return "Citation mining"
    return sanitize_text(prospect.channel, 40)


CITATIONS_SHOWN = 10


def _cited_by(prospect: ProspectiveSource) -> str:
    """`cited by N Resources: id, id, ...` for a mined Prospective Source, or "".

    Resource ids come from resources.yaml, so each is sanitized and defused like any
    other text in the PR body.
    """
    ids = prospect.citations
    if not ids:
        return ""
    shown = ", ".join(_free_text(sanitize_text(i, 80)) for i in ids[:CITATIONS_SHOWN])
    more = f" and {len(ids) - CITATIONS_SHOWN} more" if len(ids) > CITATIONS_SHOWN else ""
    return f"cited by {len(ids)} Resource{'s' if len(ids) != 1 else ''}: {shown}{more}"


def _trial_line(trial: TrialEvidence) -> str:
    return (
        f"Trial: {trial.in_window} entries in the last {TRIAL_WINDOW_MONTHS} months, "
        f"{trial.judged} judged, {len(trial.included)} included"
    )


def _addition_row(a: Addition) -> str:
    lines = [
        f"- **{sanitize_text(a.source.get('id'), 120)}** — from {_channel(a.prospect)}",
    ]
    if cited := _cited_by(a.prospect):
        lines.append(f"  {cited[0].upper()}{cited[1:]}")
    lines += [
        f"  Feed: {_url_span(a.source.get('url'))} ({sanitize_text(a.source.get('type'), 10)})",
    ]
    # The Topic fit rationale was sanitized by `_trial`, and entry titles and rationales
    # by judge_sources; sanitizing again would double the escapes.
    if fit := _rationale_line(a.trial.topic_fit_rationale, label="Topic fit"):
        lines.append(f"  {fit}")
    lines.append(f"  {_trial_line(a.trial)}:")
    for entry in a.trial.included:
        lines.append(f"  - {_free_text(entry.title)} — {_url_span(entry.url)}")
        if rationale := _rationale_line(entry.rationale):
            lines.append(f"    {rationale}")
    return "\n".join(lines)


def _outcome_row(r: Rejection | Untried) -> str:
    url = _url_span(r.prospect.url) if r.prospect.url else "(no url)"
    row = f"- {_channel(r.prospect)}: {url} — `{sanitize_text(r.reason, 40)}`"
    if r.detail:
        row += f": {_free_text(r.detail)}"  # sanitized when the outcome was made
    extras = [e for e in (_cited_by(r.prospect), r.trial and _trial_line(r.trial)) if e]
    if extras:
        row += f" ({'; '.join(extras)})"
    # Why the feed lacks Topic fit (an `off-topic` rejection); sanitized by `_trial`.
    if r.trial and r.trial.topic_fit is False:
        if fit := _rationale_line(r.trial.topic_fit_rationale, label="Topic fit"):
            row += f"\n  {fit}"
    return row


# ── Entry point ──────────────────────────────────────────────────────────────


FETCH_TIMEOUT_SECONDS = 20  # per socket operation
FETCH_DEADLINE_SECONDS = 60  # for the whole fetch, redirects included
FETCH_MAX_BYTES = 2_000_000
FETCH_MAX_REDIRECTS = 5
FETCH_PORTS = (None, 80, 443)  # None: the scheme's default
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
# 4xx statuses that say "try later" (timeout, too early, too many requests), not "gone".
TRANSIENT_CLIENT_ERRORS = frozenset({408, 425, 429})
_ACCEPT = (
    "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.9, "
    "text/html;q=0.8, */*;q=0.5"
)
# IPv6 ranges that embed or translate to an IPv4 address, so reach whatever that is:
# IPv4-compatible (deprecated) and the NAT64 prefixes.
_TRANSLATED_V6 = tuple(
    ipaddress.ip_network(n) for n in ("::/96", "64:ff9b::/96", "64:ff9b:1::/48")
)
_clock = time.monotonic  # the fetch deadline's clock; a seam for tests


class FetchError(Exception):
    """A url Source discovery refused to fetch, or a response it refused to read."""


def is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if `address` is globally routable, looking through IPv6 forms that reach an
    IPv4 address (mapped, 6to4, Teredo, IPv4-compatible, NAT64)."""
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return is_public_address(address.ipv4_mapped)
        if address.sixtofour is not None:
            return is_public_address(address.sixtofour)
        if address.teredo is not None or any(address in n for n in _TRANSLATED_V6):
            return False
    return address.is_global


def _public_create_connection(address, *args, **kwargs):
    """`socket.create_connection` that refuses a peer that is not a public address.

    Checked on the connected socket, so a DNS answer that changed since
    `_check_target` resolved the host (DNS rebinding) is still caught, before any
    byte (or TLS handshake) is sent.
    """
    sock = socket.create_connection(address, *args, **kwargs)
    try:
        peer = ipaddress.ip_address(str(sock.getpeername()[0]).split("%")[0])
        if not is_public_address(peer):
            raise FetchError("refused a connection to a non-public address")
    except BaseException:
        sock.close()
        raise
    return sock


class _PublicHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection = _public_create_connection


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection = _public_create_connection


class _PublicHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PublicHTTPConnection, req)


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PublicHTTPSConnection, req, context=self._context)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface every redirect as an HTTPError so `http_fetch` can check its target first."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# No proxies (a proxy would be the peer checked), no automatic redirects, and only
# connections to public addresses.
_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), _NoRedirect, _PublicHTTPHandler, _PublicHTTPSHandler
)


def _check_target(url: str) -> None:
    """Refuse a url that is not a public web url (SSRF) before connecting: it must pass
    `is_safe_url`, use a default, 80 or 443 port, and every address its host resolves
    to must be public. The connection itself is checked again (`_public_create_connection`)."""
    if not is_safe_url(url):
        raise FetchError("refused an unsafe url")
    parts = urlsplit(url)
    if parts.port not in FETCH_PORTS:
        raise FetchError(f"refused port {parts.port}")
    try:
        infos = socket.getaddrinfo(parts.hostname or "", None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise FetchError(f"cannot resolve the host: {exc}") from exc
    for info in infos:
        if not is_public_address(ipaddress.ip_address(str(info[4][0]).split("%")[0])):
            raise FetchError("refused a host that resolves to a non-public address")


def http_fetch(url: str) -> Fetched:
    """HTTP GET for Source discovery; the injected `fetch`. Returns the final url too.

    Follows up to FETCH_MAX_REDIRECTS redirects by hand, checking the first url and
    every redirect target with `_check_target` before connecting, and every connection
    with `_public_create_connection`. An HTTP error status is returned, not raised; a
    body over FETCH_MAX_BYTES, a fetch outlasting FETCH_DEADLINE_SECONDS (checked
    before each connection and after each read, which returns whatever has arrived
    rather than waiting for a full buffer), a refused url, an https→http redirect and
    any network error raise.
    """
    deadline = _clock() + FETCH_DEADLINE_SECONDS
    for _ in range(FETCH_MAX_REDIRECTS + 1):
        if _clock() > deadline:
            raise FetchError(f"fetch slower than {FETCH_DEADLINE_SECONDS}s")
        _check_target(url)
        request = urllib.request.Request(
            url, headers={"User-Agent": scout.USER_AGENT, "Accept": _ACCEPT}
        )
        try:
            with _OPENER.open(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
                chunks, size = [], 0
                while chunk := response.read1(64 * 1024):
                    size += len(chunk)
                    if size > FETCH_MAX_BYTES:
                        raise FetchError(f"response larger than {FETCH_MAX_BYTES} bytes")
                    if _clock() > deadline:
                        raise FetchError(f"fetch slower than {FETCH_DEADLINE_SECONDS}s")
                    chunks.append(chunk)
                return Fetched(response.status, dict(response.headers), b"".join(chunks), url)
        except urllib.error.HTTPError as exc:
            headers = dict(exc.headers or {})
            exc.close()
            location = headers.get("Location") if exc.code in _REDIRECT_CODES else None
            if not location:
                return Fetched(exc.code, headers, b"", url)
            target = urljoin(url, location)
            if urlsplit(url).scheme.lower() == "https" and urlsplit(target).scheme.lower() != "https":
                # A downgrade would be followed in the clear and stored as the feed url.
                raise FetchError("refused an https→http redirect")
            url = target
    raise FetchError(f"more than {FETCH_MAX_REDIRECTS} redirects")


def _plan_title(plan: ReviewPlan, today: date) -> str:
    def count(n: int, noun: str) -> str:
        return f"{n} {noun}{'s' if n != 1 else ''}"

    parts = []
    if plan.retirements:
        parts.append(count(len(plan.retirements), "retirement"))
    if plan.additions:
        parts.append(count(len(plan.additions), "addition"))
    if plan.rejected:
        parts.append(f"{len(plan.rejected)} rejected")
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


PROSPECTS_PATH = scout.ROOT / "scout" / "prospects.yaml"
_PROSPECTS_TEMPLATE = """\
# Source discovery's Prospective Source memory, written by Source review only
# (scripts/source_review.py); Scout never reads it. Each lasting rejection is kept
# for 6 months, so Citation mining does not re-Trial the same site every month; a
# Source suggestion is always tried again.
rejected: []
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Source review: retire Dead and Unproductive Sources, add suggested and "
            "Citation-mined ones that pass a Trial"
        )
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
    prospects_text = PROSPECTS_PATH.read_text() if PROSPECTS_PATH.exists() else _PROSPECTS_TEMPLATE
    prospects_data = ryaml.load(prospects_text)
    memory = prospects_data.get("rejected") or []
    sources = sources_data["sources"]
    resources = resources_data["resources"]
    # Suggestions decided by an earlier, merged Source review are closed, not Trialled
    # again: GitHub may not act on the `Closes` lines of a PR opened with GITHUB_TOKEN.
    suggestions, close_suggestions = reconcile_suggestions(
        _load_suggestions(args.suggestions), sources, memory
    )

    # The unchanged Scout judge, with the system prompt Scout builds; the Topic fit
    # judge takes the same prompt, for the guide's sections and inclusion criteria.
    system = scout.build_system_prompt(resources_data["sections"], resources)
    today = date.today()
    plan = review_sources(
        sources, resources, seen, suggestions, today,
        fetch=http_fetch, judge=functools.partial(judge.judge_entry, system),
        topic_fit=functools.partial(judge.judge_topic_fit, system), memory=memory,
    )
    decision = evaluate_source_review_automerge(plan, sources, resources)

    # Everything printed is sanitized: an id, url or error with a line break could
    # otherwise emit a `::` workflow command.
    enabled = sum(1 for s in sources if s.get("enabled", True))
    print(
        f"Reviewed {enabled} enabled Source(s) and {len(suggestions)} undecided Source "
        f"suggestion(s) ({len(close_suggestions)} already decided, to close): "
        f"{len(plan.retirements)} to retire, {len(plan.additions)} to add, "
        f"{len(plan.rejected)} rejected, {len(plan.untried)} not decided."
    )
    if plan.mining is not None:
        print(mining_summary(plan.mining))
    for r in plan.retirements:
        print(f"  [{r.reason}] {sanitize_text(r.source_id, 120)}: {_evidence(r, today)}")
    for a in plan.additions:
        print(f"  [add] {sanitize_text(a.source['id'], 120)} from {_channel(a.prospect)}: "
              f"{_trial_line(a.trial)}")
    for r in [*plan.rejected, *plan.untried]:
        print(f"  [{sanitize_text(r.reason, 40)}] {_channel(r.prospect)}: "
              f"{sanitize_text(r.prospect.url, 200)} {_one_line(r.detail)}")
    for c in close_suggestions:
        print(f"  [close] Source suggestion #{c['number']}: already decided on main")
    for u in plan.untried:
        print(f"::warning::{_channel(u.prospect)} not decided ({sanitize_text(u.reason, 40)}); "
              "it is tried again next run.", flush=True)
    if plan.incomplete:
        print(f"::warning::{_one_line(plan.incomplete)}", flush=True)
    print(json.dumps(decision._asdict()))

    if args.dry_run:
        print("Dry run — no files written.")
        return

    # Judge-failure fields are written even for an empty plan: the workflow fails the
    # run on them after the PR steps, so a dead judge never looks like a quiet month.
    summary: dict = {
        "empty": plan.is_empty,
        "incomplete": plan.incomplete is not None,
        "judge_failed": plan.judge_failure is not None,
        "auth_failed": plan.auth_failed,
        "judge_failure_hint": plan.judge_failure,
        "citation_mining": plan.mining._asdict() if plan.mining is not None else None,
        # Open suggestions whose decision is already on main; the workflow closes them.
        "close_suggestions": close_suggestions,
    }
    if plan.is_empty:
        print("Nothing to add, retire or reject; no PR.")
    else:
        apply_plan(sources, plan)
        with open(scout.SOURCES_PATH, "w") as f:
            ryaml.dump(sources_data, f)
        prospects_data["rejected"] = update_prospect_memory(memory, plan)
        with open(PROSPECTS_PATH, "w") as f:
            ryaml.dump(prospects_data, f)
        print(f"Updated {scout.SOURCES_PATH} and {PROSPECTS_PATH}")
        summary |= {
            "title": _plan_title(plan, today),
            "body": pr_body(plan, decision._asdict(), sources),
            **decision._asdict(),
        }
    if args.summary is not None:
        args.summary.write_text(json.dumps(summary))


if __name__ == "__main__":
    main()
