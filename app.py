"""Internal Link Dashboard - short redirect links + health checks."""
import configparser
import os
import re
import time
from datetime import date, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from flask import (Flask, flash, redirect, render_template, request, url_for)

import db
import jobs
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
JOBS = jobs.load_jobs(config)
JOB_TOKEN = config.get("jobs", "token", fallback="").strip()

app = Flask(__name__)
app.secret_key = "internal-link-dashboard"

TREND_DAYS = 14
RESERVED_SLUGS = {"go", "manage", "links", "refresh", "static", "services",
                  "jobs"}

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
# the end of a log is where the error is, so long output is cut from the front
MAX_OUTPUT_TAIL = 8000

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


# ------------------------------------------------------- display helpers

@app.template_filter("ago")
def ago(stamp):
    """'2 h ago'. Absolute times in a table are hard to scan at a glance."""
    if not stamp:
        return "never"
    seconds = (datetime.now()
               - datetime.strptime(stamp, TIMESTAMP_FORMAT)).total_seconds()
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{int(seconds // 60)} min ago"
    if seconds < 172800:
        return f"{int(seconds // 3600)} h ago"
    return f"{int(seconds // 86400)} d ago"


@app.template_filter("duration")
def duration(ms):
    if ms is None:
        return "—"
    seconds = int(ms) // 1000
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60:02d}s"


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
    """Links and jobs share one page - two views of the same question."""
    view = "jobs" if request.args.get("view") == "jobs" else "links"
    rows = load_dashboard_rows()
    job_rows = jobs.build_rows(JOBS, db.latest_job_runs(), datetime.now())
    return render_template("dashboard.html", view=view,
                           rows=rows, summary=summary_of(rows),
                           job_rows=job_rows,
                           job_summary=jobs.summarize(job_rows,
                                                      db.job_runs_since(24)),
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


def start_and_report(links, processes, action=None):
    """Launch everything for these links, wait once, then re-check them.

    One wait for the whole batch, so starting four apps costs one pause
    and not four. `action` is runner.start_link (default) or
    runner.restart_link. Returns a list of human-readable problems.
    """
    action = action or runner.start_link
    problems = []
    for link in links:
        for result in action(link["slug"], processes, LOG_DIR):
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


@app.route("/links/<int:link_id>/restart", methods=["POST"])
def restart_link(link_id):
    """Stop the old copy (by its configured port) and start a fresh one.

    Unlike Start this runs while the link is online - replacing a running
    old version is the whole point.
    """
    with db.get_conn() as conn:
        link = conn.execute("SELECT * FROM links WHERE id = ?",
                            (link_id,)).fetchone()
    if link is None:
        flash("Link not found.", "error")
        return redirect(url_for("dashboard"))
    if not PROCESSES.get(link["slug"]):
        flash(f"No start command is configured for '{link['name']}'.", "error")
        return redirect(url_for("dashboard"))

    problems = start_and_report([link], PROCESSES, runner.restart_link)
    if problems:
        flash("Could not restart - " + "; ".join(problems), "error")
    else:
        flash(f"Restarted '{link['name']}'. If it stays down, see "
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


# ------------------------------------------------- hearing back from jobs

def _clean_stamp(value):
    """Accept only 'YYYY-MM-DD HH:MM:SS'.

    Validating here means every later reader - status, sorting, the
    'overdue' maths - can parse without guarding. Raises ValueError.
    """
    text = str(value)
    datetime.strptime(text, TIMESTAMP_FORMAT)
    return text


@app.route("/jobs/<slug>/report", methods=["POST"])
def report_job_run(slug):
    """A job's wrapper telling us how its run went.

    An unknown slug is a 404 on purpose: a typo in a Scheduled Task should
    fail loudly at the wrapper, not quietly become a job that waits for a
    first report that will never come.
    """
    if JOB_TOKEN and request.headers.get("X-Job-Token", "") != JOB_TOKEN:
        return {"error": "bad or missing X-Job-Token"}, 401
    if slug not in JOBS:
        return {"error": f"no [job:{slug}] section on this dashboard"}, 404

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return {"error": "expected a JSON object"}, 400
    try:
        started_at = _clean_stamp(payload["started_at"])
        finished_at = _clean_stamp(payload["finished_at"])
        duration_ms = int(payload["duration_ms"])
        exit_code = int(payload["exit_code"])
    except (KeyError, TypeError, ValueError) as exc:
        return {"error": f"bad or missing field: {exc}"}, 400

    db.record_job_run(slug, started_at, finished_at, duration_ms, exit_code,
                      str(payload.get("host", ""))[:100],
                      str(payload.get("output_tail", ""))[-MAX_OUTPUT_TAIL:])
    return {"ok": True}, 201


@app.route("/jobs/<slug>")
def job_detail(slug):
    """Run history and captured output for one job.

    This page is the point of the whole feature: when a nightly job fails
    you read what it printed here, instead of remoting into its server to
    find a log file on somebody's Desktop.
    """
    job = JOBS.get(slug)
    if job is None:
        flash(f"No job named '{slug}' is configured on this dashboard.",
              "error")
        return redirect(url_for("dashboard", view="jobs"))
    runs = db.recent_job_runs(slug, limit=20)
    return render_template(
        "job_detail.html", job=job, runs=runs,
        status=jobs.derive_status(job, runs[0] if runs else None,
                                  datetime.now()))


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
    # Job runs are tiny and arrive about daily, so once a day is plenty.
    scheduler.add_job(db.purge_old_job_runs, "interval", hours=24,
                      id="purge-job-runs")
    scheduler.start()


def main():
    db.init_db()
    if SAMPLE_MODE:
        import sample_data
        db.seed_samples(PORT)
        sample_data.seed_demo_hits(TREND_DAYS)
    report_start_button_config()
    print(f"[jobs] watching {len(JOBS)} scheduled job(s): "
          f"{', '.join(JOBS) if JOBS else 'none'}")
    start_scheduler()
    print(f"Dashboard running on http://localhost:{PORT}")
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
