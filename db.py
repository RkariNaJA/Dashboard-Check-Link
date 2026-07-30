"""SQLite storage for the link dashboard."""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL UNIQUE,   -- short-link name: /go/<slug>
    url         TEXT NOT NULL,          -- real destination
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- One row per link / day / visitor IP (clicks counted by the /go redirect)
CREATE TABLE IF NOT EXISTS daily_hits (
    link_id  INTEGER NOT NULL REFERENCES links(id) ON DELETE CASCADE,
    day      TEXT NOT NULL,            -- YYYY-MM-DD (local time)
    ip       TEXT NOT NULL,
    hits     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (link_id, day, ip)
);

CREATE TABLE IF NOT EXISTS checks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    link_id      INTEGER NOT NULL REFERENCES links(id) ON DELETE CASCADE,
    checked_at   TEXT NOT NULL,        -- local time
    ok           INTEGER NOT NULL,
    status_code  INTEGER,
    response_ms  INTEGER,
    error        TEXT
);
CREATE INDEX IF NOT EXISTS idx_checks_link_time ON checks(link_id, checked_at);
"""


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def record_click(link_id, day, ip):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO daily_hits (link_id, day, ip, hits)
               VALUES (?, ?, ?, 1)
               ON CONFLICT(link_id, day, ip)
               DO UPDATE SET hits = hits + 1""",
            (link_id, day, ip),
        )


def seed_samples(port):
    """Insert 4 demo links when the database is empty (sample_mode only)."""
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
        if count:
            return
        demo = [
            ("HR Request Form (demo)", "hr-form", f"http://127.0.0.1:{port}/"),
            ("IT Service Desk (demo)", "it-service-desk",
             f"http://127.0.0.1:{port}/manage"),
            ("Report Portal (demo)", "report-portal",
             "http://127.0.0.1:9098/reports"),
            ("Room Booking (demo)", "room-booking", f"http://127.0.0.1:{port}/"),
        ]
        conn.executemany(
            "INSERT INTO links (name, slug, url) VALUES (?, ?, ?)", demo
        )
