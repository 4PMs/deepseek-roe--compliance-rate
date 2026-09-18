from __future__ import annotations

import socket
import socketserver

UDP_DESTINATION = ("127.0.0.1", 8765)


class DatagramForwardingHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        chunks: list[bytes] = []
        while True:
            chunk = self.request.recv(65535)
            if not chunk:
                break
            chunks.append(chunk)
        payload = b"".join(chunks)
        if payload:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as target:
                target.sendto(payload, UDP_DESTINATION)


class ForwardingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with ForwardingServer(("0.0.0.0", 8764), DatagramForwardingHandler) as server:
        print("DB observer bridge ready: tcp/8764 -> udp/127.0.0.1:8765", flush=True)
        server.serve_forever()
