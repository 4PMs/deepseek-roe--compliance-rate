from __future__ import annotations

from pathlib import Path
import subprocess
import sys

ROOT = Path(r"C:\Users\Scar0\Desktop\4pms_paper")
PYTHON = Path(r"C:\Users\Scar0\Desktop\4pms_paper_n240_clean\.venv\Scripts\python.exe")
BRIDGE = ROOT / "reports" / "scenarioAB-n30-current-20260915T141418Z" / "observer_bridge.py"
BATCH = ROOT / "reports" / "scenarioAB-n30-current-20260915T141418Z" / "run_batch.py"


def main() -> int:
    bridge = subprocess.Popen(
        [str(PYTHON), str(BRIDGE)],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        ready = bridge.stdout.readline().strip() if bridge.stdout else ""
        print(ready, flush=True)
        if "DB observer bridge ready" not in ready:
            raise RuntimeError(f"DB observer bridge failed to start: {ready}")
        batch = subprocess.run([str(PYTHON), str(BATCH)], cwd=ROOT, check=False)
        return batch.returncode
    finally:
        bridge.terminate()
        try:
            bridge.wait(timeout=5)
        except subprocess.TimeoutExpired:
            bridge.kill()
            bridge.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
