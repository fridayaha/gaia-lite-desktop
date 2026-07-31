#!/usr/bin/env python3
"""GitHub API 指标采集脚本

通过 GitHub REST API 实时采集候选开源项目的社区与版本数据，
用于 docs/13_open_source_tech_selection_evaluation.md 报告。

依赖：仅使用 Python 标准库（urllib.request + json），不引入第三方依赖。

用法：
    python scripts/collect_github_metrics.py

输出：
    JSON 格式，包含 stars / forks / open_issues / license / language / pushed_at / updated_at
    及 latest release tag / published_at，可直接粘贴到选型报告表格中。
"""

import json
import sys
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CANDIDATE_REPOS = [
    "agentregistry-dev/agentregistry",
    "iflytek/skillhub",
    "modelcontextprotocol/registry",
    "artifacthub/hub",
    "backstage/backstage",
    "datahub-project/datahub",
    "open-metadata/OpenMetadata",
    "ckan/ckan",
    "tiangolo/fastapi",
    "django/django",
]

REQUEST_TIMEOUT = 15  # seconds
USER_AGENT = "hub-poc-collector/0.1"


def _github_request(path: str) -> dict | list:
    """Send an unauthenticated GitHub API v3 request and return parsed JSON."""
    url = f"https://api.github.com{path}"
    req = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    })
    with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def collect_repo_metadata(full_name: str) -> dict:
    """Fetch basic repo metadata from GitHub."""
    data = _github_request(f"/repos/{full_name}")
    lic = data.get("license") or {}
    return {
        "stars": data.get("stargazers_count"),
        "forks": data.get("forks_count"),
        "open_issues": data.get("open_issues_count"),
        "license": lic.get("spdx_id") or lic.get("name") or "Unknown",
        "language": data.get("language"),
        "pushed_at": data.get("pushed_at"),
        "updated_at": data.get("updated_at"),
    }


def collect_latest_release(full_name: str) -> dict:
    """Fetch the latest release info from GitHub."""
    releases = _github_request(f"/repos/{full_name}/releases?per_page=1")
    if releases:
        first = releases[0]
        return {
            "latest_release_tag": first.get("tag_name"),
            "release_published_at": first.get("published_at"),
        }
    return {
        "latest_release_tag": None,
        "release_published_at": None,
    }


def main() -> int:
    collected_at = datetime.now(timezone.utc).isoformat()
    results: dict[str, dict] = {}
    errors: list[str] = []

    for full_name in CANDIDATE_REPOS:
        try:
            meta = collect_repo_metadata(full_name)
            time.sleep(0.5)  # gentle rate limiting for unauthenticated requests
            release = collect_latest_release(full_name)
            results[full_name] = {**meta, **release}
            print(f"[OK] {full_name}", file=sys.stderr)
        except (HTTPError, URLError, json.JSONDecodeError) as exc:
            errors.append(f"{full_name}: {exc}")
            results[full_name] = {"error": str(exc)}
            print(f"[FAIL] {full_name}: {exc}", file=sys.stderr)

    output = {
        "collected_at": collected_at,
        "repositories": results,
    }

    if errors:
        output["errors"] = errors

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
