#!/usr/bin/env python3
"""Render README.md from resources.yaml + sources.yaml + templates/."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote, urlsplit

import jinja2
import yaml

ROOT = Path(__file__).resolve().parent.parent
RESOURCES_PATH = ROOT / "resources.yaml"
SOURCES_PATH = ROOT / "sources.yaml"
TEMPLATES_DIR = ROOT / "templates"
OUTPUT_PATH = ROOT / "README.md"


# RFC 3986 reserved characters that are harmless in a Markdown link destination, plus
# `%` so existing escapes survive. `()[]` are left out: they could end the link.
_LINK_URL_SAFE = ":/?#@!$&'*+,;=%"


def link_url(url: str) -> str:
    """Percent-encode everything in `url` that could break out of a `[text](url)` link.

    A last line of defense behind Scout's feed-URL validation: letters, digits,
    `-._~` and `_LINK_URL_SAFE` pass through, so ordinary URLs are unchanged, while
    whitespace, `()[]<>"`, backticks, backslashes and non-ASCII are encoded.
    """
    return quote(str(url), safe=_LINK_URL_SAFE)


def format_link(resource: dict) -> str:
    base = f"[{resource['title']}]({link_url(resource['url'])})"
    archived = " _(archived)_" if resource.get("archived") else ""
    return f"{base}{archived}"


def join_links(links: list[str]) -> str:
    if len(links) == 1:
        return links[0]
    if len(links) == 2:
        return f"{links[0]} and {links[1]}"
    return ", ".join(links[:-1]) + f", and {links[-1]}"


def format_attribution(author: str | None, blurb: str | None) -> str:
    has_author = bool(author)
    has_blurb = bool(blurb)
    if not has_author and not has_blurb:
        return ""
    parts: list[str] = []
    if has_author:
        parts.append(f"{author}.")
    if has_blurb:
        parts.append(blurb)
    return " — " + " ".join(parts)


def render_bullet(cluster_resources: list[dict]) -> str:
    first = cluster_resources[0]
    links = [format_link(r) for r in cluster_resources]
    # cluster_label: renders as "- **Label:** link1, link2, ..., linkN."
    # Used for the benchmarks line in section 9 where the original format is a
    # labelled comma list with no "and" before the final item.
    if first.get("cluster_label"):
        label = first["cluster_label"]
        return f"- **{label}:** {', '.join(links)}."
    return f"- {join_links(links)}{format_attribution(first.get('author'), first.get('blurb'))}"


def render_top_7_line(resource: dict) -> str:
    # top_7_title overrides the link text in the top-7 list when the section
    # listing and top-7 entry use different titles for the same URL.
    title = resource.get("top_7_title") or resource["title"]
    link = f"[{title}]({link_url(resource['url'])})"
    author = resource.get("top_7_author") or resource.get("author")
    blurb = resource.get("top_7_blurb") or resource.get("blurb")
    return f"{link}{format_attribution(author, blurb)}"


def group_section_bullets(resources: list[dict]) -> list[str]:
    """Group by cluster (preserving first-occurrence order), render each as a bullet."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in resources:
        cluster = r.get("cluster")
        key = ("cluster", cluster) if cluster else ("solo", r["id"])
        groups.setdefault(key, []).append(r)
    return [render_bullet(group) for group in groups.values()]


def build_populated_sections(
    sections: list[dict],
    resources: list[dict],
) -> list[dict]:
    visible = [
        r for r in resources
        if not r.get("superseded_by") and not r.get("hidden") and not r.get("quarantined_at")
    ]
    by_section: dict[str, list[dict]] = defaultdict(list)
    for r in visible:
        by_section[r["section"]].append(r)

    populated: list[dict] = []
    for section in sorted(sections, key=lambda s: s["order"]):
        section_resources = by_section.get(section["id"], [])
        if not section_resources:
            continue
        populated.append({
            "order": section["order"],
            "title": section["title"],
            "bullets": group_section_bullets(section_resources),
        })
    return populated


# A Source is Worth following once its lifetime Yield reaches this many Resources.
WORTH_FOLLOWING_MIN_YIELD = 3


class WorthFollowing(NamedTuple):
    """One entry in the guide's "Worth following" list."""

    name: str  # the feed's title (already escaped Markdown), else the Source URL's host
    url: str  # the feed's site link, else the scheme and host of the Source URL
    yield_count: int  # lifetime Yield: Resources attributed to the Source, any state
    main_section: str  # title of the section most of those Resources are in

    @property
    def line(self) -> str:
        noun = "Resource" if self.yield_count == 1 else "Resources"
        return f"{self.yield_count} {noun} in the guide, mostly {self.main_section}"


def worth_following(sources: list[dict], resources: list[dict], sections: list[dict]) -> list[WorthFollowing]:
    """The Sources Worth following, strongest first. Pure.

    Every Source not Retired or disabled (`enabled` false or null) whose lifetime Yield, the
    Resources with its `source_id` (archived, quarantined and hidden ones included), is at
    least `WORTH_FOLLOWING_MIN_YIELD`. Ordered by Yield descending, then name. The main
    section is the most frequent among its Resources; a tie goes to the earlier section.
    """
    section_order = {s["id"]: s["order"] for s in sections}
    section_title = {s["id"]: s["title"] for s in sections}
    by_source: dict[str, list[str]] = defaultdict(list)
    for r in resources:
        if r.get("source_id"):
            by_source[r["source_id"]].append(r["section"])

    entries: list[WorthFollowing] = []
    for source in sources:
        yielded = by_source.get(source["id"], [])
        if not source.get("enabled", True) or len(yielded) < WORTH_FOLLOWING_MIN_YIELD:
            continue
        counts = Counter(yielded)
        main = min(counts, key=lambda sid: (-counts[sid], section_order[sid]))
        parts = urlsplit(str(source["url"]))
        host = (parts.hostname or "").rstrip(".")
        netloc = f"{host}:{parts.port}" if parts.port else host
        entries.append(WorthFollowing(
            name=source.get("feed_title") or host.removeprefix("www."),
            url=source.get("site_url") or f"{parts.scheme}://{netloc}",
            yield_count=len(yielded),
            main_section=section_title[main],
        ))
    return sorted(entries, key=lambda e: (-e.yield_count, e.name.casefold(), e.name))


def _check_top_7_not_quarantined(top_7_slugs: list[str], resources_by_id: dict) -> None:
    """Hard-fail render if any top_7 slug points at a quarantined resource.

    A top-7 entry going dead is editorially significant: the maintainer must
    drop it from `top_7:` or unquarantine, not silently ship a shorter marquee.
    """
    quarantined = [
        (slug, resources_by_id[slug].get("quarantine_reason") or "unknown")
        for slug in top_7_slugs
        if resources_by_id[slug].get("quarantined_at")
    ]
    if quarantined:
        details = ", ".join(f"{slug} ({reason})" for slug, reason in quarantined)
        raise ValueError(
            f"top_7 references quarantined resource(s): {details}. "
            "Drop the slug from `top_7:` in resources.yaml, or unquarantine "
            "(remove `quarantined_at`) if the URL has recovered."
        )


def render() -> str:
    data = yaml.safe_load(RESOURCES_PATH.read_text())
    sources = yaml.safe_load(SOURCES_PATH.read_text())["sources"]

    resources = data["resources"]
    resources_by_id = {r["id"]: r for r in resources}

    populated_sections = build_populated_sections(data["sections"], resources)
    _check_top_7_not_quarantined(data["top_7"], resources_by_id)
    top_7_lines = [
        render_top_7_line(resources_by_id[slug])
        for slug in data["top_7"]
    ]
    worth_following_entries = worth_following(sources, resources, data["sections"])

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
        undefined=jinja2.StrictUndefined,
    )
    env.filters["link_url"] = link_url

    def _render(name: str, **ctx: object) -> str:
        return env.get_template(name).render(**ctx).rstrip("\n")

    parts = [
        _render("header.md"),
        _render("top_7.md", top_7_lines=top_7_lines),
        _render("sections.md.j2", populated_sections=populated_sections),
        "---",
        _render("worth_following.md", worth_following=worth_following_entries),
    ]
    return "\n\n".join(p for p in parts if p) + "\n"


def main() -> None:
    output = render()
    OUTPUT_PATH.write_text(output)


if __name__ == "__main__":
    main()
