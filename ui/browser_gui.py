#!/usr/bin/env python3
"""
SDM - Simple Download Manager browser UI server.
"""

import json
import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ui.web_gui import SDMWebApi


class SDMBrowserHandler(BaseHTTPRequestHandler):
    api: SDMWebApi
    html_path: str

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        try:
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send_file(self.html_path, "text/html; charset=utf-8")
                return
            if path == "/api/get_state":
                self._send_json(self.api.get_state())
                return
            self.send_error(404)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            try:
                self._send_json({"ok": False, "error": str(exc)}, status=500)
            except (BrokenPipeError, ConnectionResetError):
                return

    def do_POST(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            self.send_error(404)
            return

        method_name = path.removeprefix("/api/")
        method = getattr(self.api, method_name, None)
        if method is None or method_name.startswith("_"):
            self.send_error(404)
            return

        try:
            payload = self._read_json()
            if method_name == "add_download":
                result = method(payload or {})
            elif isinstance(payload, list):
                result = method(*payload)
            elif payload is None:
                result = method()
            else:
                result = method(payload)
            self._send_json(result)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            try:
                self._send_json({"ok": False, "error": str(exc)}, status=500)
            except (BrokenPipeError, ConnectionResetError):
                return

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length == 0:
            return None
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else None

    def _send_file(self, path: str, content_type: str):
        with open(path, "rb") as file:
            data = file.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, data, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    html_path = os.path.join(os.path.dirname(__file__), "assets", "sdm_ui.html")
    handler = SDMBrowserHandler
    handler.api = SDMWebApi()
    handler.html_path = html_path

    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    host, port = server.server_address
    url = f"http://{host}:{port}"
    print(f"SDM browser UI running at {url}")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
