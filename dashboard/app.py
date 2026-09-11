from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from dashboard.backend.domain.models import DashboardRun
from dashboard.backend.services.analytics import summarize_runs
from dashboard.backend.services.run_loader import RunRepository

ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT / "frontend"
DEMO_RUNS_DIR = ROOT / "fixtures" / "demo_runs"


def build_snapshot(runs: list[DashboardRun]) -> dict[str, object]:
    return {
        "summary": summarize_runs(runs).to_dict(),
        "techniques": _technique_summary(runs),
        "runs": [run.to_dict() for run in runs],
    }


def _technique_summary(runs: list[DashboardRun]) -> list[dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    for run in runs:
        for event in run.timeline:
            for technique in event.techniques:
                row = rows.setdefault(
                    technique.technique_id,
                    {
                        "technique_id": technique.technique_id,
                        "name": technique.name,
                        "tactic": technique.tactic,
                        "attempted": 0,
                        "verified": 0,
                        "violating": 0,
                        "run_ids": [],
                    },
                )
                row["attempted"] = int(row["attempted"]) + 1
                if technique.status == "verified":
                    row["verified"] = int(row["verified"]) + 1
                if event.roe_status == "violation":
                    row["violating"] = int(row["violating"]) + 1
                if run.run_id not in row["run_ids"]:
                    row["run_ids"].append(run.run_id)
    return sorted(rows.values(), key=lambda row: (str(row["tactic"]), str(row["technique_id"])))


class DashboardHandler(BaseHTTPRequestHandler):
    repository: RunRepository

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        if path == "/api/dashboard":
            self._json(build_snapshot(self.repository.load_all()))
            return
        if path.startswith("/api/runs/"):
            run_id = unquote(path.removeprefix("/api/runs/"))
            run = self.repository.get(run_id)
            if run is None:
                self._json({"error": "run not found"}, status=404)
            else:
                self._json(run.to_dict())
            return
        self._static(path)

    def _json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        candidate = (FRONTEND_DIR / relative).resolve()
        try:
            candidate.relative_to(FRONTEND_DIR.resolve())
        except ValueError:
            self.send_error(404)
            return
        if not candidate.is_file():
            self.send_error(404)
            return
        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[dashboard] {self.address_string()} - {format % args}")


def serve(runs_dir: Path, host: str, port: int) -> None:
    handler = type("ConfiguredDashboardHandler", (DashboardHandler,), {"repository": RunRepository(runs_dir)})
    server = ThreadingHTTPServer((host, port), handler)
    print(f"ROE Benchmark dashboard: http://{host}:{server.server_port}")
    print(f"Artifacts: {runs_dir.resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="ROE Benchmark read-only benchmark dashboard")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--demo", action="store_true", help="Use bundled synthetic demo artifacts")
    args = parser.parse_args()
    serve(DEMO_RUNS_DIR if args.demo else args.runs_dir, args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
