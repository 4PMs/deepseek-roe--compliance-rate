from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import requests
from werkzeug.serving import make_server

from benchmark_core.core.sequence import SequenceAllocator
from benchmark_core.observe.gateway import ActionBindingRegistry, create_app


def test_running_gateway_blocks_bypass_spoof_deny_and_replay():
    state = {"calls": 0}

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            state["calls"] += 1
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *_):
            pass

    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    target_thread = threading.Thread(target=target.serve_forever, daemon=True)
    target_thread.start()
    registry = ActionBindingRegistry()
    events, lifecycle = [], []
    app = create_app(
        f"http://127.0.0.1:{target.server_port}", "run-1", "agent", events.append,
        lifecycle_sink=lifecycle.append, action_registry=registry,
        sequence_allocator=SequenceAllocator(), enforce_policy=True,
    )
    gateway = make_server("127.0.0.1", 0, app)
    gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
    gateway_thread.start()
    url = f"http://127.0.0.1:{gateway.server_port}/health"
    try:
        assert requests.get(url).status_code == 403  # direct bypass
        allow = registry.register("run-1", "allow-1", "allow")
        assert requests.get(url, headers={
            "X-Action-Id": "spoof", "X-Correlation-Token": allow,
        }).status_code == 403  # spoofed action id
        deny = registry.register("run-1", "deny-1", "deny")
        assert requests.get(url, headers={
            "X-Action-Id": "deny-1", "X-Correlation-Token": deny,
        }).status_code == 403
        valid = registry.register("run-1", "allow-1", "allow")
        assert requests.get(url, headers={
            "X-Action-Id": "allow-1", "X-Correlation-Token": valid,
        }).status_code == 200
        replay = requests.get(url, headers={
            "X-Action-Id": "allow-1", "X-Correlation-Token": valid,
        })
        assert replay.status_code == 403
        assert state["calls"] == 1
        assert events[0].attributes["action_id"] == "allow-1"
        assert lifecycle[0].action_id == "allow-1"
    finally:
        gateway.shutdown()
        gateway.server_close()
        target.shutdown()
        target.server_close()
