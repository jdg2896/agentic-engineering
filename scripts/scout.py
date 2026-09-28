#!/usr/bin/env python3
"""Scout new resources from RSS/Atom feeds and evaluate candidates via Claude Code headless."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import NamedTuple

import feedparser
import yaml as pyyaml
from ruamel.yaml import YAML

import judge as judge_runner

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
        "- Must be substantive technical content, not marketing or press release",
        '- No listicles, SEO-optimised roundups, or "X things you should know" posts',
        "- Papers must have practical infrastructure implications, not pure ML theory",
        "- Tools must be production-ready or notable open-source research artefacts",
        "- Content must be about building, evaluating, operating, or securing agentic systems — any engineering discipline (FE, BE, infra, QA, data)",
        "- Vendor blog posts are acceptable only if they contain reproducible techniques or architecture decisions",
        "- Reject if a substantially similar resource already exists in resources.yaml",
        "- News/announcements (new model release, funding round) → reject unless the announcement post itself contains technical content",
        "- GitHub release notes → include only if the release introduces a meaningful new capability (not just patch/bugfix)",
        "- Security content → include only if it covers agent-specific attack surface (prompt injection, indirect injection, tool misuse)",
        "",
        "Return your editorial judgment as structured output matching the provided JSON schema.",
    ]

    return "\n".join(lines)


def safe_slug(base: str, existing: set[str]) -> str:
    if base not in existing:
        return base
    return base + "-2"


@dataclass
class ScoutRun:
    """Outcome of judging one run's new entries."""

    candidates: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    evaluated: int = 0
    # Sources whose every new entry was judged; only these may bump last_checked_at.
    fully_judged: set[str] = field(default_factory=set)


def judge_sources(
    new_entries: dict[str, list],
    judge: Callable[[str, str, str, str], dict],
    known_urls: set[str],
    existing_slugs: set[str],
    limit: int | None = None,
) -> ScoutRun:
    """Judge each Source's new entries and record which Sources were fully judged.

    `judge(title, url, summary, source_id)` returns the judgment dict or raises;
    the first raise is recorded in `errors` and stops judging, since any error
    fails the run. Entries in `known_urls` are skipped. Judging also stops once
    `limit` entries are judged. Sources left unfinished by either stop are not
    in `fully_judged`.
    """
    run = ScoutRun()
    slugs = set(existing_slugs)
    for source_id, entries in new_entries.items():
        for entry in entries:
            url = entry.get("link", "")
            if not url:
                continue
            if url in known_urls:
                print(f"    skip (known): {url}")
                continue
            if limit is not None and run.evaluated >= limit:
                print(f"\n  --limit {limit} reached, stopping early.")
                return run
            title = entry.get("title", "(untitled)")
            content_list = entry.get("content", [])
            content_val = content_list[0].get("value", "") if content_list else ""
            summary = entry.get("summary", "") or content_val

            try:
                result = judge(title, url, summary, source_id)
            except Exception as exc:
                # Any error fails the run, so further judge calls would only burn quota.
                run.errors.append(f"source {source_id}: judge error for '{title}' ({url}): {exc}")
                return run
            run.evaluated += 1
            if result["decision"] == "include":
                slug = safe_slug(result.get("slug", ""), slugs)
                slugs.add(slug)
                run.candidates.append({
                    "slug": slug,
                    "source_id": source_id,
                    "url": url,
                    "title": result.get("title", title),
                    "author": result.get("author", ""),
                    "section": result.get("section", ""),
                    "type": result.get("type", "article"),
                    "license": result.get("license"),
                    "blurb": result.get("blurb", ""),
                    "tags": result.get("tags", []),
                    "rationale": result.get("rationale", ""),
                })
                print(f"    [include] {title}")
                print(f"              {url}")
                print(f"              section={result.get('section')}  type={result.get('type')}")
                print(f"              blurb: {result.get('blurb')}")
            else:
                run.rejected.append({
                    "url": url,
                    "title": title,
                    "source_id": source_id,
                    "rejected_at": str(date.today()),
                })
                print(f"    [reject]  {title}")
                print(f"              {result.get('rationale')}")
        run.fully_judged.add(source_id)
    return run


CANDIDATE_CAP = 8
BASE_LABELS = ["automated", "scout"]
SKIPPED_LABEL = "auto-merge-skipped"
VALID_TYPES = tuple(judge_runner.JUDGMENT_SCHEMA["properties"]["type"]["enum"])


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
        if c.get("section") not in sections:
            reasons.append(f"candidate `{c.get('slug')}` has unknown section `{c.get('section')}`")
        if c.get("type") not in types:
            reasons.append(f"candidate `{c.get('slug')}` has unknown type `{c.get('type')}`")
    labels = list(BASE_LABELS) if not reasons else [*BASE_LABELS, SKIPPED_LABEL]
    return Decision(not reasons, reasons, labels)


def load_candidates(path: Path) -> list[dict]:
    """Read candidates.yaml; a missing or empty file means zero Candidates."""
    if not path.exists():
        return []
    return (pyyaml.safe_load(path.read_text()) or {}).get("candidates") or []


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
    which stays hand-curated (ADR-0002).
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
            "verified_at": None,
            "archived": False,
            "paywall": False,
            "superseded_by": None,
            "notes": None,
        })


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

    ryaml = YAML()
    ryaml.preserve_quotes = True
    ryaml.default_flow_style = False
    ryaml.indent(mapping=2, sequence=4, offset=2)
    ryaml.representer.add_representer(
        type(None),
        lambda dumper, data: dumper.represent_scalar("tag:yaml.org,2002:null", "null"),
    )

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

    def judge(title: str, url: str, summary: str, source_id: str) -> dict:
        # Raises JudgeError (JudgeAuthError carries the token-rotation hint) on failure.
        return judge_runner.judge_entry(system, title, url, summary, source_id)

    enabled = [s for s in sources if s.get("enabled", True)]
    print(f"Processing {len(enabled)} source(s)...")

    # Fetch every feed first; a Source whose feed fails is left out, so it is not bumped.
    new_entries: dict[str, list] = {}
    for source in enabled:
        source_id = source["id"]
        try:
            feed = feedparser.parse(source["url"], agent=USER_AGENT)
            cutoff = date.fromisoformat(str(source["last_checked_at"]))
            new_entries[source_id] = [
                e for e in feed.entries
                if e.get("published_parsed")
                and date(*e.published_parsed[:3]) > cutoff
            ]
            print(f"  [{source_id}] {len(new_entries[source_id])} new entry/entries since {cutoff}")
        except Exception as exc:
            print(f"::error::source {source_id}: {exc}", file=sys.stderr)

    run = judge_sources(
        new_entries,
        judge,
        known_urls=seen_urls | existing_urls,
        existing_slugs=existing_slugs,
        limit=args.limit,
    )

    print(
        f"\nSummary: {len(run.candidates)} included / {len(run.rejected)} rejected / "
        f"{run.evaluated} evaluated / {len(run.errors)} judge error(s)"
    )

    if run.errors:
        # Fail closed: a dead judge must never advance last_checked_at or record rejects.
        for err in run.errors:
            print(f"::error::{err}", file=sys.stderr)
        print(
            f"::error::{len(run.errors)} judge error(s); no files written. "
            "Check the judge credential (see docs/adr/0001-oauth-token-for-ci.md).",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.dry_run:
        print("Dry run — no files written.")
        return

    # candidates.yaml — plain yaml, new file each run
    CANDIDATES_PATH.write_text(
        pyyaml.dump({"candidates": run.candidates}, sort_keys=False, allow_unicode=True)
    )
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
    today = date.today()
    bumped = [s for s in enabled if s["id"] in run.fully_judged]
    for source in bumped:
        source["last_checked_at"] = date(today.year, today.month, today.day)
    with open(SOURCES_PATH, "w") as f:
        ryaml.dump(sources_data, f)
    print(f"Updated {SOURCES_PATH} (last_checked_at → {today} on {len(bumped)}/{len(enabled)} source(s))")


if __name__ == "__main__":
    main()
