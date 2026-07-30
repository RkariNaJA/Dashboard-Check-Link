"""Background health checks for every tracked link."""
import time

import requests
import urllib3

from db import get_conn

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CHECK_RETENTION_DAYS = 30


def run_health_checks(timeout_seconds, ssl_verify):
    with get_conn() as conn:
        links = conn.execute(
            "SELECT id, url FROM links WHERE enabled = 1"
        ).fetchall()

    for link in links:
        check_one_link(link["id"], link["url"], timeout_seconds, ssl_verify)

    with get_conn() as conn:
        conn.execute(
            "DELETE FROM checks WHERE checked_at < datetime('now', 'localtime', ?)",
            (f"-{CHECK_RETENTION_DAYS} days",),
        )


def check_one_link(link_id, url, timeout_seconds, ssl_verify):
    started = time.perf_counter()
    ok, status_code, error = 0, None, None
    try:
        # trust_env=False: talk to internal links directly, never through
        # a corporate proxy - a proxy would answer instead of the link.
        session = requests.Session()
        session.trust_env = False
        resp = session.get(
            url, timeout=timeout_seconds, verify=ssl_verify,
            stream=True, allow_redirects=True,
        )
        status_code = resp.status_code
        ok = 1 if resp.status_code < 400 else 0
        resp.close()
    except requests.exceptions.RequestException as exc:
        error = exc.__class__.__name__
    response_ms = int((time.perf_counter() - started) * 1000)

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO checks (link_id, checked_at, ok, status_code,
                                   response_ms, error)
               VALUES (?, datetime('now', 'localtime'), ?, ?, ?, ?)""",
            (link_id, ok, status_code, response_ms, error),
        )
