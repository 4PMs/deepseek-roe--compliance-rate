from pathlib import Path
import os
import subprocess

from dotenv import load_dotenv

from benchmark_core.observe.database import DatabaseEventCollector
from environments.juice_shop.reset import reset_juice_shop

ROOT = Path(r"C:\Users\Scar0\Desktop\4pms_paper")
PYTHON = Path(r"C:\Users\Scar0\Desktop\4pms_paper_n240_clean\.venv\Scripts\python.exe")
BRIDGE = ROOT / "reports" / "scenarioAB-n30-current-20260915T141418Z" / "observer_bridge.py"
load_dotenv(ROOT / ".env", override=False)
os.environ["DB_OBSERVER"] = "db-observer-relay:8765"
os.environ["RUN_SEQUENCE_TOKEN"] = "diagnostic-only"
os.environ["RUN_SEQUENCE_OBSERVER"] = "host.docker.internal:65534"

bridge = subprocess.Popen(
    [str(PYTHON), str(BRIDGE)],
    cwd=ROOT,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
)
try:
    line = bridge.stdout.readline().strip() if bridge.stdout else ""
    if "ready" not in line:
        raise RuntimeError(line)
    collector = DatabaseEventCollector(
        "diagnostic",
        lambda event: None,
        token=os.environ["DB_OBSERVER_TOKEN"],
        host="0.0.0.0",
        port=8765,
    )
    collector.start()
    try:
        reset = reset_juice_shop()
        print(
            {
                "baseline_verified": reset["baseline_verified"],
                "heartbeat_received": collector.wait_until_ready(timeout=3),
            }
        )
    finally:
        collector.close()
finally:
    bridge.terminate()
    bridge.wait(timeout=5)
