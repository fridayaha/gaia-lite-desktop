"""DVP benchmark runner (P5, DESIGN.md §九).

Orchestrates a full DVP benchmark run:
  1. Precondition checks (backend up, ontology seeded, MySQL seeded).
  2. Run the read dimension harness → results JSON.
  3. (Optional, P4) Run the agent dimension harness → results JSON.
  4. Generate the markdown report from the JSON results.

The harnesses are run as subprocesses (they manage their own asyncio event
loop and httpx clients). Results land in `reports/<timestamp>/`.

Usage:
    python -m tests.benchmark.dvp.scripts.run_benchmark
    python -m tests.benchmark.dvp.scripts.run_benchmark --skip-agent

Prerequisites (must be done once before first run):
  - Backend running on http://localhost:8000 (scripts/start_backend.py)
  - MySQL `dvp_benchmark` seeded (scripts/seed_dvp.py)
  - DVP ontology + datasource registered (scripts/01_setup_ontology.py)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # gaia/
DVP_DIR = ROOT / "tests" / "benchmark" / "dvp"
REPORTS_DIR = DVP_DIR / "reports"
BACKEND_URL = os.environ.get("DVP_API_BASE", "http://localhost:8000")


def _check(cmd: list[str], label: str) -> bool:
    """Run a check command; return True if it succeeded. Logs on failure."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print(f"  ✗ {label}: exit {r.returncode}", file=sys.stderr)
            if r.stderr:
                print(f"    {r.stderr.strip()[:200]}", file=sys.stderr)
            return False
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  ✗ {label}: {e}", file=sys.stderr)
        return False


def preconditions() -> bool:
    """Verify backend + ontology + MySQL are ready."""
    import urllib.request

    print("── Precondition checks ──")
    # Backend health
    try:
        with urllib.request.urlopen(f"{BACKEND_URL}/health", timeout=5) as resp:
            if resp.status != 200:
                print(f"  ✗ backend health: {resp.status}", file=sys.stderr)
                return False
        print("  ✓ backend up")
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ backend not reachable at {BACKEND_URL}: {e}", file=sys.stderr)
        print(f"    start it: cd {ROOT} && setsid .venv/bin/python scripts/start_backend.py "
              f"</dev/null >.run-logs/backend.log 2>&1 &", file=sys.stderr)
        return False

    # Ontology registered (summary endpoint)
    try:
        import urllib.request

        with urllib.request.urlopen(
            f"{BACKEND_URL}/ontologies/DVP/object-types/summary", timeout=10
        ) as resp:
            if resp.status != 200:
                print(f"  ✗ ontology DVP: {resp.status}", file=sys.stderr)
                print("    seed it: python -m tests.benchmark.dvp.scripts.01_setup_ontology",
                      file=sys.stderr)
                return False
        print("  ✓ ontology DVP registered")
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ ontology DVP not registered: {e}", file=sys.stderr)
        print("    seed it: python -m tests.benchmark.dvp.scripts.01_setup_ontology",
              file=sys.stderr)
        return False

    # MySQL reachable (via docker exec into marketing-mysql)
    if not _check(
        ["docker", "exec", "marketing-mysql", "mysqladmin", "ping", "-h", "localhost", "-uroot",
         "-pmarketing123"],
        "mysql ping",
    ):
        print("    start mysql: docker start marketing-mysql", file=sys.stderr)
        return False
    print("  ✓ mysql up")
    return True


def _backend_version() -> str:
    """Read backend version from the package __init__."""
    try:
        v = (ROOT / "src" / "ontology" / "__init__.py").read_text()
        for line in v.splitlines():
            if line.startswith("__version__"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


def run_dimension(module: str, json_out: Path, label: str) -> bool:
    """Run a dimension harness as a subprocess, writing JSON results."""
    print(f"\n── Running {label} dimension ──")
    cmd = [sys.executable, "-m", module, "--json", str(json_out)]
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(ROOT))
    elapsed = time.time() - t0
    print(f"  {label} done in {elapsed:.1f}s (exit {r.returncode})")
    return r.returncode == 0 and json_out.exists()


def main() -> int:
    ap = argparse.ArgumentParser(description="DVP benchmark runner")
    ap.add_argument("--skip-agent", action="store_true", help="skip agent dimension (default: skipped, P4 pending)")
    ap.add_argument("--skip-read", action="store_true", help="skip read dimension")
    ap.add_argument(
        "--out-dir", metavar="DIR", help="output dir (default: reports/<timestamp>)"
    )
    args = ap.parse_args()

    if not preconditions():
        return 1

    version = _backend_version()
    ts = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else REPORTS_DIR / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n── Output dir: {out_dir} ──")

    read_json = out_dir / "read.json"
    agent_json = out_dir / "agent.json"
    ran_any = False

    if not args.skip_read:
        if not run_dimension(
            "tests.benchmark.dvp.harness.read_harness", read_json, "read"
        ):
            print("  read dimension failed; continuing to report with partial data",
                  file=sys.stderr)
        else:
            ran_any = True

    if not args.skip_agent and (DVP_DIR / "harness" / "agent_harness.py").exists():
        if not run_dimension(
            "tests.benchmark.dvp.harness.agent_harness", agent_json, "agent"
        ):
            print("  agent dimension failed; continuing to report with partial data",
                  file=sys.stderr)
        else:
            ran_any = True
    else:
        print("\n  (agent dimension skipped — P4 pending LLM config)")

    if not ran_any:
        print("error: no dimension ran successfully", file=sys.stderr)
        return 2

    # Generate report.
    report_path = out_dir / "report.md"
    gen_cmd = [
        sys.executable,
        "-m",
        "tests.benchmark.dvp.scripts.generate_report",
        "--out",
        str(report_path),
        "--backend-version",
        version,
    ]
    if read_json.exists():
        gen_cmd += ["--read", str(read_json)]
    if agent_json.exists():
        gen_cmd += ["--agent", str(agent_json)]
    print("\n── Generating report ──")
    r = subprocess.run(gen_cmd, cwd=str(ROOT))
    if r.returncode != 0:
        return r.returncode

    # Symlink latest.
    latest = REPORTS_DIR / "latest.md"
    try:
        if latest.exists() or latest.is_symlink():
            latest.unlink()
        latest.symlink_to(report_path.relative_to(REPORTS_DIR))
        print(f"  latest → {latest}")
    except OSError:
        # Fallback: copy if symlinks unsupported.
        shutil.copy2(report_path, latest)

    print("\n═══ DVP benchmark complete ═══")
    print(f"  report: {report_path}")
    print(f"  latest: {latest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
