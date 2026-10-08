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

-- One row per completed run of a scheduled job. Jobs themselves are
-- declared in config.ini, so there is no table of jobs to keep in sync.
CREATE TABLE IF NOT EXISTS job_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    slug         TEXT NOT NULL,          -- matches a [job:<slug>] section
    started_at   TEXT NOT NULL,          -- local time, from the job's host
    finished_at  TEXT NOT NULL,
    duration_ms  INTEGER NOT NULL,
    exit_code    INTEGER,
    ok           INTEGER NOT NULL,       -- exit_code == 0
    host         TEXT NOT NULL DEFAULT '',
    output_tail  TEXT NOT NULL DEFAULT '',
    received_at  TEXT NOT NULL           -- this dashboard's own clock
);
CREATE INDEX IF NOT EXISTS idx_job_runs_slug_time ON job_runs(slug, started_at);
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


JOB_RUN_RETENTION_DAYS = 90


def record_job_run(slug, started_at, finished_at, duration_ms, exit_code,
                   host, output_tail):
    """Store one finished run. `ok` is derived here so every reader agrees."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO job_runs (slug, started_at, finished_at, duration_ms,
                                     exit_code, ok, host, output_tail, received_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))""",
            (slug, started_at, finished_at, duration_ms, exit_code,
             1 if exit_code == 0 else 0, host, output_tail),
        )


def latest_job_runs():
    """Newest run per slug, as {slug: Row}.

    SQLite guarantees the bare columns come from the row that produced
    MAX(started_at), so one grouped query answers the whole dashboard.
    output_tail is left out - the list view never shows it and it is the
    one big column.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT slug, MAX(started_at) AS started_at, finished_at,
                      duration_ms, exit_code, ok, host, received_at
               FROM job_runs GROUP BY slug"""
        ).fetchall()
    return {row["slug"]: row for row in rows}


def recent_job_runs(slug, limit=20):
    """Run history for one job, newest first, with its captured output."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM job_runs WHERE slug = ?
               ORDER BY started_at DESC LIMIT ?""", (slug, limit)).fetchall()


def job_runs_since(hours):
    """How many reports arrived in the last `hours` - a liveness figure."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT COUNT(*) FROM job_runs
               WHERE received_at >= datetime('now', 'localtime', ?)""",
            (f"-{hours} hours",)).fetchone()[0]


def purge_old_job_runs():
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM job_runs WHERE received_at < datetime('now', 'localtime', ?)",
            (f"-{JOB_RUN_RETENTION_DAYS} days",))
