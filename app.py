#!/usr/bin/env python3
"""Local dev server — mirrors the Vercel setup."""

import http.server
import json
import os
import sys

from scraper import research

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
ROOT = os.path.dirname(os.path.abspath(__file__))


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} — {fmt % args}")

    def _send_json(self, data: dict, code: int = 200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: str, ctype: str):
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send_file(os.path.join(ROOT, "index.html"), "text/html; charset=utf-8")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != "/api/search":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
            company = payload.get("company", "").strip()
        except Exception:
            self._send_json({"error": "Bad JSON"}, 400)
            return

        if not company:
            self._send_json({"error": "company name is required"}, 400)
            return

        try:
            self._send_json(research(company))
        except Exception as e:
            self._send_json({"error": str(e)}, 500)


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"\n  OMG HR Finder → http://localhost:{PORT}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Bye.")
