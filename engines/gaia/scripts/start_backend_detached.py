"""Start the backend uvicorn server fully detached from the controlling terminal.

Used by the agent harness where bash background jobs get SIGHUP'd on command
exit. Uses subprocess.Popen with start_new_session=True (setsid) so the server
survives the parent shell exiting.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path("/home/jason/code/gaia")
LOG = ROOT / ".run-logs" / "backend.log"
PID = Path("/tmp/be.pid")


def main() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log = open(LOG, "w", buffering=1)
    env = dict(os.environ)
    p = subprocess.Popen(
        [sys.executable, "-u", "-m", "uvicorn", "ontology.main:app", "--host", "127.0.0.1", "--port", "8000"],
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd=str(ROOT),
        env=env,
    )
    PID.write_text(str(p.pid))
    print(f"started backend pid={p.pid} log={LOG}")


if __name__ == "__main__":
    main()
