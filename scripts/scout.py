#!/usr/bin/env python3
"""Scout new resources from RSS/Atom feeds and evaluate candidates via Claude."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

import anthropic
import feedparser
import yaml as pyyaml
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parent.parent
SOURCES_PATH = ROOT / "sources.yaml"
SEEN_PATH = ROOT / "scout" / "seen.yaml"
RESOURCES_PATH = ROOT / "resources.yaml"
CANDIDATES_PATH = ROOT / "candidates.yaml"

_REPO = os.environ.get("GITHUB_REPOSITORY", "jdg2896/agentic-engineering")
USER_AGENT = f"agentic-engineering-bot (+https://github.com/{_REPO})"

TOOL_DEF = {
    "name": "judge_candidate",
    "description": "Editorial judgment on a candidate resource.",
    "input_schema": {
        "type": "object",
        "required": ["decision", "section", "slug", "title", "author", "type", "blurb", "tags", "rationale"],
        "properties": {
            "decision": {"type": "string", "enum": ["include", "reject"]},
            "section": {"type": "string"},
            "slug": {"type": "string"},
            "title": {"type": "string"},
            "author": {"type": "string"},
            "type": {"type": "string", "enum": ["article", "paper", "docs", "repo", "video", "spec", "course", "book"]},
            "license": {"type": ["string", "null"]},
            "blurb": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "rationale": {"type": "string"},
        },
    },
}


def build_system_prompt(sections: list, resources: list) -> str:
    lines = [
        "You are an editorial assistant for a curated resource guide for backend engineers building agentic systems in developer workflows.",
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
        "- Content must be relevant to backend engineers building agentic systems in dev workflows",
        "- Vendor blog posts are acceptable only if they contain reproducible techniques or architecture decisions",
        "- Reject if a substantially similar resource already exists in resources.yaml",
        "- News/announcements (new model release, funding round) → reject unless the announcement post itself contains technical content",
        "- GitHub release notes → include only if the release introduces a meaningful new capability (not just patch/bugfix)",
        "- Security content → include only if it covers agent-specific attack surface (prompt injection, indirect injection, tool misuse)",
        "",
        "Call `judge_candidate` with your decision.",
    ]

    return "\n".join(lines)


def judge_entry(
    client: anthropic.Anthropic,
    system: list,
    title: str,
    url: str,
    summary: str,
    source_id: str,
) -> dict:
    user_prompt = (
        f"Evaluate this candidate resource for inclusion.\n\n"
        f"Title: {title}\n"
        f"URL: {url}\n"
        f"Source feed: {source_id}\n"
        f"Summary/description:\n{summary or '(no summary available)'}"
    )
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=system,
        tools=[TOOL_DEF],
        tool_choice={"type": "tool", "name": "judge_candidate"},
        messages=[{"role": "user", "content": user_prompt}],
    )
    return next(b.input for b in resp.content if b.type == "tool_use")


def safe_slug(base: str, existing: set[str]) -> str:
    if base not in existing:
        return base
    return base + "-2"


def main() -> None:
    parser = argparse.ArgumentParser(description="Scout new resources from RSS/Atom feeds")
    parser.add_argument("--dry-run", action="store_true", help="Make API calls but skip all writes")
    parser.add_argument("--limit", type=int, default=None, metavar="N", help="Process only first N candidates")
    parser.add_argument("--source", default=None, metavar="ID", help="Restrict to one source ID")
    args = parser.parse_args()

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

    client = anthropic.Anthropic()
    system_text = build_system_prompt(resources_data["sections"], resources_data["resources"])
    # Build system once; first call pays cache-write cost, subsequent calls hit cache
    system = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]

    candidates: list[dict] = []
    new_seen: list[dict] = []
    evaluated = 0
    limit_reached = False

    enabled = [s for s in sources if s.get("enabled", True)]
    print(f"Processing {len(enabled)} source(s)...")

    for source in enabled:
        if limit_reached:
            break
        source_id = source["id"]
        try:
            feed = feedparser.parse(source["url"], agent=USER_AGENT)
            cutoff = date.fromisoformat(str(source["last_checked_at"]))

            new_entries = [
                e for e in feed.entries
                if e.get("published_parsed")
                and date(*e.published_parsed[:3]) > cutoff
            ]
            print(f"  [{source_id}] {len(new_entries)} new entry/entries since {cutoff}")

            for entry in new_entries:
                if limit_reached:
                    break
                url = entry.get("link", "")
                if not url:
                    continue
                if url in seen_urls or url in existing_urls:
                    print(f"    skip (known): {url}")
                    continue

                title = entry.get("title", "(untitled)")
                content_list = entry.get("content", [])
                content_val = content_list[0].get("value", "") if content_list else ""
                summary = entry.get("summary", "") or content_val

                try:
                    result = judge_entry(client, system, title, url, summary, source_id)
                except Exception as exc:
                    print(f"::error::source {source_id}: API error for '{title}': {exc}", file=sys.stderr)
                    continue

                decision = result["decision"]
                if decision == "include":
                    raw_slug = result.get("slug", "")
                    slug = safe_slug(raw_slug, existing_slugs)
                    existing_slugs.add(slug)
                    candidates.append({
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
                    new_seen.append({
                        "url": url,
                        "title": title,
                        "source_id": source_id,
                        "rejected_at": str(date.today()),
                    })
                    print(f"    [reject]  {title}")
                    print(f"              {result.get('rationale')}")

                evaluated += 1
                if args.limit is not None and evaluated >= args.limit:
                    limit_reached = True
                    print(f"\n  --limit {args.limit} reached, stopping early.")
                    break

        except Exception as exc:
            print(f"::error::source {source_id}: {exc}", file=sys.stderr)
            continue

    print(f"\nSummary: {len(candidates)} included / {len(new_seen)} rejected / {evaluated} evaluated")

    if args.dry_run:
        print("Dry run — no files written.")
        return

    # candidates.yaml — plain yaml, new file each run
    CANDIDATES_PATH.write_text(
        pyyaml.dump({"candidates": candidates}, sort_keys=False, allow_unicode=True)
    )
    print(f"Wrote {CANDIDATES_PATH}")

    # seen.yaml — append rejects
    if new_seen:
        if seen_data["seen"] is None:
            seen_data["seen"] = []
        seen_data["seen"].extend(new_seen)
        with open(SEEN_PATH, "w") as f:
            ryaml.dump(seen_data, f)
        print(f"Updated {SEEN_PATH} (+{len(new_seen)} rejected)")

    # sources.yaml — bump last_checked_at on every processed source
    today = date.today()
    for source in enabled:
        source["last_checked_at"] = date(today.year, today.month, today.day)
    with open(SOURCES_PATH, "w") as f:
        ryaml.dump(sources_data, f)
    print(f"Updated {SOURCES_PATH} (last_checked_at → {today})")


if __name__ == "__main__":
    main()
