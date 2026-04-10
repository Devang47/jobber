import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dashboard_data import (
    build_dashboard_snapshot,
    load_api_status,
    load_monitor_log_tail,
    load_recent_api_logs,
    load_scheduler_snapshot,
)


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "dashboard"


class DashboardHandler(BaseHTTPRequestHandler):
    db_path = BASE_DIR / "schedule_state.db"
    api_log_root = BASE_DIR / "logs" / "api"
    monitor_log_path = BASE_DIR / "logs" / "monitor.log"

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self._serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        if parsed.path == "/app.js":
            return self._serve_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
        if parsed.path == "/styles.css":
            return self._serve_file(STATIC_DIR / "styles.css", "text/css; charset=utf-8")
        if parsed.path == "/api/overview":
            return self._send_json(
                build_dashboard_snapshot(
                    self.db_path,
                    self.api_log_root,
                    self.monitor_log_path,
                )
            )
        if parsed.path == "/api/platforms":
            return self._send_json(load_scheduler_snapshot(self.db_path))
        if parsed.path == "/api/api-status":
            return self._send_json(load_api_status(self.api_log_root))
        if parsed.path == "/api/logs":
            params = parse_qs(parsed.query)
            source = params.get("source", [None])[0]
            try:
                limit = int(params.get("limit", ["200"])[0])
            except ValueError:
                limit = 200
            return self._send_json({"logs": load_recent_api_logs(self.api_log_root, source=source, limit=limit)})
        if parsed.path == "/api/monitor-log":
            params = parse_qs(parsed.query)
            try:
                limit = int(params.get("limit", ["200"])[0])
            except ValueError:
                limit = 200
            return self._send_json({"lines": load_monitor_log_tail(self.monitor_log_path, limit=limit)})

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format, *args):
        return

    def _serve_file(self, path: Path, content_type: str):
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def build_server(host: str, port: int, *, db_path: str, api_log_root: str, monitor_log_path: str):
    handler = type(
        "ConfiguredDashboardHandler",
        (DashboardHandler,),
        {
            "db_path": Path(db_path),
            "api_log_root": Path(api_log_root),
            "monitor_log_path": Path(monitor_log_path),
        },
    )
    return ThreadingHTTPServer((host, port), handler)


def main():
    parser = argparse.ArgumentParser(description="Jobber dashboard server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--db-path", default=str(BASE_DIR / "schedule_state.db"))
    parser.add_argument("--api-log-root", default=str(BASE_DIR / "logs" / "api"))
    parser.add_argument("--monitor-log-path", default=str(BASE_DIR / "logs" / "monitor.log"))
    args = parser.parse_args()

    server = build_server(
        args.host,
        args.port,
        db_path=args.db_path,
        api_log_root=args.api_log_root,
        monitor_log_path=args.monitor_log_path,
    )
    print(f"Dashboard running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
