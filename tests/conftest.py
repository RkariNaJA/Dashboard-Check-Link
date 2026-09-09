"""Shared fixtures: a throwaway database and a real local HTTP server."""
import http.server
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point db at an empty database for this test only."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    return db


@pytest.fixture
def a_link(temp_db):
    """Insert one link and return its id, with a factory for the url."""
    def make(url):
        with temp_db.get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO links (name, slug, url) VALUES (?, ?, ?)",
                ("Demo", "demo", url))
            return cur.lastrowid
    return make


@pytest.fixture
def live_server():
    """A real HTTP server on a free port, so 'online' means online."""
    class Quiet(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


@pytest.fixture
def dead_url():
    """A port with nothing listening - refused immediately."""
    return "http://127.0.0.1:9/"


@pytest.fixture
def log_containing():
    """Poll a log file until it contains `wanted` (children are detached)."""
    import time

    def read(log_path, wanted, timeout=15):
        deadline = time.time() + timeout
        text = ""
        while time.time() < deadline:
            if log_path.exists():
                text = log_path.read_text(encoding="utf-8", errors="replace")
                if wanted.lower() in text.lower():
                    return text
            time.sleep(0.1)
        return text
    return read
