#!/usr/bin/env python3
"""Verify link health for all resources in resources.yaml."""

from __future__ import annotations

import argparse
import json
import os
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

_REPO = os.environ.get("GITHUB_REPOSITORY", "jdg2896/agentic-engineering")
USER_AGENT = f"agentic-engineering-bot (+https://github.com/{_REPO})"


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


def check_url(resource: dict) -> dict:
    url = resource["url"]
    paywall = resource.get("paywall", False)

    session = requests.Session()
    session.max_redirects = MAX_REDIRECTS
    headers = {"User-Agent": USER_AGENT}

    status_code: int | None = None
    final_url = url
    error: str | None = None

    try:
        resp = session.head(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
        if resp.status_code in (405, 501):
            resp = session.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
        status_code = resp.status_code
        final_url = resp.url
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

    if status_code is None or status_code >= 400:
        result["outcome"] = "dead"
        return result

    if 200 <= status_code < 300:
        if _same_location(url, final_url):
            result["outcome"] = "ok"
        else:
            result["outcome"] = "migrated"
        return result

    # Unexpected non-redirect 3xx without following (shouldn't reach here)
    result["outcome"] = "dead"
    return result


def run_verification(resources: list, limit: int | None) -> list[dict]:
    targets = resources[:limit] if limit is not None else resources
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


def apply_updates(yaml_data: object, results: list[dict], today: date) -> None:
    """Mutate ruamel.yaml CommentedMap resources in-place for ok/paywall_skipped outcomes."""
    by_id = {r["id"]: r for r in results}
    for resource in yaml_data["resources"]:
        res_id = resource["id"]
        if res_id not in by_id:
            continue
        outcome = by_id[res_id]["outcome"]
        if outcome in ("ok", "paywall_skipped"):
            resource["verified_at"] = today.isoformat()


def print_summary(report: dict) -> None:
    counts = report["counts"]
    print(
        f"Results: {counts['ok']} ok / {counts['dead']} dead / "
        f"{counts['migrated']} migrated / {counts['paywall_skipped']} paywall_skipped"
    )
    if report["dead"]:
        print("\nDead:")
        for r in report["dead"]:
            code = r["status_code"] or r["error"]
            print(f"  [{code}] {r['url']}")
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

    with open(RESOURCES_PATH) as f:
        data = yaml.load(f)

    resources = data["resources"]
    print(f"Verifying {min(args.limit, len(resources)) if args.limit else len(resources)} resources...")

    results = run_verification(resources, args.limit)
    report = build_report(results)

    print_summary(report)

    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Report written to {REPORT_PATH}")

    if not args.dry_run:
        apply_updates(data, results, date.today())
        with open(RESOURCES_PATH, "w") as f:
            yaml.dump(data, f)
        print(f"Updated {RESOURCES_PATH}")
    else:
        print("Dry run — resources.yaml not modified.")


if __name__ == "__main__":
    main()
