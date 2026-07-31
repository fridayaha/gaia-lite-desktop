"""Start the backend fully detached (new session) — survives parent shell exit.

Uses subprocess.Popen(start_new_session=True) which is the reliable way to
daemonize in this environment (nohup/setsid/disown proved unstable — the
tool shell kills background jobs on command return; Popen with a new session
detaches the child from the controlling terminal).

Run: .venv/bin/python scripts/start_backend.py
Stop: pkill -f "uvicorn ontology.main"
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / ".run-logs" / "backend.log"
LOG.parent.mkdir(exist_ok=True)


def main() -> int:
    log = open(LOG, "w")
    p = subprocess.Popen(
        [
            str(ROOT / ".venv" / "bin" / "python"),
            "-m",
            "uvicorn",
            "ontology.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        cwd=str(ROOT),
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    print(f"backend started pid={p.pid}, log={LOG}")
    # Give it a moment to confirm it didn't immediately crash.
    time.sleep(2)
    if p.poll() is not None:
        print(f"ERROR: backend exited early with code {p.returncode}", file=sys.stderr)
        return 1
    print("backend alive (detached). Health: http://localhost:8000/health")
    return 0


if __name__ == "__main__":
    sys.exit(main())
