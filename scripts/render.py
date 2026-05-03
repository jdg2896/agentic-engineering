#!/usr/bin/env python3
"""Render agentic-engineering.md from resources.yaml + templates/."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path

import jinja2
import yaml

ROOT = Path(__file__).resolve().parent.parent
RESOURCES_PATH = ROOT / "resources.yaml"
TEMPLATES_DIR = ROOT / "templates"
OUTPUT_PATH = ROOT / "agentic-engineering.md"

VERIFICATION_WINDOW_DAYS = 90


def _as_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def is_verified(resource: dict, today: date) -> bool:
    verified_at = _as_date(resource.get("verified_at"))
    if verified_at is None:
        return False
    return 0 <= (today - verified_at).days <= VERIFICATION_WINDOW_DAYS


def format_link(resource: dict, today: date) -> str:
    base = f"[{resource['title']}]({resource['url']})"
    mark = " ✓" if is_verified(resource, today) else ""
    archived = " _(archived)_" if resource.get("archived") else ""
    return f"{base}{mark}{archived}"


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


def render_bullet(cluster_resources: list[dict], today: date) -> str:
    first = cluster_resources[0]
    links = [format_link(r, today) for r in cluster_resources]
    return f"- {join_links(links)}{format_attribution(first.get('author'), first.get('blurb'))}"


def render_top_7_line(resource: dict, today: date) -> str:
    link = format_link(resource, today)
    author = resource.get("top_7_author") or resource.get("author")
    blurb = resource.get("top_7_blurb") or resource.get("blurb")
    return f"{link}{format_attribution(author, blurb)}"


def group_section_bullets(resources: list[dict], today: date) -> list[str]:
    """Group by cluster (preserving first-occurrence order), render each as a bullet."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in resources:
        cluster = r.get("cluster")
        key = ("cluster", cluster) if cluster else ("solo", r["id"])
        groups.setdefault(key, []).append(r)
    return [render_bullet(group, today) for group in groups.values()]


def build_populated_sections(
    sections: list[dict],
    resources: list[dict],
    today: date,
) -> list[dict]:
    visible = [
        r for r in resources
        if not r.get("superseded_by") and not r.get("hidden")
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
            "bullets": group_section_bullets(section_resources, today),
        })
    return populated


def compute_compilation_month_year(resources: list[dict], today: date) -> str:
    verified_dates = [
        _as_date(r.get("verified_at"))
        for r in resources
        if r.get("verified_at") is not None
    ]
    verified_dates = [d for d in verified_dates if d is not None]
    most_recent = max(verified_dates) if verified_dates else today
    return most_recent.strftime("%B %Y")


def render(today: date | None = None) -> str:
    today = today or date.today()
    data = yaml.safe_load(RESOURCES_PATH.read_text())

    resources = data["resources"]
    resources_by_id = {r["id"]: r for r in resources}

    populated_sections = build_populated_sections(data["sections"], resources, today)
    top_7_lines = [
        render_top_7_line(resources_by_id[slug], today)
        for slug in data["top_7"]
    ]
    compilation_month_year = compute_compilation_month_year(resources, today)

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
        undefined=jinja2.StrictUndefined,
    )

    def _render(name: str, **ctx: object) -> str:
        return env.get_template(name).render(**ctx).rstrip("\n")

    parts = [
        _render("header.md", compilation_month_year=compilation_month_year),
        _render("top_7.md", top_7_lines=top_7_lines),
        _render("sections.md.j2", populated_sections=populated_sections),
        "---",
        _render("opinionated_stack.md"),
        "---",
        _render("caveats.md"),
    ]
    return "\n\n".join(p for p in parts if p) + "\n"


def main() -> None:
    output = render()
    OUTPUT_PATH.write_text(output)


if __name__ == "__main__":
    main()
