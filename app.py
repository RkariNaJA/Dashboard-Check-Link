"""Internal Link Dashboard - short redirect links + health checks."""
import configparser
import os
import re
import time
from datetime import date, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from flask import (Flask, flash, redirect, render_template, request, url_for)

import db
import runner
from collector import check_one_link, run_health_checks

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

config = configparser.ConfigParser(interpolation=None)
config.read(os.path.join(BASE_DIR, "config.ini"))

CHECK_MIN = config.getint("health", "check_interval_minutes", fallback=5)
TIMEOUT_S = config.getint("health", "timeout_seconds", fallback=10)
SSL_VERIFY = config.getboolean("health", "ssl_verify", fallback=False)
SAMPLE_MODE = config.getboolean("app", "sample_mode", fallback=False)
HOST = config.get("server", "host", fallback="0.0.0.0")
PORT = config.getint("server", "port", fallback=8090)

app = Flask(__name__)
app.secret_key = "internal-link-dashboard"

TREND_DAYS = 14
RESERVED_SLUGS = {"go", "manage", "links", "refresh", "static", "services"}

LOG_DIR = os.path.join(BASE_DIR, "logs")
# how long to let an app boot before asking whether it came up
START_GRACE_SECONDS = 4
PROCESSES = runner.load_processes(config)


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "link"


def unique_slug(conn, wanted, ignore_id=None):
    slug, n = wanted, 1
    while True:
        taken = slug in RESERVED_SLUGS or conn.execute(
            "SELECT id FROM links WHERE slug = ? AND id IS NOT ?",
            (slug, ignore_id)).fetchone()
        if not taken:
            return slug
        n += 1
        slug = f"{wanted}-{n}"


# ------------------------------------------------------------------ queries

def load_dashboard_rows():
    today = date.today().isoformat()
    days = [(date.today() - timedelta(days=i)).isoformat()
            for i in range(TREND_DAYS - 1, -1, -1)]
    rows = []
    with db.get_conn() as conn:
        links = conn.execute(
            "SELECT * FROM links ORDER BY enabled DESC, name"
        ).fetchall()
        for link in links:
            lid = link["id"]
            totals = conn.execute(
                """SELECT COALESCE(SUM(hits), 0) AS clicks,
                          COUNT(DISTINCT ip) AS visitors
                   FROM daily_hits WHERE link_id = ?""", (lid,)).fetchone()
            today_row = conn.execute(
                """SELECT COALESCE(SUM(hits), 0) AS clicks,
                          COUNT(DISTINCT ip) AS visitors
                   FROM daily_hits WHERE link_id = ? AND day = ?""",
                (lid, today)).fetchone()
            per_day = dict(conn.execute(
                """SELECT day, SUM(hits) FROM daily_hits
                   WHERE link_id = ? AND day >= ? GROUP BY day""",
                (lid, days[0])).fetchall())
            latest = conn.execute(
                """SELECT * FROM checks WHERE link_id = ?
                   ORDER BY checked_at DESC LIMIT 1""", (lid,)).fetchone()
            rows.append({
                "link": link,
                "total_clicks": totals["clicks"],
                "total_visitors": totals["visitors"],
                "today_clicks": today_row["clicks"],
                "today_visitors": today_row["visitors"],
                "trend": [{"day": d, "clicks": per_day.get(d, 0)} for d in days],
                "trend_max": max([per_day.get(d, 0) for d in days] + [1]),
                "check": latest,
                "startable": link["slug"] in PROCESSES,
            })
    return rows


def summary_of(rows):
    active = [r for r in rows if r["link"]["enabled"]]
    checked = [r for r in active if r["check"] is not None]
    return {
        "total": len(active),
        "online": sum(1 for r in checked if r["check"]["ok"]),
        "offline": sum(1 for r in checked if not r["check"]["ok"]),
        "clicks_today": sum(r["today_clicks"] for r in active),
        "clicks_total": sum(r["total_clicks"] for r in active),
        "startable": sum(1 for r in active if r["startable"]),
    }


# --------------------------------------------------------- the counting part

@app.route("/go/<slug>")
def go(slug):
    """The short link users click: count the click, then forward them."""
    with db.get_conn() as conn:
        link = conn.execute("SELECT * FROM links WHERE slug = ?",
                            (slug,)).fetchone()
    if link is None:
        return render_template("missing.html", slug=slug), 404
    if link["enabled"]:
        ip = (request.headers.get("X-Forwarded-For", "")
              or request.remote_addr or "-").split(",")[0].strip()
        db.record_click(link["id"], date.today().isoformat(), ip)
    # 302: never let browsers cache the redirect, or clicks go uncounted
    return redirect(link["url"], code=302)


# ------------------------------------------------------------------- routes

@app.route("/")
def dashboard():
    rows = load_dashboard_rows()
    return render_template("dashboard.html", rows=rows,
                           summary=summary_of(rows),
                           check_interval=CHECK_MIN)


@app.route("/manage")
def manage():
    with db.get_conn() as conn:
        links = conn.execute("SELECT * FROM links ORDER BY name").fetchall()
    return render_template("manage.html", links=links)


@app.route("/links/add", methods=["POST"])
def add_link():
    name = request.form.get("name", "").strip()
    url = request.form.get("url", "").strip()
    slug = request.form.get("slug", "").strip()
    if not name or not url:
        flash("Name and destination URL are required.", "error")
        return redirect(url_for("manage"))
    if not url.lower().startswith(("http://", "https://")):
        url = "http://" + url
    with db.get_conn() as conn:
        slug = unique_slug(conn, slugify(slug or name))
        cur = conn.execute(
            "INSERT INTO links (name, slug, url) VALUES (?, ?, ?)",
            (name, slug, url))
        link_id = cur.lastrowid
    check_one_link(link_id, url, TIMEOUT_S, SSL_VERIFY)   # first status now
    short = request.url_root.rstrip("/") + "/go/" + slug
    flash(f"Link added. Share this with your users: {short}", "ok")
    return redirect(url_for("manage"))


@app.route("/links/<int:link_id>/edit", methods=["GET", "POST"])
def edit_link(link_id):
    with db.get_conn() as conn:
        link = conn.execute("SELECT * FROM links WHERE id = ?",
                            (link_id,)).fetchone()
    if link is None:
        flash("Link not found.", "error")
        return redirect(url_for("manage"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        url = request.form.get("url", "").strip()
        slug = request.form.get("slug", "").strip()
        if not name or not url:
            flash("Name and destination URL are required.", "error")
        else:
            if not url.lower().startswith(("http://", "https://")):
                url = "http://" + url
            with db.get_conn() as conn:
                slug = unique_slug(conn, slugify(slug or name), link_id)
                conn.execute(
                    """UPDATE links SET name = ?, slug = ?, url = ?
                       WHERE id = ?""", (name, slug, url, link_id))
            flash(f"Link '{name}' updated.", "ok")
            return redirect(url_for("manage"))
    return render_template("edit.html", link=link)


@app.route("/links/<int:link_id>/toggle", methods=["POST"])
def toggle_link(link_id):
    with db.get_conn() as conn:
        conn.execute("UPDATE links SET enabled = 1 - enabled WHERE id = ?",
                     (link_id,))
    return redirect(url_for("manage"))


@app.route("/links/<int:link_id>/delete", methods=["POST"])
def delete_link(link_id):
    with db.get_conn() as conn:
        conn.execute("DELETE FROM links WHERE id = ?", (link_id,))
    flash("Link deleted (its history was removed too).", "ok")
    return redirect(url_for("manage"))


# ------------------------------------------------------- starting the apps

def why_not_start(link, processes):
    """Reason this link cannot be started right now, or None if it can.

    The 'is it up?' answer comes from a live check, never from the checks
    table: right after a reboot the newest stored row is stale and still
    says 'online', which would refuse to start the very apps this button
    exists for.
    """
    if not processes.get(link["slug"]):
        return f"No start command is configured for '{link['name']}'."
    if check_one_link(link["id"], link["url"], TIMEOUT_S, SSL_VERIFY):
        return f"'{link['name']}' is already online - left it alone."
    return None


def start_and_report(links, processes):
    """Launch everything for these links, wait once, then re-check them.

    One wait for the whole batch, so starting four apps costs one pause
    and not four. Returns a list of human-readable problems.
    """
    problems = []
    for link in links:
        for result in runner.start_link(link["slug"], processes, LOG_DIR):
            if not result.ok:
                problems.append(f"{link['name']} ({result.name}): "
                                f"{result.message}")
    if links:
        time.sleep(START_GRACE_SECONDS)
        for link in links:
            check_one_link(link["id"], link["url"], TIMEOUT_S, SSL_VERIFY)
    return problems


@app.route("/links/<int:link_id>/start", methods=["POST"])
def start_link(link_id):
    with db.get_conn() as conn:
        link = conn.execute("SELECT * FROM links WHERE id = ?",
                            (link_id,)).fetchone()
    if link is None:
        flash("Link not found.", "error")
        return redirect(url_for("dashboard"))

    reason = why_not_start(link, PROCESSES)
    if reason:
        flash(reason, "error")
        return redirect(url_for("dashboard"))

    problems = start_and_report([link], PROCESSES)
    if problems:
        flash("Could not start - " + "; ".join(problems), "error")
    else:
        flash(f"Started '{link['name']}'. If it stays down, see "
              f"logs/ for what it printed.", "ok")
    return redirect(url_for("dashboard"))


@app.route("/services/start-all", methods=["POST"])
def start_all():
    with db.get_conn() as conn:
        links = conn.execute(
            "SELECT * FROM links WHERE enabled = 1 ORDER BY name").fetchall()
    to_start = [ln for ln in links if why_not_start(ln, PROCESSES) is None]
    if not to_start:
        flash("Nothing to start - every app with a start command is "
              "already online.", "ok")
        return redirect(url_for("dashboard"))

    problems = start_and_report(to_start, PROCESSES)
    names = ", ".join(ln["name"] for ln in to_start)
    if problems:
        flash(f"Started {len(to_start)} ({names}), but: "
              + "; ".join(problems), "error")
    else:
        flash(f"Started {len(to_start)}: {names}. If any stays down, see "
              f"logs/ for what it printed.", "ok")
    return redirect(url_for("dashboard"))


@app.route("/refresh", methods=["POST"])
def refresh_now():
    run_health_checks(TIMEOUT_S, SSL_VERIFY)
    flash("Health checks ran just now.", "ok")
    return redirect(url_for("dashboard"))


# --------------------------------------------------------------- background

def report_start_button_config():
    """Say at startup which links got a Start button, and which did not.

    A [process:...] section whose slug matches no link produces no button
    and no error - the hardest kind of problem to spot. Name it instead.
    """
    with db.get_conn() as conn:
        slugs = [r["slug"] for r in conn.execute("SELECT slug FROM links")]
    if not PROCESSES:
        print("[runner] no [process:...] sections in config.ini - "
              "no Start buttons will appear")
        return
    matched = [s for s in PROCESSES if s in slugs]
    print(f"[runner] Start buttons for {len(matched)} link(s): "
          f"{', '.join(matched) if matched else 'none'}")
    for slug in runner.unmatched_slugs(PROCESSES, slugs):
        print(f"[runner] WARNING: config.ini has link = {slug!r}, but no "
              f"link on the dashboard uses that slug - no button for it")


def start_scheduler():
    scheduler = BackgroundScheduler(daemon=True)
    # first health check a few seconds AFTER the web server is listening
    scheduler.add_job(run_health_checks, "interval", minutes=CHECK_MIN,
                      args=[TIMEOUT_S, SSL_VERIFY], id="health",
                      next_run_time=datetime.now() + timedelta(seconds=5))
    scheduler.start()


def main():
    db.init_db()
    if SAMPLE_MODE:
        import sample_data
        db.seed_samples(PORT)
        sample_data.seed_demo_hits(TREND_DAYS)
    report_start_button_config()
    start_scheduler()
    print(f"Dashboard running on http://localhost:{PORT}")
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
