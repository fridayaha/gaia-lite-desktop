#!/usr/bin/env python3
"""Secret Scanner CLI Spike — 验证 Betterleaks / Gitleaks CLI 可用性和输出格式。

警告：
- 这不是生产代码。
- 仅用于本地手工验证。
- 所有 secret 样例都是假的（placeholder），不包含真实凭据。
- 不提交到 CI。
- 不修改任何生产代码。

用法：
    python tools/spikes/secret_scanner_cli_spike.py [--provider betterleaks|gitleaks|all]

前提：
    需要预先安装 Betterleaks 和/或 Gitleaks（brew install / go install）。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

FAKE_SECRETS = {
    "fake_github_token": "ghp_fakeSpikeToken1234567890abcdefghijklmnop",
    "fake_aws_key": "AKIAFAKESPIKEACCESSKEY00000001",
    "fake_generic_api": "api_key_fake_spike_test_1234567890",
    "fake_jwt": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.fake.fake",
}

PLACEHOLDER_CONFIG = """{
  "name": "spike-test",
  "description": "spike test asset with fake secrets for CLI validation",
  "config": {
    "api_key": "FAKE_SPIKE_KEY_1234567890",
    "database_url": "postgres://user:password@localhost:5432/db",
    "aws_access_key": "AKIAFAKESPIKE",
    "github_token": "ghp_abcdefghijklmno",
    "secret_env": "SECRET=this_is_a_fake_secret_value_for_testing"
  }
}
"""


def check_tool(name: str) -> tuple[bool, str | None]:
    path = shutil.which(name)
    if not path:
        return False, None
    return True, path


def run_command(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"
    except FileNotFoundError:
        return -1, "", f"binary not found: {cmd[0]}"
    except Exception as e:
        return -1, "", str(e)


def spike_gitleaks(tmpdir: str) -> dict:
    result = {"tool": "gitleaks", "installed": False}
    installed, path = check_tool("gitleaks")
    if not installed:
        result["error"] = "gitleaks not installed"
        return result
    result["installed"] = True
    result["path"] = path

    ver_rc, ver_out, _ = run_command(["gitleaks", "version"])
    result["version"] = ver_out.strip() if ver_rc == 0 else f"rc={ver_rc}"

    report_path = os.path.join(tmpdir, "gitleaks_report.json")
    cmd = [
        "gitleaks", "dir", tmpdir,
        "--report-format=json",
        f"--report-path={report_path}",
        "--redact",
        "--timeout=30",
    ]
    rc, stdout, stderr = run_command(cmd, timeout=35)
    result["exit_code"] = rc
    result["stderr"] = stderr[:500] if stderr else None

    if rc == 0:
        result["findings_count"] = 0
        result["report"] = []
    elif rc == 1 and os.path.exists(report_path):
        with open(report_path) as f:
            data = json.load(f)
        result["findings_count"] = len(data)
        result["report"] = data[:3] if data else []
        result["fields"] = list(data[0].keys()) if data else []
        result["has_secret_field"] = any("Secret" in f for f in data)
    elif rc == 126:
        result["error"] = "unknown flag (126)"
    else:
        result["error"] = f"unexpected rc={rc}"

    return result


def spike_betterleaks(tmpdir: str) -> dict:
    result = {"tool": "betterleaks", "installed": False}
    installed, path = check_tool("betterleaks")
    if not installed:
        result["error"] = "betterleaks not installed"
        return result
    result["installed"] = True
    result["path"] = path

    ver_rc, ver_out, _ = run_command(["betterleaks", "--version"])
    result["version"] = ver_out.strip() if ver_rc == 0 else f"rc={ver_rc}"

    report_path = os.path.join(tmpdir, "betterleaks_report.json")
    cmd = [
        "betterleaks", "dir", tmpdir,
        "-f", "json",
        "-r", report_path,
    ]
    rc, stdout, stderr = run_command(cmd, timeout=35)
    result["exit_code"] = rc
    result["stderr"] = stderr[:500] if stderr else None

    if rc == 0:
        result["findings_count"] = 0
        result["report"] = []
    elif rc == 1 and os.path.exists(report_path):
        with open(report_path) as f:
            data = json.load(f)
        result["findings_count"] = len(data)
        result["report"] = data[:3] if data else []
        result["fields"] = list(data[0].keys()) if data else []
        result["has_secret_field"] = any("secret" in str(f).lower() for f in data)
    elif rc == 126:
        result["error"] = "unknown flag (126)"
    else:
        result["error"] = f"unexpected rc={rc}"

    return result


def main():
    provider = "all"
    if len(sys.argv) > 1:
        provider = sys.argv[1]

    tmpdir = tempfile.mkdtemp(prefix="hub_spike_")
    try:
        with open(os.path.join(tmpdir, "config.json"), "w") as f:
            json.dump(json.loads(PLACEHOLDER_CONFIG), f, indent=2)

        for name, value in FAKE_SECRETS.items():
            with open(os.path.join(tmpdir, f"{name}.json"), "w") as f:
                json.dump({"name": name, "token": value}, f, indent=2)

        print(f"Spike test files created in: {tmpdir}")
        print()

        results = {}
        if provider in ("gitleaks", "all"):
            print("=== Gitleaks CLI Spike ===")
            r = spike_gitleaks(tmpdir)
            results["gitleaks"] = r
            print(f"  installed: {r.get('installed')}")
            print(f"  version: {r.get('version', 'N/A')}")
            print(f"  exit_code: {r.get('exit_code', 'N/A')}")
            print(f"  findings_count: {r.get('findings_count', 'N/A')}")
            if r.get("fields"):
                print(f"  fields: {r['fields']}")
            if r.get("has_secret_field"):
                print(f"  WARNING: report contains Secret field!")
            if r.get("error"):
                print(f"  error: {r['error']}")
            print()

        if provider in ("betterleaks", "all"):
            print("=== Betterleaks CLI Spike ===")
            r = spike_betterleaks(tmpdir)
            results["betterleaks"] = r
            print(f"  installed: {r.get('installed')}")
            print(f"  version: {r.get('version', 'N/A')}")
            print(f"  exit_code: {r.get('exit_code', 'N/A')}")
            print(f"  findings_count: {r.get('findings_count', 'N/A')}")
            if r.get("fields"):
                print(f"  fields: {r['fields']}")
            if r.get("has_secret_field"):
                print(f"  WARNING: report contains secret-containing fields!")
            if r.get("error"):
                print(f"  error: {r['error']}")
            print()

        print(f"Spike output saved to: {tmpdir}")
        print("(temporary directory, will be auto-cleaned)")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
