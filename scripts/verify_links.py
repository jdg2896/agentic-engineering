#!/usr/bin/env python3
"""Verify link health for all resources in resources.yaml."""

from __future__ import annotations

import argparse
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import requests
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parent.parent
RESOURCES_PATH = ROOT / "resources.yaml"
REPORT_PATH = ROOT / "verification_report.json"

TIMEOUT = 15
MAX_REDIRECTS = 5
MAX_WORKERS = 10
SOFT_404_SNIFF_BYTES = 32_768
QUARANTINE_GRACE_DAYS = 21

_REPO = os.environ.get("GITHUB_REPOSITORY", "jdg2896/agentic-engineering")
USER_AGENT = f"agentic-engineering-bot (+https://github.com/{_REPO})"

_TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.I)
# Title-based soft-404 markers. We only inspect <title> to avoid false positives from
# legitimate pages that happen to mention "not found" in body copy.
_SOFT_404_TITLE_PATTERNS = [
    re.compile(r"^\s*(?:404\b|page not found\b|not found\b)", re.I),
    re.compile(r"\bpage not found\b", re.I),
]


def _normalize(url: str) -> tuple[str, str]:
    """Return (host, path) after stripping www., normalizing trailing slash."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/") or "/"
    return host, path


def _same_location(original: str, final: str) -> bool:
    """True if original and final resolve to the same host+path (ignoring scheme/www/slash)."""
    return _normalize(original) == _normalize(final)


def _looks_like_soft_404(html: str) -> bool:
    """True if the HTML's <title> matches a soft-404 marker (e.g. SPAs that 200 a not-found page)."""
    match = _TITLE_RE.search(html)
    if not match:
        return False
    title = match.group(1).strip()
    return any(p.search(title) for p in _SOFT_404_TITLE_PATTERNS)


def _fetch_html_snippet(
    session: requests.Session, url: str, headers: dict, max_bytes: int = SOFT_404_SNIFF_BYTES
) -> str | None:
    """Fetch up to max_bytes of an HTML response. Returns None if not HTML or on error."""
    try:
        resp = session.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=True, stream=True)
    except requests.RequestException:
        return None
    try:
        if "html" not in resp.headers.get("content-type", "").lower():
            return None
        chunk = resp.raw.read(max_bytes, decode_content=True) or b""
        return chunk.decode("utf-8", errors="replace")
    except requests.RequestException:
        return None
    finally:
        resp.close()


def check_url(resource: dict) -> dict:
    url = resource["url"]
    paywall = resource.get("paywall", False)

    session = requests.Session()
    session.max_redirects = MAX_REDIRECTS
    headers = {"User-Agent": USER_AGENT}

    status_code: int | None = None
    final_url = url
    error: str | None = None
    content_type = ""

    try:
        resp = session.head(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
        if resp.status_code in (405, 501):
            resp = session.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
        status_code = resp.status_code
        final_url = resp.url
        content_type = resp.headers.get("content-type", "")
    except requests.TooManyRedirects:
        error = "too_many_redirects"
    except requests.Timeout:
        error = "timeout"
    except requests.ConnectionError as exc:
        error = f"connection_error: {exc}"
    except requests.RequestException as exc:
        error = str(exc)

    result: dict = {
        "id": resource["id"],
        "kind": resource.get("kind", "resource"),
        "url": url,
        "status_code": status_code,
        "final_url": final_url if final_url != url else None,
        "error": error,
    }

    if error:
        result["outcome"] = "dead"
        return result

    if status_code in (401, 403) and paywall:
        result["outcome"] = "paywall_skipped"
        return result

    if status_code in (401, 403):
        result["outcome"] = "ok"
        return result

    if status_code is None or status_code >= 400:
        result["outcome"] = "dead"
        return result

    if 200 <= status_code < 300:
        # Soft-404 sniff: SPAs (e.g. platform.claude.com) return 200 with a "Not Found" body.
        # Skip cleanly if response isn't HTML or the GET fails.
        if "html" in content_type.lower() or not content_type:
            body = _fetch_html_snippet(session, final_url, headers)
            if body and _looks_like_soft_404(body):
                result["error"] = "soft_404"
                result["outcome"] = "dead"
                return result
        if _same_location(url, final_url):
            result["outcome"] = "ok"
        else:
            result["outcome"] = "migrated"
        return result

    # Unexpected non-redirect 3xx without following (shouldn't reach here)
    result["outcome"] = "dead"
    return result


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "wf"


def collect_targets(data) -> list[dict]:
    """Build a flat list of verification targets from both resources and worth_following."""
    targets: list[dict] = []
    for r in data.get("resources") or []:
        targets.append({
            "id": r["id"],
            "url": r["url"],
            "paywall": r.get("paywall", False),
            "kind": "resource",
        })
    for r in data.get("worth_following") or []:
        targets.append({
            "id": f"wf:{_slugify(r['name'])}",
            "url": r["url"],
            "paywall": False,
            "kind": "worth_following",
        })
    return targets


def run_verification(targets: list, limit: int | None) -> list[dict]:
    targets = targets[:limit] if limit is not None else targets
    results: list[dict] = [{}] * len(targets)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(check_url, dict(r)): i for i, r in enumerate(targets)}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                r = targets[idx]
                results[idx] = {
                    "id": r["id"],
                    "kind": r.get("kind", "resource"),
                    "url": r["url"],
                    "status_code": None,
                    "final_url": None,
                    "error": str(exc),
                    "outcome": "dead",
                }

    return results


def build_report(results: list[dict]) -> dict:
    buckets: dict[str, list] = {"ok": [], "migrated": [], "dead": [], "paywall_skipped": []}
    for r in results:
        buckets[r["outcome"]].append(r)
    return {
        "date": date.today().isoformat(),
        "counts": {k: len(v) for k, v in buckets.items()},
        "ok": buckets["ok"],
        "migrated": buckets["migrated"],
        "dead": buckets["dead"],
        "paywall_skipped": buckets["paywall_skipped"],
    }


def _parse_yaml_date(value) -> date | None:
    """ruamel preserves YAML dates as datetime.date; tolerate ISO strings too."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    return None


def _dead_reason(result: dict) -> str:
    error = result.get("error")
    if error:
        return error
    code = result.get("status_code")
    return str(code) if code is not None else "unknown"


def compute_state_changes(yaml_data, results: list[dict], today: date) -> list[dict]:
    """Plan per-entry state transitions. Pure: does not mutate yaml_data.

    Returns a list of change dicts (one per result that matched a YAML entry):
      {
        "entry": <ruamel CommentedMap>,
        "kind": "resource"|"worth_following",
        "id": str,
        "url": str,
        "top_7": bool,
        "set": {field: value, ...},
        "clear": [field, ...],
        "transition": "verified"|"first_dead"|"quarantine"|"recovery"|"migrated_clear"|"no_change",
        "reason": str | None,
        "first_dead_at": str | None,
      }
    """
    resources_by_id = {r["id"]: r for r in (yaml_data.get("resources") or [])}
    wf_by_url = {w["url"]: w for w in (yaml_data.get("worth_following") or [])}
    top_7_set = set(yaml_data.get("top_7") or [])

    changes: list[dict] = []
    for result in results:
        kind = result.get("kind", "resource")
        rid = result["id"]
        if kind == "resource":
            entry = resources_by_id.get(rid)
        else:
            entry = wf_by_url.get(result["url"])
        if entry is None:
            continue

        outcome = result["outcome"]
        q_at = _parse_yaml_date(entry.get("quarantined_at"))
        fd_at = _parse_yaml_date(entry.get("first_dead_at"))

        set_fields: dict = {}
        clear_fields: list[str] = []
        transition = "no_change"
        reason: str | None = None

        if outcome in ("ok", "paywall_skipped"):
            set_fields["verified_at"] = today
            if fd_at is not None:
                clear_fields.append("first_dead_at")
            if q_at is not None:
                clear_fields.extend(["quarantined_at", "quarantine_reason"])
                transition = "recovery"
            else:
                transition = "verified"
        elif outcome == "migrated":
            final_url = result.get("final_url")
            if final_url and final_url != entry.get("url"):
                set_fields["url"] = final_url
            set_fields["verified_at"] = today
            if fd_at is not None:
                clear_fields.append("first_dead_at")
            transition = "migrated"
        elif outcome == "dead":
            if q_at is not None:
                pass  # already quarantined: no-op, don't restart the clock
            elif fd_at is None:
                set_fields["first_dead_at"] = today
                transition = "first_dead"
            elif (today - fd_at).days >= QUARANTINE_GRACE_DAYS:
                reason = _dead_reason(result)
                set_fields["quarantined_at"] = today
                set_fields["quarantine_reason"] = reason
                transition = "quarantine"

        changes.append({
            "entry": entry,
            "kind": kind,
            "id": rid,
            "url": result["url"],
            "top_7": rid in top_7_set,
            "set": set_fields,
            "clear": clear_fields,
            "transition": transition,
            "reason": reason,
            "first_dead_at": fd_at.isoformat() if fd_at else None,
        })

    return changes


def apply_state_changes(changes: list[dict]) -> None:
    """Commit planned mutations to the YAML entries."""
    for ch in changes:
        entry = ch["entry"]
        for field, value in ch["set"].items():
            entry[field] = value
        for field in ch["clear"]:
            if field in entry:
                del entry[field]


def report_transitions(changes: list[dict]) -> dict:
    """Summarize quarantine/recovery events for verification_report.json."""
    newly_quarantined: list[dict] = []
    recovered: list[dict] = []
    accumulating_dead: list[dict] = []
    for ch in changes:
        t = ch["transition"]
        if t == "quarantine":
            newly_quarantined.append({
                "id": ch["id"],
                "kind": ch["kind"],
                "url": ch["url"],
                "quarantine_reason": ch["reason"],
                "top_7": ch["top_7"],
                "first_dead_at": ch["first_dead_at"],
            })
        elif t == "recovery":
            recovered.append({
                "id": ch["id"],
                "kind": ch["kind"],
                "url": ch["url"],
            })
        elif t == "first_dead" or (t == "no_change" and ch["first_dead_at"]):
            # Accumulating: dead but not yet at threshold. Useful for PR body.
            accumulating_dead.append({
                "id": ch["id"],
                "kind": ch["kind"],
                "url": ch["url"],
                "first_dead_at": ch["first_dead_at"] or (
                    ch["set"].get("first_dead_at").isoformat()
                    if isinstance(ch["set"].get("first_dead_at"), date) else None
                ),
            })
    return {
        "newly_quarantined": newly_quarantined,
        "recovered": recovered,
        "accumulating_dead": accumulating_dead,
    }


def print_summary(report: dict) -> None:
    counts = report["counts"]
    print(
        f"Results: {counts['ok']} ok / {counts['dead']} dead / "
        f"{counts['migrated']} migrated / {counts['paywall_skipped']} paywall_skipped"
    )
    nq = report.get("newly_quarantined") or []
    rec = report.get("recovered") or []
    acc = report.get("accumulating_dead") or []
    if nq or rec or acc:
        print(
            f"State: {len(nq)} newly_quarantined / {len(rec)} recovered / "
            f"{len(acc)} accumulating_dead"
        )
    if report["dead"]:
        print("\nDead:")
        for r in report["dead"]:
            code = r["status_code"] or r["error"]
            print(f"  [{code}] {r['url']}")
    if nq:
        print("\nNewly quarantined:")
        for r in nq:
            flag = " 🚩 top-7" if r["top_7"] else ""
            print(f"  [{r['quarantine_reason']}]{flag} {r['url']}")
    if rec:
        print("\nRecovered:")
        for r in rec:
            print(f"  {r['url']}")
    if report["migrated"]:
        print("\nMigrated:")
        for r in report["migrated"]:
            print(f"  {r['url']} → {r['final_url']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify links in resources.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Run checks without modifying resources.yaml")
    parser.add_argument("--limit", type=int, default=None, metavar="N", help="Verify only the first N entries")
    args = parser.parse_args()

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.default_flow_style = False
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.representer.add_representer(
        type(None),
        lambda dumper, data: dumper.represent_scalar("tag:yaml.org,2002:null", "null"),
    )

    with open(RESOURCES_PATH) as f:
        data = yaml.load(f)

    targets = collect_targets(data)
    total = min(args.limit, len(targets)) if args.limit else len(targets)
    print(f"Verifying {total} targets (resources + worth_following)...")

    results = run_verification(targets, args.limit)
    today = date.today()
    changes = compute_state_changes(data, results, today)

    report = build_report(results)
    report.update(report_transitions(changes))

    print_summary(report)

    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Report written to {REPORT_PATH}")

    if not args.dry_run:
        apply_state_changes(changes)
        with open(RESOURCES_PATH, "w") as f:
            yaml.dump(data, f)
        print(f"Updated {RESOURCES_PATH}")
    else:
        print("Dry run — resources.yaml not modified.")


if __name__ == "__main__":
    main()
