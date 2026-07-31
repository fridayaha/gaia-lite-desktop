#!/usr/bin/env python3
"""
GitCode PR auto-merge script.
Checks all open PRs and merges those that are mergeable (no conflicts).
Only logs when an actual merge action is taken (success or failure).
"""

import json
import subprocess
from datetime import datetime


def get_token():
    with open("/root/.gitcode_token") as f:
        return f.read().strip()


TOKEN = get_token()
OWNER = "Ascend-SACT"
REPO = "union_agent"


def api_get(path):
    url = f"https://gitcode.com/api/v5/repos/{OWNER}/{REPO}/{path}"
    sep = "&" if "?" in path else "?"
    url += f"{sep}access_token={TOKEN}"
    cmd = ["curl", "-s", "-H", "User-Agent: Mozilla/5.0", "-H", "Accept: application/json", url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"error": r.stdout[:200]}


def api_put(path, data=None):
    url = f"https://gitcode.com/api/v5/repos/{OWNER}/{REPO}/{path}"
    sep = "&" if "?" in path else "?"
    url += f"{sep}access_token={TOKEN}"
    cmd = [
        "curl",
        "-s",
        "-w",
        "\n__HTTP__%{http_code}",
        "-X",
        "PUT",
        "-H",
        "User-Agent: Mozilla/5.0",
        "-H",
        "Accept: application/json",
        "-H",
        "Content-Type: application/json",
    ]
    if data:
        cmd += ["-d", json.dumps(data)]
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    parts = r.stdout.rsplit("\n__HTTP__", 1)
    body = parts[0]
    http_code = parts[1] if len(parts) > 1 else "0"
    try:
        return json.loads(body), int(http_code)
    except json.JSONDecodeError:
        return {"raw": body}, int(http_code)


def main():
    prs = api_get("pulls?state=open&per_page=50")
    if isinstance(prs, dict) and "error" in prs:
        return  # API error — stay silent
    if not prs:
        return  # No open PRs — stay silent

    for pr in prs:
        pr_num = pr.get("number")
        title = pr.get("title", "")
        author = pr.get("user", {}).get("login", "unknown")
        mergeable = pr.get("mergeable")
        is_draft = pr.get("draft", False)
        head_label = pr.get("head", {}).get("label", "")
        base_label = pr.get("base", {}).get("label", "")

        if is_draft:
            continue

        # Get PR detail to confirm mergeable
        detail = api_get(f"pulls/{pr_num}")
        if isinstance(detail, dict) and "error" not in detail:
            mergeable = detail.get("mergeable", mergeable)

        if mergeable is True:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{now}] PR #{pr_num}: {title}")
            print(f"  author={author} | {head_label} -> {base_label}")
            print(f"  attempting merge...")

            result, http_code = api_put(
                f"pulls/{pr_num}/merge",
                {"Do": "merge", "MergeTitleField": f"{title} (#{pr_num})", "MergeMessageField": ""},
            )

            if http_code == 200 and (
                result.get("merged") is True or "merged" in str(result).lower()
            ):
                print(f"  -> MERGED successfully!")
            else:
                print(f"  -> merge FAILED (HTTP {http_code}): {result}")
        # else: not mergeable — stay silent


if __name__ == "__main__":
    main()
