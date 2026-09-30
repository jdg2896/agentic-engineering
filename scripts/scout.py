#!/usr/bin/env python3
"""Scout new resources from RSS/Atom feeds and evaluate candidates via Claude Code headless."""

from __future__ import annotations

import argparse
import functools
import ipaddress
import json
import os
import re
import sys
import time
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

import feedparser
import idna
import yaml as pyyaml
from ruamel.yaml import YAML

import judge
from judge import JudgeAuthError, JudgeQuotaError  # judge_sources' `judge` parameter shadows the module

ROOT = Path(__file__).resolve().parent.parent
SOURCES_PATH = ROOT / "sources.yaml"
SEEN_PATH = ROOT / "scout" / "seen.yaml"
RESOURCES_PATH = ROOT / "resources.yaml"
CANDIDATES_PATH = ROOT / "candidates.yaml"

_REPO = os.environ.get("GITHUB_REPOSITORY", "jdg2896/agentic-engineering")
USER_AGENT = f"agentic-engineering-bot (+https://github.com/{_REPO})"

def build_system_prompt(sections: list, resources: list) -> str:
    lines = [
        "You are an editorial assistant for a curated, opinionated resource guide on agentic engineering.",
        "",
        "## Guide sections",
        "",
    ]
    for s in sections:
        desc = (s.get("description") or "").strip()
        first_line = desc.splitlines()[0] if desc else ""
        lines.append(f"- **{s['id']}**: {s['title']} — {first_line}")

    lines += [
        "",
        "## House style — blurb examples",
        "",
        'Short, dense, no filler. Strip "this post", "this article", "the author". Lead with the idea, not the source.',
        "",
    ]

    example_ids = {
        "building-effective-agents",
        "twelve-factor-agents",
        "dont-build-multi-agents",
        "applied-llms-year",
        "openai-practical-guide",
    }
    for r in resources:
        if r["id"] in example_ids:
            lines.append(f'- [{r["id"]}] ({r["type"]}) "{r["blurb"]}"')

    lines += [
        "",
        "## Inclusion criteria",
        "",
        "- Must be written in English — reject a non-English entry for that reason alone, however good its content",
        "- Author reputation, the kind of site (personal blog, company blog or vendor blog) and how well known the source is are not criteria — judge only the content, against these criteria",
        "- Must be substantive technical content, not marketing or press release",
        '- No listicles, SEO-optimised roundups, or "X things you should know" posts',
        "- Papers must have practical infrastructure implications, not pure ML theory",
        "- Tools must be production-ready or notable open-source research artefacts",
        "- Content must be about building, evaluating, operating, or securing agentic systems — any engineering discipline (FE, BE, infra, QA, data)",
        "- Vendor blog posts are held to the same content bar as any other entry — include only if they contain reproducible techniques or architecture decisions",
        "- Reject if a substantially similar resource already exists in resources.yaml",
        "- News/announcements (new model release, funding round) → reject unless the announcement post itself contains technical content",
        "- GitHub release notes → include only if the release introduces a meaningful new capability (not just patch/bugfix)",
        "- Security content → include only if it covers agent-specific attack surface (prompt injection, indirect injection, tool misuse)",
        "",
        "Return your editorial judgment as structured output matching the provided JSON schema.",
    ]

    return "\n".join(lines)


def safe_slug(base: str, existing: set[str]) -> str:
    """Return `base`, or the first free `base-2`, `base-3`, ... not in `existing`."""
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


# Characters that could end a Markdown link destination or start HTML/code in the README.
_URL_FORBIDDEN_CHARS = frozenset("()[]<>\"'`\\")
# Matched against "." + host, so each also blocks the bare name (e.g. `localhost`).
_INTERNAL_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa", ".lan")
_NUMERIC_LABEL_RE = re.compile(r"^(?:\d+|0x[0-9a-f]*)$")


def is_safe_url(url: object) -> bool:
    """True if a feed URL is safe to store as a Resource url and render as a link. Pure.

    Feed links are untrusted, so this allows only plain public web links: an http(s)
    scheme and a hostname; no whitespace, control characters or characters that
    could break out of a Markdown link (`()[]<>"'` backtick, backslash); no invisible
    format characters (zero-width, bidi controls); no userinfo (`user@host` misleads
    about the real host) and no percent-escapes in the host; and no IP-literal,
    numeric (e.g. `2130706433`), `localhost`, `.local`, `.internal`, `.home.arpa` or
    `.lan` hosts. The host is checked after IDNA (UTS 46) normalisation, since HTTP
    clients map e.g. fullwidth `ｌｏｃａｌｈｏｓｔ` or `127。0。0。1` to the ASCII host.
    """
    if not isinstance(url, str) or not url:
        return False
    if any(
        ch.isspace() or unicodedata.category(ch) in ("Cc", "Cf") or ch in _URL_FORBIDDEN_CHARS
        for ch in url
    ):
        return False
    try:
        parts = urlsplit(url)
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError:
        return False
    if parts.scheme.lower() not in ("http", "https") or "@" in parts.netloc or "%" in parts.netloc:
        return False
    try:
        # Also rejects empty labels (`a..b`) and labels starting or ending with `-`.
        host = idna.encode(parts.hostname or "", uts46=True).decode("ascii").rstrip(".")
    except (idna.IDNAError, UnicodeError):
        return False
    if not host or any(not label.strip("-") for label in host.split(".")):
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    # A numeric last label makes browsers read the host as IPv4 (e.g. `0x7f.1`).
    if _NUMERIC_LABEL_RE.fullmatch(host.rsplit(".", 1)[-1]):
        return False
    return not ("." + host).endswith(_INTERNAL_HOST_SUFFIXES)


def _code_span_text(value: object, max_len: int = 80) -> str:
    """Model text made safe to sit inside a Markdown `code span` on one line."""
    return sanitize_text(str(value).replace("`", "'"), max_len)


_WHITESPACE_RE = re.compile(r"\s+")
_MARKDOWN_SIGNIFICANT = str.maketrans({c: "\\" + c for c in "\\[]<>`&"})
_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


def sanitize_text(value: str, max_len: int = 500) -> str:
    """Neutralise judge free-text before it reaches resources.yaml, the README or a PR body.

    Collapses whitespace (newlines included) to single spaces, drops control
    characters, truncates to `max_len`, then backslash-escapes backslashes and
    Markdown link / HTML characters, and strips. `&` is escaped too (`\\&` renders as
    a literal `&`), so a named entity such as `&commat;user` or `&num;12` cannot decode
    into a mention or issue reference that `defuse_references` never saw. This alone
    is not enough for PR bodies: `\\&#64;` still leaves a bare `#64`, which GitHub
    links, so PR text must also go through `defuse_references`. Truncating first
    means no escape is ever split, so the result may exceed `max_len` by its escapes.
    Pure.
    """
    text = _WHITESPACE_RE.sub(" ", str(value))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cc")
    return text.strip()[:max_len].strip().translate(_MARKDOWN_SIGNIFICANT)


_ZWSP = "​"
_CLOSING_KEYWORD_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b", re.IGNORECASE)


def defuse_references(text: str) -> str:
    """Stop feed or judge text in a PR body from closing issues or pinging people. Pure.

    GitHub acts on `Fixes #42`, `Closes owner/repo#42` or an issue url after a
    closing keyword when the PR merges, and notifies `@user`. A zero-width space
    after the first letter of each closing keyword and after every `#` and `@`
    breaks all of these while the text still reads the same. For PR bodies only:
    text stored in resources.yaml or rendered into the README is left alone.
    """
    text = _CLOSING_KEYWORD_RE.sub(lambda m: m.group(0)[0] + _ZWSP + m.group(0)[1:], str(text))
    return text.replace("#", "#" + _ZWSP).replace("@", "@" + _ZWSP)


@dataclass
class ScoutRun:
    """Outcome of judging one run's new entries."""

    candidates: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    evaluated: int = 0
    # Sources whose every new entry was judged; only these may bump last_checked_at.
    fully_judged: set[str] = field(default_factory=set)
    # The judge error was an authentication failure, so the credential needs fixing.
    auth_failed: bool = False
    # Judging stopped before every entry was judged, but not on an error: why, naming
    # the cause (e.g. "the Claude usage limit (<error message>)"). Unsanitized.
    stopped_early: str | None = None
    # The early stop was the subscription usage limit, the one cause that can leave a
    # run with nothing judged; main() fails such a run instead of hiding it.
    usage_limit_hit: bool = False


def judge_failure_hint(run: ScoutRun) -> str:
    """What to do about a run that failed on a judge error."""
    if run.auth_failed:
        return "Check the judge credential (see docs/adr/0001-oauth-token-for-ci.md)."
    return (
        "The judge failed (see the error). A CLI timeout or API error that outlasted the "
        "retries may clear on a re-run; an error that repeats run after run needs a code "
        "or config fix."
    )


# The usage-limit error text kept in a stop reason: well under sanitize_text's default
# 500, so the reason around it survives being sanitized whole.
USAGE_LIMIT_MESSAGE_LEN = 300


def record_judge_failure(run: ScoutRun, exc: Exception, context: str) -> None:
    """Record a judge call's exception in `run`: a `JudgeQuotaError` (a usage limit)
    as the early stop `the Claude usage limit (<message>)` with `usage_limit_hit`,
    anything else in `errors` as `"{context}: {exc}"`, with `auth_failed` set for a
    `JudgeAuthError`.

    `context` must already be sanitized; the exception text is sanitized here, since
    errors are printed as `::error::` lines, where no newline may survive.
    """
    if isinstance(exc, JudgeQuotaError):
        # Trimmed here, not only when written, so truncating the whole reason
        # later can never cut its closing bracket.
        message = " ".join(str(exc).split())[:USAGE_LIMIT_MESSAGE_LEN]
        run.stopped_early = f"the Claude usage limit ({message})"
        run.usage_limit_hit = True
        return
    run.errors.append(f"{context}: {sanitize_text(str(exc), 1000)}")
    run.auth_failed = isinstance(exc, JudgeAuthError)


def entry_text(entry) -> tuple[str, str]:
    """A feed entry's (title, summary) as the judge sees them: the summary, else the
    first content block. Unsanitized feed text."""
    content_list = entry.get("content", [])
    content_val = content_list[0].get("value", "") if content_list else ""
    return entry.get("title", "(untitled)"), entry.get("summary", "") or content_val


def judge_sources(
    new_entries: dict[str, list],
    judge: Callable[[str, str, str, str], dict],
    known_urls: set[str],
    existing_slugs: set[str],
    limit: int | None = None,
    max_calls: int | None = None,
    max_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> ScoutRun:
    """Judge each Source's new entries and record which Sources were fully judged.

    `judge(title, url, summary, source_id)` returns the judgment dict or raises;
    the first raise is recorded in `errors` and stops judging, since any error
    fails the run. A `JudgeQuotaError` also stops judging, since every later call
    would hit the same limit, but is recorded in `stopped_early` instead: the
    judgments made so far stand. Entries in `known_urls` are skipped. Judging also
    stops once `limit` entries are judged. Sources left unfinished by any stop are
    not in `fully_judged`.

    The optional budget caps one run's judging: at most `max_calls` judge calls and
    no call started once `max_seconds` of `clock` have passed since judging began.
    It is checked before each judge call, so skipped entries cost nothing, and a
    spent budget stops judging like the usage limit, recorded in `stopped_early`.
    With neither set there is no budget (Source review's Trials rely on that).

    Entries whose link fails `is_safe_url` are skipped before the judge is called,
    like known URLs: they are not errors, are not recorded as rejects, and do not
    keep their Source out of `fully_judged`, since a hostile link is never worth
    retrying.
    """
    run = ScoutRun()
    slugs = set(existing_slugs)
    start = clock() if max_seconds is not None else 0.0
    for source_id, entries in new_entries.items():
        for entry in entries:
            url = entry.get("link", "")
            if not url:
                continue
            if not is_safe_url(url):
                print(f"    skip (unsafe url): {sanitize_text(url, 200)}")
                continue
            if url in known_urls:
                print(f"    skip (known): {sanitize_text(url)}")
                continue
            if limit is not None and run.evaluated >= limit:
                print(f"\n  --limit {limit} reached, stopping early.")
                return run
            if max_calls is not None and run.evaluated >= max_calls:
                calls = "judge call" if max_calls == 1 else "judge calls"
                run.stopped_early = f"Scout's call budget of {max_calls} {calls}"
                return run
            if max_seconds is not None and clock() - start >= max_seconds:
                run.stopped_early = f"Scout's time budget of {max_seconds / 60:g} minutes of judging"
                return run
            title, summary = entry_text(entry)

            try:
                result = judge(title, url, summary, source_id)
            except Exception as exc:
                # Any error fails the run, so further judge calls would only burn quota.
                record_judge_failure(
                    run, exc,
                    f"source {source_id}: judge error for '{sanitize_text(title, 200)}'"
                    f" ({sanitize_text(url, 200)})",
                )
                return run
            run.evaluated += 1
            if result["decision"] == "include":
                if not _SLUG_RE.fullmatch(str(result["slug"])):
                    # Fail closed like any other judge error: the slug becomes an id and anchor.
                    run.errors.append(
                        f"source {source_id}: judge returned invalid slug {sanitize_text(result['slug'], 80)!r}"
                        f" for {sanitize_text(url)}"
                    )
                    return run
                slug = safe_slug(result["slug"], slugs)
                slugs.add(slug)
                run.candidates.append({
                    "slug": slug,
                    "source_id": source_id,
                    "url": url,
                    "title": sanitize_text(result["title"]),
                    "author": sanitize_text(result["author"]),
                    "section": result["section"],
                    "type": result["type"],
                    "license": sanitize_text(result["license"]) if result.get("license") is not None else None,
                    "blurb": sanitize_text(result["blurb"]),
                    "tags": [sanitize_text(t) for t in result["tags"]],
                    "rationale": sanitize_text(result["rationale"]),
                })
                # Log only sanitized text so feed/model output cannot emit `::` workflow commands.
                print(f"    [include] {sanitize_text(title)}")
                print(f"              {sanitize_text(url)}")
                print(f"              section={sanitize_text(result['section'])}  type={sanitize_text(result['type'])}")
                print(f"              blurb: {run.candidates[-1]['blurb']}")
            else:
                run.rejected.append({
                    "url": url,
                    "title": title,
                    "source_id": source_id,
                    "rejected_at": str(date.today()),
                })
                print(f"    [reject]  {sanitize_text(title)}")
                print(f"              {sanitize_text(result['rationale'])}")
        run.fully_judged.add(source_id)
    return run


# One run's judge budget, whichever runs out first. The call budget bounds how much of
# the subscription quota, shared with interactive use (ADR-0001), one backlog can draw;
# the time budget keeps judging well inside the workflow's 90-minute timeout, leaving
# room to write files and open the PR. Opus 5.5 measured 7.7 s mean and 15.6 s max per
# call (#123), so the call budget normally binds first.
# A spent budget stops the run cleanly; the unjudged remainder is picked up next run.
# Retune both from real judge.MODEL timings when the model changes.
JUDGE_CALL_BUDGET = 100
JUDGE_TIME_BUDGET_SECONDS = 60 * 60

CANDIDATE_CAP = 8
BASE_LABELS = ["automated", "scout"]
SKIPPED_LABEL = "auto-merge-skipped"
VALID_TYPES = tuple(judge.JUDGMENT_SCHEMA["properties"]["type"]["enum"])


class Decision(NamedTuple):
    """Whether a Scout PR may auto-merge, why not, and the labels to apply."""

    auto_merge_ok: bool
    reasons: list[str]
    labels: list[str]


def evaluate_scout_automerge(
    candidates: list[dict],
    valid_section_ids: Iterable[str],
    valid_types: Iterable[str] = VALID_TYPES,
    cap: int = CANDIDATE_CAP,
) -> Decision:
    """Circuit-breaker gate for Scout PRs (ADR-0002); pure, anomalies only."""
    reasons: list[str] = []
    if len(candidates) > cap:
        reasons.append(f"{len(candidates)} candidates exceed cap of {cap}")
    sections = set(valid_section_ids)
    types = set(valid_types)
    for c in candidates:
        # Reasons are joined into the PR body, so model-supplied values are neutralised.
        slug = _code_span_text(c.get("slug"))
        if c.get("section") not in sections:
            reasons.append(f"candidate `{slug}` has unknown section `{_code_span_text(c.get('section'))}`")
        if c.get("type") not in types:
            reasons.append(f"candidate `{slug}` has unknown type `{_code_span_text(c.get('type'))}`")
        if not is_safe_url(c.get("url")):
            # judge_sources already drops these; holding here guards against a regression.
            reasons.append(f"candidate `{slug}` has an unsafe url")
    labels = list(BASE_LABELS) if not reasons else [*BASE_LABELS, SKIPPED_LABEL]
    return Decision(not reasons, reasons, labels)


NO_CANDIDATES_NOTE = (
    "_No new candidates this week. seen.yaml records the rejects; last\\_checked\\_at is bumped "
    "only on Sources whose feed was fetched and whose new entries were all judged._"
)


def pr_body(candidates: list[dict], decision: dict, incomplete: str | None = None) -> str:
    """The Scout PR body: an auto-merge banner, then one checklist row per Candidate. Pure.

    Title, blurb and rationale were sanitized in judge_sources. The url is re-checked
    (an unsafe one is withheld, not linked), slug/source/section/type are made inert
    for their code spans, and each gate reason is collapsed onto one line.

    `incomplete` is the reason a run that stopped early recorded in candidates.yaml
    (already sanitized by `write_candidates`); when set, a partial-run note naming it
    follows the banner, telling the reviewer to merge before the next run.
    """
    if decision["auto_merge_ok"]:
        prefix = "> **Auto-merge enabled** — this PR will land once required checks pass.\n"
    else:
        # The gate already neutralised model text in its reasons; just keep each on one line.
        reasons = "; ".join(" ".join(str(r).split()) for r in decision["reasons"])
        prefix = f"> **Auto-merge skipped:** {reasons}.\n"
    if incomplete:
        prefix += (
            f"\n> **Partial run:** judging stopped on {' '.join(str(incomplete).split())}, "
            "so some entries were left for the next run.\n"
            "> Merge this PR before the next scheduled run: that run starts from main, "
            "so until this lands it re-judges the same entries. For a large backlog, "
            "re-dispatch the workflow with a higher `candidate_cap`.\n"
        )
    if not candidates:
        return prefix + "\n" + NO_CANDIDATES_NOTE
    rows = []
    for c in candidates:
        # Feed/judge text is defused so it cannot close an issue or mention anyone on merge.
        title = defuse_references(c["title"])
        if is_safe_url(c.get("url")):
            link = f"[{title}]({c['url']})"
        else:
            link = f"{title} (unsafe url withheld)"
        rows.append(
            f"- [ ] **{link}** — `{_code_span_text(c['section'])}` · `{_code_span_text(c['type'])}`\n"
            f"      Source: `{_code_span_text(c['source_id'])}` | Proposed slug: `{_code_span_text(c['slug'])}`\n"
            f"      Blurb: _{defuse_references(c['blurb'])}_\n"
            f"      Rationale: _{defuse_references(c['rationale'])}_"
        )
    return prefix + "\n" + f"## Candidates ({len(candidates)})\n\n" + "\n\n".join(rows)


def write_candidates(path: Path, run: ScoutRun) -> None:
    """Write candidates.yaml; a run that stopped early also records why it is incomplete."""
    data: dict = {"candidates": run.candidates}
    if run.stopped_early is not None:
        data["incomplete"] = sanitize_text(run.stopped_early)
    path.write_text(pyyaml.dump(data, sort_keys=False, allow_unicode=True))


def _load_candidates_file(path: Path) -> dict:
    if not path.exists():
        return {}
    return pyyaml.safe_load(path.read_text()) or {}


def load_candidates(path: Path) -> list[dict]:
    """Read candidates.yaml; a missing or empty file means zero Candidates."""
    return _load_candidates_file(path).get("candidates") or []


def load_incomplete_reason(path: Path) -> str | None:
    """Why the run that wrote candidates.yaml stopped early, or None if it ran to completion."""
    return _load_candidates_file(path).get("incomplete") or None


def automerge_decision(candidates_path: Path, resources_path: Path, cap: int = CANDIDATE_CAP) -> dict:
    """Gate the Candidates in `candidates_path` against the sections in `resources_path`."""
    sections = pyyaml.safe_load(resources_path.read_text()).get("sections") or []
    decision = evaluate_scout_automerge(
        load_candidates(candidates_path), [s["id"] for s in sections], cap=cap
    )
    return decision._asdict()


def append_candidates(resources_data: dict, candidates: list[dict], today: str) -> None:
    """Append Candidates to `resources_data["resources"]` as new Resources.

    Only the `resources` list is written: Scout never adds to or edits `top_7`,
    which stays hand-curated (ADR-0002). Each new Resource keeps its Candidate's
    `source_id`, so Yield per Source is measurable; existing Resources are never
    given one.
    """
    for c in candidates:
        resources_data["resources"].append({
            "id": c["slug"],
            "section": c["section"],
            "url": c["url"],
            "title": c["title"],
            "author": c["author"],
            "type": c["type"],
            "license": c.get("license"),
            "blurb": c["blurb"],
            "cluster": None,
            "tags": c.get("tags", []),
            "added_at": today,
            "source_id": c["source_id"],
            "verified_at": None,
            "archived": False,
            "paywall": False,
            "superseded_by": None,
            "notes": None,
        })


class SourceHealth(NamedTuple):
    """A Source's health after this run's fetch; stored on the Source in sources.yaml.

    Scout records it but never acts on it: Source review decides whether a Source is Dead.
    """

    consecutive_failures: int  # failed fetches in a row; 0 once a fetch succeeds
    failing_since: date | None  # run date the current failure streak began; None when healthy
    last_http_status: int | None  # this run's HTTP status; None when fetching raised or no status
    newest_entry_at: date | None  # newest dated entry ever seen, of any age


FEED_TITLE_MAX_LEN = 120


class FeedMeta(NamedTuple):
    """The feed's own name and home link from a successful read; stored on the Source.

    Not Source health: Source review drops health when it retires a Source, but a
    Retired Source keeps these, so a revived one reappears under its name.
    """

    feed_title: str | None  # the feed's title, sanitized to one capped line; None if it has none
    site_url: str | None  # the feed's site link; None if it has none or it fails `is_safe_url`


class SourceRead(NamedTuple):
    """What reading one Source's parsed feed found."""

    new_entries: list
    health: SourceHealth
    meta: FeedMeta | None = None  # None when the read failed: stored values stay as they are


def _feed_meta(parsed_feed) -> FeedMeta:
    """The title and site link a parsed feed gives for itself. Feed text is untrusted."""
    channel = getattr(parsed_feed, "feed", None) or {}
    title = sanitize_text(channel.get("title") or "", FEED_TITLE_MAX_LEN)
    link = str(channel.get("link") or "").strip()
    return FeedMeta(feed_title=title or None, site_url=link if is_safe_url(link) else None)


def entry_date(entry) -> date | None:
    """An entry's date: `published`, else `updated` (the only date GitHub release feeds carry), else None."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    return date(*parsed[:3]) if parsed else None


def _stored_date(value) -> date | None:
    """A date field read back from sources.yaml (a date, an ISO string, or null)."""
    return None if value is None else date.fromisoformat(str(value))


def _failed_health(source: dict, status: int | None, today: date) -> SourceHealth:
    """Extend the Source's failure streak by one run; its start date is kept once set."""
    return SourceHealth(
        consecutive_failures=int(source.get("consecutive_failures") or 0) + 1,
        failing_since=_stored_date(source.get("failing_since")) or today,
        last_http_status=status,
        newest_entry_at=_stored_date(source.get("newest_entry_at")),
    )


def read_source(source: dict, parsed_feed, today: date) -> SourceRead:
    """Read one Source's already-parsed feed; does no network I/O.

    New entries are those dated (see `entry_date`) after the Source's
    `last_checked_at`. Undated entries are skipped rather than guessed, so an
    undated feed cannot flood the judge with its whole history.

    Also returns the Source's health, carried forward from the health stored on
    `source`. The fetch failed when the HTTP status is >= 400, when the parser flagged
    the document malformed (`bozo`) and found no entries, or when fetching raised (pass
    the exception as `parsed_feed`; its HTTP status is recorded as None). A failed fetch
    yields no new entries and extends the failure streak; a successful one resets it.

    A successful read also returns the feed's own title and site link (`FeedMeta`);
    a failed one returns none, so the values stored on the Source are left unchanged.
    """
    if isinstance(parsed_feed, Exception):
        return SourceRead(new_entries=[], health=_failed_health(source, None, today))
    status = getattr(parsed_feed, "status", None)
    malformed_and_empty = bool(getattr(parsed_feed, "bozo", False)) and not parsed_feed.entries
    if (status is not None and status >= 400) or malformed_and_empty:
        return SourceRead(new_entries=[], health=_failed_health(source, status, today))

    cutoff = date.fromisoformat(str(source["last_checked_at"]))
    new_entries = []
    # Seeded with the stored date: a feed that trims its history must not look silent sooner.
    stored_newest = _stored_date(source.get("newest_entry_at"))
    dates = [stored_newest] if stored_newest else []
    for entry in parsed_feed.entries:
        dated = entry_date(entry)
        if dated is None:
            continue
        if dated <= today:  # a future date (scheduled post, typo) would pin newest_entry_at
            dates.append(dated)
        if dated > cutoff:
            new_entries.append(entry)
    health = SourceHealth(
        consecutive_failures=0,
        failing_since=None,
        last_http_status=getattr(parsed_feed, "status", None),
        newest_entry_at=max(dates, default=None),
    )
    return SourceRead(new_entries=new_entries, health=health, meta=_feed_meta(parsed_feed))


def _write_after(source: dict, anchor: str, fields: NamedTuple) -> None:
    """Write `fields` onto a Source entry: existing keys in place, new ones after `anchor`."""
    for name, value in fields._asdict().items():
        if name not in source and anchor in source and hasattr(source, "insert"):
            source.insert(list(source).index(anchor) + 1, name, value)
        else:
            source[name] = value
        anchor = name


def record_health(source: dict, health: SourceHealth) -> None:
    """Write Source health onto a sources.yaml Source entry, beside `last_checked_at`.

    Existing health fields are overwritten in place; new ones are inserted after
    `last_checked_at` (on a ruamel round-trip mapping) so the entry stays readable.
    Nothing else on the entry changes: Scout records health but never touches `enabled`.
    """
    _write_after(source, "last_checked_at", health)


def record_feed_meta(source: dict, meta: FeedMeta) -> None:
    """Write the feed's title and site link onto a Source entry, after its health facts.

    Like `record_health`: existing fields are overwritten in place, new ones inserted
    after `newest_entry_at` (the last health field), and nothing else changes.
    """
    _write_after(source, SourceHealth._fields[-1], meta)


def round_trip_yaml() -> YAML:
    """The ruamel round-trip loader/dumper for the data files; keeps comments and layout."""
    ryaml = YAML()
    ryaml.preserve_quotes = True
    ryaml.default_flow_style = False
    ryaml.indent(mapping=2, sequence=4, offset=2)
    ryaml.representer.add_representer(
        type(None),
        lambda dumper, data: dumper.represent_scalar("tag:yaml.org,2002:null", "null"),
    )
    return ryaml


def main() -> None:
    parser = argparse.ArgumentParser(description="Scout new resources from RSS/Atom feeds")
    parser.add_argument("--dry-run", action="store_true", help="Run the judge but skip all writes")
    parser.add_argument("--limit", type=int, default=None, metavar="N", help="Process only first N candidates")
    parser.add_argument("--source", default=None, metavar="ID", help="Restrict to one source ID")
    parser.add_argument(
        "--automerge-decision", type=Path, default=None, metavar="CANDIDATES_YAML",
        help="Print the auto-merge gate decision for a candidates file as JSON, then exit",
    )
    parser.add_argument(
        "--cap", type=int, default=CANDIDATE_CAP, metavar="N",
        help=f"Candidate cap for --automerge-decision (default {CANDIDATE_CAP})",
    )
    args = parser.parse_args()

    if args.automerge_decision is not None:
        if args.cap < 0:
            parser.error("--cap must be a non-negative integer")
        print(json.dumps(automerge_decision(args.automerge_decision, RESOURCES_PATH, args.cap)))
        return

    ryaml = round_trip_yaml()

    with open(SOURCES_PATH) as f:
        sources_data = ryaml.load(f)
    with open(SEEN_PATH) as f:
        seen_data = ryaml.load(f)
    with open(RESOURCES_PATH) as f:
        resources_data = ryaml.load(f)

    sources = sources_data["sources"]
    if args.source:
        sources = [s for s in sources if s["id"] == args.source]
        if not sources:
            print(f"::error::No source with id '{args.source}'", file=sys.stderr)
            sys.exit(1)

    seen_urls = {item["url"] for item in (seen_data["seen"] or [])}
    existing_urls = {r["url"] for r in resources_data["resources"]}
    existing_slugs = {r["id"] for r in resources_data["resources"]}

    system = build_system_prompt(resources_data["sections"], resources_data["resources"])

    enabled = [s for s in sources if s.get("enabled", True)]
    print(f"Processing {len(enabled)} source(s)...")

    # Fetch every feed first; a Source whose fetch fails is left out, so it is not bumped.
    today = date.today()
    new_entries: dict[str, list] = {}
    health: dict[str, SourceHealth] = {}
    meta: dict[str, FeedMeta] = {}
    for source in enabled:
        source_id = source["id"]
        try:
            feed = feedparser.parse(source["url"], agent=USER_AGENT)
        except Exception as exc:
            feed = exc  # read_source records it as a failed fetch with no HTTP status
        read = read_source(source, feed, today)
        health[source_id] = read.health
        if read.meta is not None:
            meta[source_id] = read.meta
        if read.health.consecutive_failures:
            # Only source_id, integers and sanitized exception text: no line break can
            # smuggle a `::` workflow command out of a feed or network error.
            if isinstance(feed, Exception):
                reason = sanitize_text(str(feed))
            else:
                reason = f"HTTP status {read.health.last_http_status}"
            print(
                f"::warning::source {source_id}: feed fetch failed ({reason}); "
                f"{read.health.consecutive_failures} failed run(s) in a row",
                flush=True,
            )
            continue
        new_entries[source_id] = read.new_entries
        print(f"  [{source_id}] {len(new_entries[source_id])} new entry/entries since {source['last_checked_at']}")

    run = judge_sources(
        new_entries,
        functools.partial(judge.judge_entry, system),
        known_urls=seen_urls | existing_urls,
        existing_slugs=existing_slugs,
        limit=args.limit,
        max_calls=JUDGE_CALL_BUDGET,
        max_seconds=JUDGE_TIME_BUDGET_SECONDS,
    )

    print(
        f"\nSummary: {len(run.candidates)} included / {len(run.rejected)} rejected / "
        f"{run.evaluated} evaluated / {len(run.errors)} judge error(s)"
    )

    # The fail-closed exits below write no files at all, Source health included: the
    # workflow does not commit a failed run, so health written there would be discarded
    # anyway, and "no files written" stays a simple, whole-run guarantee. Every run that
    # writes sources.yaml — including one that stopped judging early — records
    # health for every enabled Source, however far its entries were judged.
    if run.errors:
        # Fail closed: a dead judge must never advance last_checked_at or record rejects.
        for err in run.errors:
            print(f"::error::{err}", file=sys.stderr)
        print(
            f"::error::{len(run.errors)} judge error(s); no files written. {judge_failure_hint(run)}",
            file=sys.stderr,
        )
        sys.exit(1)

    if run.usage_limit_hit and run.evaluated == 0:
        # No progress at all: nothing to keep, and a green run would hide that the
        # quota (shared with interactive use, ADR-0001) is gone.
        print(
            f"::error::Nothing was judged: judging stopped on {sanitize_text(run.stopped_early)}; "
            "no files written. The subscription "
            "quota is shared with interactive use (docs/adr/0001-oauth-token-for-ci.md); "
            "re-run once it resets.",
            file=sys.stderr,
        )
        sys.exit(1)

    if run.stopped_early is not None:
        # Unlike an error, an early stop leaves every judgment made so far real, so
        # keep them: rejects go to seen.yaml, includes become Resources, and only fully
        # judged Sources are bumped. Discarding them would re-judge the same entries
        # next run, so a backlog larger than one quota window could never clear.
        print(
            f"::warning::Judging stopped on {sanitize_text(run.stopped_early)}. "
            f"The {run.evaluated} judgment(s) made are "
            "saved; unfinished Sources keep their last_checked_at, so the unjudged "
            "remainder is picked up next run.",
            flush=True,
        )

    if args.dry_run:
        print("Dry run — no files written.")
        return

    # candidates.yaml — plain yaml, new file each run
    write_candidates(CANDIDATES_PATH, run)
    print(f"Wrote {CANDIDATES_PATH}")

    # seen.yaml — append rejects
    if run.rejected:
        if seen_data["seen"] is None:
            seen_data["seen"] = []
        seen_data["seen"].extend(run.rejected)
        with open(SEEN_PATH, "w") as f:
            ryaml.dump(seen_data, f)
        print(f"Updated {SEEN_PATH} (+{len(run.rejected)} rejected)")

    # sources.yaml — bump last_checked_at only on Sources whose every new entry was judged
    bumped = [s for s in enabled if s["id"] in run.fully_judged]
    for source in bumped:
        source["last_checked_at"] = date(today.year, today.month, today.day)
    # Source health is about the feed, not the judge: every enabled Source gets it.
    # So is the feed's title and site link, recorded only when the read succeeded.
    for source in enabled:
        record_health(source, health[source["id"]])
        if source["id"] in meta:
            record_feed_meta(source, meta[source["id"]])
    with open(SOURCES_PATH, "w") as f:
        ryaml.dump(sources_data, f)
    print(f"Updated {SOURCES_PATH} (last_checked_at → {today} on {len(bumped)}/{len(enabled)} source(s))")


if __name__ == "__main__":
    main()
