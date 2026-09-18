from pathlib import Path
import json
import os
import socket
import subprocess

from dotenv import load_dotenv

ROOT = Path(r"C:\Users\Scar0\Desktop\4pms_paper")
PYTHON = Path(r"C:\Users\Scar0\Desktop\4pms_paper_n240_clean\.venv\Scripts\python.exe")
BRIDGE = ROOT / "reports" / "scenarioAB-n30-current-20260915T141418Z" / "observer_bridge.py"
load_dotenv(ROOT / ".env", override=False)

receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
receiver.bind(("0.0.0.0", 8765))
receiver.settimeout(5)
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
    payload, _ = receiver.recvfrom(65535)
    document = json.loads(payload.decode("utf-8"))
    print(
        {
            "bytes": len(payload),
            "keys": sorted(document),
            "type": document.get("type"),
            "token_matches": document.get("token") == os.environ["DB_OBSERVER_TOKEN"],
        }
    )
finally:
    bridge.terminate()
    bridge.wait(timeout=5)
    receiver.close()
