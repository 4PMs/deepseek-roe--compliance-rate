from pathlib import Path
import os
import subprocess

from dotenv import load_dotenv

from benchmark_core.observe.database import DatabaseEventCollector

ROOT = Path(r"C:\Users\Scar0\Desktop\4pms_paper")
PYTHON = Path(r"C:\Users\Scar0\Desktop\4pms_paper_n240_clean\.venv\Scripts\python.exe")
BRIDGE = ROOT / "reports" / "scenarioA-n30-20260915T025311Z" / "observer_bridge.py"
load_dotenv(ROOT / ".env", override=False)

token = os.environ["DB_OBSERVER_TOKEN"]
bridge = subprocess.Popen(
    [str(PYTHON), str(BRIDGE)],
    cwd=ROOT,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
)
try:
    line = bridge.stdout.readline().strip()
    if "ready" not in line:
        raise RuntimeError(line)
    collector = DatabaseEventCollector(
        "diagnostic", lambda event: None, token=token, host="0.0.0.0", port=8765
    )
    collector.start()
    try:
        print({"heartbeat_received": collector.wait_until_ready(timeout=5)})
    finally:
        collector.close()
finally:
    bridge.terminate()
    bridge.wait(timeout=5)
