"""Vercel serverless function — POST /api/search"""

import json
import sys
import os

# Allow imports from project root when running on Vercel
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from http.server import BaseHTTPRequestHandler
from scraper import research


class handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # suppress Vercel log noise

    def _json(self, data: dict, code: int = 200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except Exception:
            self._json({"error": "Invalid JSON"}, 400)
            return

        company = (payload.get("company") or "").strip()
        if not company:
            self._json({"error": "company name is required"}, 400)
            return

        try:
            data = research(company)
            self._json(data)
        except Exception as e:
            self._json({"error": str(e)}, 500)
