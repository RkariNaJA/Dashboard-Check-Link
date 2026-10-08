# Scheduled Job Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show on the existing dashboard whether each scheduled job ran and passed, and flag jobs that have stopped running — without the dashboard reaching out to any machine.

**Architecture:** Jobs report *in* to the dashboard. A standard-library wrapper (`report.py`) is copied to each job host and sits between Task Scheduler and the real command; it captures exit code, duration and output, then POSTs them to `/jobs/<slug>/report`. The dashboard stores runs in SQLite, derives status at render time from `config.ini` declarations plus the newest run, and shows them under a Links | Jobs toggle on the existing dashboard page.

**Tech Stack:** Python 3, Flask 3, APScheduler, SQLite (stdlib `sqlite3`), Jinja2, pytest. The wrapper uses the standard library only.

**Spec:** `docs/superpowers/specs/2026-10-01-scheduled-jobs-design.md`

## Global Constraints

- **No outbound connections from the dashboard to job hosts.** Every network arrow points at 192.0.2.10. Nothing in this plan may add a request to a job's machine.
- **`report.py` imports the standard library only** — `urllib.request`, never `requests`. The job hosts have no packages installed and must not need any.
- **Reporting must never change a job's outcome.** The wrapper exits with the child's exit code in every path, including when the POST fails.
- **Files stay under 500 lines.** `app.py` is at 344; job logic belongs in `jobs.py`.
- **Read a file before editing it.**
- **Timestamps are `"%Y-%m-%d %H:%M:%S"` local time**, everywhere, validated at ingest.
- **Jobs are declared in `config.ini`** as `[job:<slug>]`, the same way `[process:<name>]` declares Start buttons. No web UI for adding jobs.
- **Status precedence is `never → overdue → failed → ok`.**
- **Retention: 90 days** of `job_runs`.
- **Encoding:** all job output is decoded with `errors="replace"`. The flows print Thai and emoji.
- Run tests with `python -m pytest` from the project root.

---

### Task 1: Store job runs

**Files:**
- Modify: `db.py` (append to `SCHEMA`, add functions)
- Modify: `app.py:320-327` (`start_scheduler` — add the purge)
- Test: `tests/test_jobs_db.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `db.record_job_run(slug, started_at, finished_at, duration_ms, exit_code, host, output_tail) -> None`
  - `db.latest_job_runs() -> dict[str, sqlite3.Row]` — newest run per slug, columns `slug, started_at, finished_at, duration_ms, exit_code, ok, host, received_at`
  - `db.recent_job_runs(slug, limit=20) -> list[sqlite3.Row]` — full rows including `output_tail`, newest first
  - `db.job_runs_since(hours) -> int`
  - `db.purge_old_job_runs() -> None`
  - `db.JOB_RUN_RETENTION_DAYS = 90`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_jobs_db.py`:

```python
"""Storing and reading back scheduled job runs."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def record(temp_db, slug="flow4", started="2026-10-01 06:00:00", exit_code=0):
    temp_db.record_job_run(slug, started, "2026-10-01 06:14:00", 840000,
                           exit_code, "JOBHOST01", "all done")


def test_a_recorded_run_comes_back_as_the_latest_for_its_slug(temp_db):
    record(temp_db)

    latest = temp_db.latest_job_runs()

    assert list(latest) == ["flow4"]
    assert latest["flow4"]["started_at"] == "2026-10-01 06:00:00"
    assert latest["flow4"]["host"] == "JOBHOST01"


def test_exit_code_zero_is_ok_and_anything_else_is_not(temp_db):
    record(temp_db, slug="good", exit_code=0)
    record(temp_db, slug="bad", exit_code=1)

    latest = temp_db.latest_job_runs()

    assert latest["good"]["ok"] == 1
    assert latest["bad"]["ok"] == 0


def test_latest_is_the_newest_start_time_not_the_newest_row(temp_db):
    """A late-arriving report for an older run must not win.

    Reports travel over the network and can arrive out of order; the run
    the dashboard shows is the one that started most recently.
    """
    record(temp_db, started="2026-10-01 06:00:00", exit_code=0)
    record(temp_db, started="2026-09-30 06:00:00", exit_code=1)

    assert temp_db.latest_job_runs()["flow4"]["started_at"] == "2026-10-01 06:00:00"


def test_recent_runs_are_newest_first_and_carry_the_output(temp_db):
    record(temp_db, started="2026-09-29 06:00:00")
    record(temp_db, started="2026-10-01 06:00:00")

    runs = temp_db.recent_job_runs("flow4")

    assert [r["started_at"] for r in runs] == ["2026-10-01 06:00:00",
                                               "2026-09-29 06:00:00"]
    assert runs[0]["output_tail"] == "all done"


def test_recent_runs_honours_its_limit(temp_db):
    for day in range(1, 6):
        record(temp_db, started=f"2026-10-0{day} 06:00:00")

    assert len(temp_db.recent_job_runs("flow4", limit=2)) == 2


def test_purge_drops_runs_older_than_retention_and_keeps_newer_ones(temp_db):
    with temp_db.get_conn() as conn:
        conn.execute(
            """INSERT INTO job_runs (slug, started_at, finished_at, duration_ms,
                                     exit_code, ok, host, output_tail, received_at)
               VALUES ('old', '2020-01-01 00:00:00', '2020-01-01 00:01:00',
                       60000, 0, 1, '', '', datetime('now', 'localtime', '-200 days'))""")
    record(temp_db, slug="new")

    temp_db.purge_old_job_runs()

    assert list(temp_db.latest_job_runs()) == ["new"]


def test_counts_only_runs_received_inside_the_window(temp_db):
    with temp_db.get_conn() as conn:
        conn.execute(
            """INSERT INTO job_runs (slug, started_at, finished_at, duration_ms,
                                     exit_code, ok, host, output_tail, received_at)
               VALUES ('old', '2026-09-01 06:00:00', '2026-09-01 06:01:00',
                       60000, 0, 1, '', '', datetime('now', 'localtime', '-30 hours'))""")
    record(temp_db, slug="new")

    assert temp_db.job_runs_since(24) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_jobs_db.py -v`
Expected: FAIL — `AttributeError: module 'db' has no attribute 'record_job_run'`

- [ ] **Step 3: Add the schema**

In `db.py`, append this to the end of the `SCHEMA` string, after the `idx_checks_link_time` index and before the closing `"""`:

```sql

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
```

Both clocks are stored on purpose: `started_at` is what the job believes and `received_at` is what the dashboard observed, so a host with a wrong clock shows as a gap rather than a mystery.

- [ ] **Step 4: Add the functions**

Append to `db.py`:

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_jobs_db.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Wire the purge into the existing scheduler**

Job runs arrive about once a day, so they need no scheduler of their own. In `app.py`, inside `start_scheduler`, add a second job after the existing `health` one:

```python
    # Job runs are tiny and arrive about daily, so once a day is plenty.
    scheduler.add_job(db.purge_old_job_runs, "interval", hours=24,
                      id="purge-job-runs")
```

- [ ] **Step 7: Run the whole suite and commit**

Run: `python -m pytest -q`
Expected: all tests pass, including the existing link tests

```bash
git add db.py app.py tests/test_jobs_db.py
git commit -m "Store scheduled job runs, kept for 90 days"
```

---

### Task 2: Declare jobs in config

**Files:**
- Create: `jobs.py`
- Modify: `config.ini` (append a documented section)
- Test: `tests/test_jobs.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `jobs.Job` — `namedtuple("Job", "slug name expect_every grace host")`, where `expect_every` and `grace` are **integer seconds**
  - `jobs.parse_duration(text) -> int` (seconds), raises `jobs.ConfigError`
  - `jobs.load_jobs(config) -> dict[str, Job]` keyed by slug, in config order
  - `jobs.ConfigError`
  - `jobs.DEFAULT_GRACE = "1h"`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_jobs.py`:

```python
"""Reading [job:...] sections out of config.ini."""
import configparser
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jobs


def cfg(text):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(text)
    return parser


# ----------------------------------------------------------- parse_duration

@pytest.mark.parametrize("text, seconds", [
    ("30m", 1800),
    ("6h", 21600),
    ("24h", 86400),
    ("7d", 604800),
    (" 12H ", 43200),
])
def test_reads_the_duration_forms_the_config_file_documents(text, seconds):
    assert jobs.parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "daily", "24", "h", "0h", "-3h", "1w"])
def test_rejects_anything_it_cannot_read_rather_than_guessing(text):
    with pytest.raises(jobs.ConfigError):
        jobs.parse_duration(text)


# --------------------------------------------------------------- load_jobs

def test_reads_a_job_section_keyed_by_its_slug():
    loaded = jobs.load_jobs(cfg("""
[job:excel-bom-compare]
name         = Excel BOM Compare (FLOW4)
expect_every = 24h
grace        = 2h
host         = 192.0.2.20
"""))

    assert list(loaded) == ["excel-bom-compare"]
    job = loaded["excel-bom-compare"]
    assert job.name == "Excel BOM Compare (FLOW4)"
    assert job.expect_every == 86400
    assert job.grace == 7200
    assert job.host == "192.0.2.20"


def test_grace_defaults_to_an_hour_when_not_given():
    loaded = jobs.load_jobs(cfg("""
[job:nightly]
expect_every = 24h
"""))

    assert loaded["nightly"].grace == 3600


def test_name_falls_back_to_the_slug():
    loaded = jobs.load_jobs(cfg("""
[job:nightly]
expect_every = 24h
"""))

    assert loaded["nightly"].name == "nightly"


def test_ignores_sections_that_are_not_jobs():
    loaded = jobs.load_jobs(cfg("""
[server]
port = 8090

[process:some-app]
link = some-app
"""))

    assert loaded == {}


def test_a_broken_job_is_skipped_without_taking_the_good_ones_down(capsys):
    """One typo in config.ini must not stop the dashboard from starting."""
    loaded = jobs.load_jobs(cfg("""
[job:broken]
expect_every = every day

[job:fine]
expect_every = 24h
"""))

    assert list(loaded) == ["fine"]
    assert "broken" in capsys.readouterr().out


def test_a_job_with_no_expect_every_is_skipped():
    """Without it there is no such thing as overdue, which is the point."""
    assert jobs.load_jobs(cfg("""
[job:no-schedule]
name = Something
""")) == {}


def test_keeps_config_order():
    loaded = jobs.load_jobs(cfg("""
[job:zebra]
expect_every = 24h

[job:apple]
expect_every = 24h
"""))

    assert list(loaded) == ["zebra", "apple"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_jobs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobs'`

- [ ] **Step 3: Write `jobs.py`**

```python
"""Scheduled jobs: what we expect to hear from, and how late it is.

Jobs are declared in config.ini as [job:<slug>] sections, the same way
start buttons are. Nothing here reaches out to a job's machine - jobs
report in to the dashboard, so a job we never hear from is simply late.
"""
import collections
import re

Job = collections.namedtuple("Job", "slug name expect_every grace host")

SECTION_PREFIX = "job:"
DEFAULT_GRACE = "1h"

_UNITS = {"m": 60, "h": 3600, "d": 86400}
_DURATION = re.compile(r"\s*(\d+)\s*([mhd])\s*", re.IGNORECASE)


class ConfigError(Exception):
    """A [job:...] section the dashboard cannot use."""


def parse_duration(text):
    """'30m' / '6h' / '7d' -> seconds.

    Deliberately narrow: a schedule that is read wrong produces alerts
    that are wrong, which is worse than refusing to read it at all.
    """
    match = _DURATION.fullmatch(text or "")
    if not match:
        raise ConfigError(
            f"expected a duration like 30m, 6h or 7d - got {text!r}")
    value = int(match.group(1))
    if value < 1:
        raise ConfigError(f"duration must be at least 1 - got {text!r}")
    return value * _UNITS[match.group(2).lower()]


def load_jobs(config):
    """Map slug -> Job for every [job:<slug>] section, in config order.

    A section we cannot read is skipped with a printed reason rather than
    raising: one typo should not take the whole dashboard, links and all,
    off the air.
    """
    loaded = {}
    for section in config.sections():
        if not section.startswith(SECTION_PREFIX):
            continue
        slug = section[len(SECTION_PREFIX):].strip()
        try:
            if not slug:
                raise ConfigError("section name has no slug after 'job:'")
            expect_every = parse_duration(
                config.get(section, "expect_every", fallback=""))
            grace = parse_duration(
                config.get(section, "grace", fallback=DEFAULT_GRACE))
        except ConfigError as exc:
            print(f"[jobs] ignoring [{section}]: {exc}")
            continue
        loaded[slug] = Job(
            slug=slug,
            name=config.get(section, "name", fallback="").strip() or slug,
            expect_every=expect_every,
            grace=grace,
            host=config.get(section, "host", fallback="").strip(),
        )
    return loaded
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_jobs.py -v`
Expected: PASS (16 tests, counting the parametrised ones)

- [ ] **Step 5: Document the section in `config.ini`**

Append to `config.ini`, matching the banner-comment style the file already uses for Start buttons:

```ini
; =====================================================================
; SCHEDULED JOBS  -  things that run on a timer, not things you visit
;
; A job is a batch program somewhere on the network - usually a Windows
; Scheduled Task - that should run daily or weekly and finish cleanly.
; It has no web page, so there is nothing for the health check to ping.
;
; Instead the JOB reports in to this dashboard when it finishes. The
; dashboard never contacts the job's machine, so jobs can live on any
; server, on any subnet, with no firewall rule pointing inwards.
;
; To add a job:
;   1. Add a [job:<slug>] section below
;   2. Copy reporter\report.py onto the machine that runs the job
;   3. Change that machine's Scheduled Task to run the job THROUGH it:
;
;      python C:\tools\report.py <slug> --url http://192.0.2.10:8090
;             -- <the command the task runs today>
;
; A job that stops running sends nothing, and shows here as Overdue -
; which is the failure you cannot see by reading its log.
; =====================================================================

[jobs]
; Shared secret the wrappers send as the X-Job-Token header.
; Leave blank to accept reports without a token.
token =

; expect_every = how often it should run      (30m / 6h / 24h / 7d)
; grace        = how late is still acceptable  (default 1h)
; host         = shown on the dashboard, not used to contact anything

[job:excel-bom-compare]
name         = Excel BOM Compare (FLOW4)
expect_every = 24h
grace        = 2h
host         = 192.0.2.20
```

- [ ] **Step 6: Run the whole suite and commit**

Run: `python -m pytest -q`
Expected: all tests pass

```bash
git add jobs.py config.ini tests/test_jobs.py
git commit -m "Declare scheduled jobs in config.ini"
```

---

### Task 3: Decide whether a job is healthy

**Files:**
- Modify: `jobs.py` (append)
- Test: `tests/test_jobs_status.py` (create)

**Interfaces:**
- Consumes: `jobs.Job` from Task 2; run rows shaped like `db.latest_job_runs()` values from Task 1 (needs keys `started_at` and `ok`; plain dicts work).
- Produces:
  - `jobs.STATUS_NEVER = "never"`, `jobs.STATUS_OVERDUE = "overdue"`, `jobs.STATUS_FAILED = "failed"`, `jobs.STATUS_OK = "ok"`
  - `jobs.derive_status(job, run, now) -> str` — `run` may be `None`, `now` is a `datetime`
  - `jobs.next_expected(job, run) -> datetime | None`
  - `jobs.build_rows(loaded_jobs, latest, now) -> list[dict]` with keys `job`, `run`, `status`, `next_expected`
  - `jobs.summarize(rows, runs_24h) -> dict` with keys `total`, `ok`, `failed`, `overdue`, `never`, `runs_24h`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_jobs_status.py`:

```python
"""Is this job healthy? never -> overdue -> failed -> ok."""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jobs

NOW = datetime.datetime(2026, 10, 1, 12, 0, 0)
DAILY = jobs.Job("flow4", "Excel BOM Compare", 86400, 3600, "192.0.2.20")


def run(hours_ago, ok=1):
    started = NOW - datetime.timedelta(hours=hours_ago)
    return {"started_at": started.strftime("%Y-%m-%d %H:%M:%S"), "ok": ok}


def test_a_job_that_has_never_reported_is_never():
    assert jobs.derive_status(DAILY, None, NOW) == jobs.STATUS_NEVER


def test_a_recent_successful_run_is_ok():
    assert jobs.derive_status(DAILY, run(2), NOW) == jobs.STATUS_OK


def test_a_recent_failed_run_is_failed():
    assert jobs.derive_status(DAILY, run(2, ok=0), NOW) == jobs.STATUS_FAILED


def test_a_successful_run_that_is_too_old_is_overdue():
    """Passing yesterday means nothing if it never ran today."""
    assert jobs.derive_status(DAILY, run(30), NOW) == jobs.STATUS_OVERDUE


def test_overdue_outranks_failed():
    """Two problems; the one you cannot see in the log is that it stopped."""
    assert jobs.derive_status(DAILY, run(30, ok=0), NOW) == jobs.STATUS_OVERDUE


def test_inside_the_grace_window_it_is_not_overdue_yet():
    """24h schedule + 1h grace: at 24.5h it is late but not yet alarming."""
    assert jobs.derive_status(DAILY, run(24.5), NOW) == jobs.STATUS_OK


def test_just_past_the_grace_window_it_is_overdue():
    assert jobs.derive_status(DAILY, run(25.1), NOW) == jobs.STATUS_OVERDUE


def test_next_expected_is_one_interval_after_the_last_start():
    assert jobs.next_expected(DAILY, run(2)) == NOW + datetime.timedelta(hours=22)


def test_next_expected_is_unknown_before_the_first_run():
    assert jobs.next_expected(DAILY, None) is None


# ----------------------------------------------------------------- rows

def test_build_rows_pairs_each_job_with_its_latest_run():
    loaded = {"flow4": DAILY}

    rows = jobs.build_rows(loaded, {"flow4": run(2)}, NOW)

    assert len(rows) == 1
    assert rows[0]["job"] is DAILY
    assert rows[0]["status"] == jobs.STATUS_OK
    assert rows[0]["next_expected"] is not None


def test_build_rows_keeps_a_job_that_has_no_runs_at_all():
    rows = jobs.build_rows({"flow4": DAILY}, {}, NOW)

    assert rows[0]["status"] == jobs.STATUS_NEVER
    assert rows[0]["run"] is None


def test_summary_counts_each_status():
    other = DAILY._replace(slug="other", name="Other")
    rows = jobs.build_rows(
        {"flow4": DAILY, "other": other},
        {"flow4": run(2), "other": run(2, ok=0)}, NOW)

    summary = jobs.summarize(rows, runs_24h=7)

    assert summary == {"total": 2, "ok": 1, "failed": 1, "overdue": 0,
                       "never": 0, "runs_24h": 7}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_jobs_status.py -v`
Expected: FAIL — `AttributeError: module 'jobs' has no attribute 'derive_status'`

- [ ] **Step 3: Append the status logic to `jobs.py`**

Add `import datetime` to the imports at the top of `jobs.py`, then append:

```python
STATUS_NEVER = "never"
STATUS_OVERDUE = "overdue"
STATUS_FAILED = "failed"
STATUS_OK = "ok"

STAMP = "%Y-%m-%d %H:%M:%S"


def _started(run):
    """When this run began. Ingest validates the format, so this is safe."""
    return datetime.datetime.strptime(run["started_at"], STAMP)


def derive_status(job, run, now):
    """never -> overdue -> failed -> ok.

    Overdue outranks failed on purpose: a job that failed last night and
    has not run since has two problems, and the one you cannot see by
    reading its log is that it stopped running at all.
    """
    if run is None:
        return STATUS_NEVER
    late_by = (now - _started(run)).total_seconds()
    if late_by > job.expect_every + job.grace:
        return STATUS_OVERDUE
    return STATUS_OK if run["ok"] else STATUS_FAILED


def next_expected(job, run):
    """When this job should next be heard from, or None before its first."""
    if run is None:
        return None
    return _started(run) + datetime.timedelta(seconds=job.expect_every)


def build_rows(loaded_jobs, latest, now):
    """One display row per configured job, in config order."""
    rows = []
    for job in loaded_jobs.values():
        run = latest.get(job.slug)
        rows.append({
            "job": job,
            "run": run,
            "status": derive_status(job, run, now),
            "next_expected": next_expected(job, run),
        })
    return rows


def summarize(rows, runs_24h):
    counts = collections.Counter(row["status"] for row in rows)
    return {
        "total": len(rows),
        "ok": counts[STATUS_OK],
        "failed": counts[STATUS_FAILED],
        "overdue": counts[STATUS_OVERDUE],
        "never": counts[STATUS_NEVER],
        "runs_24h": runs_24h,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_jobs_status.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Run the whole suite and commit**

Run: `python -m pytest -q`

```bash
git add jobs.py tests/test_jobs_status.py
git commit -m "Work out whether a job is late, failed or fine"
```

---

### Task 4: Accept reports from jobs

**Files:**
- Modify: `app.py` (imports, module constants, `RESERVED_SLUGS` at `app.py:36`, new route)
- Test: `tests/test_report_endpoint.py` (create)

**Interfaces:**
- Consumes: `db.record_job_run` (Task 1), `jobs.load_jobs` (Task 2).
- Produces:
  - `POST /jobs/<slug>/report` — JSON in, `{"ok": true}` + 201 out
  - `app.JOBS: dict[str, jobs.Job]`, `app.JOB_TOKEN: str`
  - `app.TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"`, `app.MAX_OUTPUT_TAIL = 8000`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_report_endpoint.py`:

```python
"""POST /jobs/<slug>/report - the only way runs get in."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
import jobs

GOOD = {
    "started_at": "2026-10-01 06:00:03",
    "finished_at": "2026-10-01 06:14:22",
    "duration_ms": 859000,
    "exit_code": 0,
    "host": "JOBHOST01",
    "output_tail": "Status: SUCCESS",
}


@pytest.fixture
def client(temp_db, monkeypatch):
    """A test client with one job configured and no token required."""
    monkeypatch.setattr(app_module, "JOBS", {
        "flow4": jobs.Job("flow4", "Excel BOM Compare", 86400, 3600, "")})
    monkeypatch.setattr(app_module, "JOB_TOKEN", "")
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def post(client, body, slug="flow4", headers=None):
    return client.post(f"/jobs/{slug}/report",
                       data=json.dumps(body),
                       content_type="application/json",
                       headers=headers or {})


def test_a_good_report_is_stored(client, temp_db):
    response = post(client, GOOD)

    assert response.status_code == 201
    run = temp_db.latest_job_runs()["flow4"]
    assert run["started_at"] == "2026-10-01 06:00:03"
    assert run["duration_ms"] == 859000
    assert run["ok"] == 1


def test_a_non_zero_exit_code_is_stored_as_not_ok(client, temp_db):
    post(client, dict(GOOD, exit_code=1))

    assert temp_db.latest_job_runs()["flow4"]["ok"] == 0


def test_an_unknown_slug_is_rejected_so_a_typo_is_loud(client, temp_db):
    """A silently accepted typo becomes a job that waits forever."""
    response = post(client, GOOD, slug="flow-four")

    assert response.status_code == 404
    assert temp_db.latest_job_runs() == {}


@pytest.mark.parametrize("body", [
    dict(GOOD, started_at="01/10/2026 06:00"),
    dict(GOOD, duration_ms="a while"),
    dict(GOOD, exit_code=None),
    {k: v for k, v in GOOD.items() if k != "finished_at"},
    [1, 2, 3],
])
def test_a_malformed_report_is_rejected(client, temp_db, body):
    response = post(client, body)

    assert response.status_code == 400
    assert temp_db.latest_job_runs() == {}


def test_a_body_that_is_not_json_is_rejected(client):
    response = client.post("/jobs/flow4/report", data="hello",
                           content_type="application/json")

    assert response.status_code == 400


def test_thai_text_and_emoji_survive_the_round_trip(client, temp_db):
    tail = "🚀 เริ่มรัน EXCEL-FLOW ✅ เสร็จสิ้น"

    post(client, dict(GOOD, output_tail=tail))

    assert temp_db.recent_job_runs("flow4")[0]["output_tail"] == tail


def test_a_very_long_output_is_trimmed_from_the_front(client, temp_db):
    """The end of a log is where the error is, so the front is what goes."""
    post(client, dict(GOOD,
                      output_tail="x" * app_module.MAX_OUTPUT_TAIL + "THE ERROR"))
    stored = temp_db.recent_job_runs("flow4")[0]["output_tail"]

    assert stored.endswith("THE ERROR")
    assert len(stored) == app_module.MAX_OUTPUT_TAIL


# --------------------------------------------------------------- the token

def test_the_right_token_is_accepted(temp_db, monkeypatch):
    monkeypatch.setattr(app_module, "JOBS", {
        "flow4": jobs.Job("flow4", "Excel BOM Compare", 86400, 3600, "")})
    monkeypatch.setattr(app_module, "JOB_TOKEN", "s3cret")
    client = app_module.app.test_client()

    response = post(client, GOOD, headers={"X-Job-Token": "s3cret"})

    assert response.status_code == 201


@pytest.mark.parametrize("headers", [{}, {"X-Job-Token": "wrong"}])
def test_a_wrong_or_missing_token_is_rejected(temp_db, monkeypatch, headers):
    monkeypatch.setattr(app_module, "JOBS", {
        "flow4": jobs.Job("flow4", "Excel BOM Compare", 86400, 3600, "")})
    monkeypatch.setattr(app_module, "JOB_TOKEN", "s3cret")
    client = app_module.app.test_client()

    response = post(client, GOOD, headers=headers)

    assert response.status_code == 401
    assert temp_db.latest_job_runs() == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_report_endpoint.py -v`
Expected: FAIL — `AttributeError: module 'app' has no attribute 'JOBS'`

- [ ] **Step 3: Add the module constants**

In `app.py`, add `import jobs` to the imports beside `import runner`. Then, next to the other config reads near `app.py:21-26`, add:

```python
JOBS = jobs.load_jobs(config)
JOB_TOKEN = config.get("jobs", "token", fallback="").strip()
```

And add `"jobs"` to `RESERVED_SLUGS` (`app.py:36`), or a link could be given a slug that shadows the new routes:

```python
RESERVED_SLUGS = {"go", "manage", "links", "refresh", "static", "services",
                  "jobs"}
```

Then add the format constants beside `TREND_DAYS`:

```python
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
# the end of a log is where the error is, so long output is cut from the front
MAX_OUTPUT_TAIL = 8000
```

- [ ] **Step 4: Add the route**

Add to `app.py`, after the `start_all` route and before `refresh_now`:

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_report_endpoint.py -v`
Expected: PASS (13 tests, counting the parametrised ones)

- [ ] **Step 6: Report configured jobs at startup**

The app already shouts about Start buttons it could not wire up. Do the same for jobs. In `app.py`, inside `main()`, after `report_start_button_config()`:

```python
    print(f"[jobs] watching {len(JOBS)} scheduled job(s): "
          f"{', '.join(JOBS) if JOBS else 'none'}")
```

- [ ] **Step 7: Run the whole suite and commit**

Run: `python -m pytest -q`

```bash
git add app.py tests/test_report_endpoint.py
git commit -m "Accept run reports from jobs over HTTP"
```

---

### Task 5: The wrapper that reports

**Files:**
- Create: `reporter/report.py`
- Test: `tests/test_reporter.py` (create)

**Interfaces:**
- Consumes: the endpoint from Task 4 (over HTTP; the tests use a local stub, not the Flask app).
- Produces:
  - `report.Run` — `namedtuple("Run", "exit_code output started finished duration_ms")`
  - `report.run_job(command) -> Run`
  - `report.post_report(url, slug, token, run) -> int` (HTTP status; raises on failure)
  - `report.report_with_retries(url, slug, token, run, log_path) -> bool`
  - `report.main(argv=None) -> int` (the child's exit code)

**This task contains the single most important test in the plan:** the wrapper must exit with the child's code even when reporting fails.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reporter.py`:

```python
"""The wrapper Task Scheduler runs instead of the job itself."""
import http.server
import json
import os
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "reporter"))

import report


@pytest.fixture
def collector():
    """A stub dashboard that remembers what was posted to it."""
    received = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            received.append({"path": self.path,
                             "token": self.headers.get("X-Job-Token"),
                             "body": json.loads(body)})
            self.send_response(201)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", received
    server.shutdown()


def python_that(code):
    return [sys.executable, "-c", code]


# ----------------------------------------------------------------- run_job

def test_captures_a_successful_child(tmp_path):
    run = report.run_job(python_that("print('hello')"))

    assert run.exit_code == 0
    assert "hello" in run.output
    assert run.duration_ms >= 0


def test_captures_a_failing_child_and_its_stderr():
    run = report.run_job(
        python_that("import sys; sys.stderr.write('boom'); sys.exit(3)"))

    assert run.exit_code == 3
    assert "boom" in run.output


def test_a_command_that_does_not_exist_is_not_a_crash():
    run = report.run_job(["definitely-not-a-real-program-xyz"])

    assert run.exit_code != 0
    assert "could not start" in run.output


def test_thai_and_emoji_in_child_output_survive():
    run = report.run_job(python_that(
        "print('\\U0001F680 \\u0e40\\u0e23\\u0e34\\u0e48\\u0e21\\u0e23\\u0e31\\u0e19')"))

    assert "🚀" in run.output


# ------------------------------------------------------------ posting home

def test_posts_the_run_to_the_dashboard(collector, tmp_path):
    url, received = collector
    run = report.run_job(python_that("print('done')"))

    assert report.report_with_retries(url, "flow4", "", run,
                                      str(tmp_path / "err.log")) is True
    assert received[0]["path"] == "/jobs/flow4/report"
    body = received[0]["body"]
    assert body["exit_code"] == 0
    assert "done" in body["output_tail"]
    assert body["host"]


def test_sends_the_token_when_one_is_given(collector, tmp_path):
    url, received = collector
    run = report.run_job(python_that("pass"))

    report.report_with_retries(url, "flow4", "s3cret", run,
                               str(tmp_path / "err.log"))

    assert received[0]["token"] == "s3cret"


def test_a_failed_post_is_logged_and_reported_as_false(tmp_path):
    log = tmp_path / "err.log"
    run = report.run_job(python_that("pass"))

    # port 9 is reserved and refuses immediately
    assert report.report_with_retries("http://127.0.0.1:9", "flow4", "", run,
                                      str(log)) is False
    assert "flow4" in log.read_text(encoding="utf-8")


# ---------------------------------------------------------- the whole thing

def test_main_exits_with_the_childs_code(collector):
    url, _ = collector

    code = report.main(["flow4", "--url", url, "--",
                        sys.executable, "-c", "import sys; sys.exit(7)"])

    assert code == 7


def test_main_still_exits_with_the_childs_code_when_reporting_fails(tmp_path,
                                                                    monkeypatch):
    """The whole design rests on this.

    If a dashboard being down could turn a good run into a bad one, the
    monitoring would be worse than no monitoring at all.
    """
    monkeypatch.chdir(tmp_path)

    code = report.main(["flow4", "--url", "http://127.0.0.1:9", "--",
                        sys.executable, "-c", "print('fine')"])

    assert code == 0


def test_main_needs_a_command_after_the_separator():
    with pytest.raises(SystemExit):
        report.main(["flow4", "--url", "http://127.0.0.1:9"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_reporter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'report'`

- [ ] **Step 3: Write `reporter/report.py`**

```python
"""Run a scheduled job and tell the dashboard how it went.

Task Scheduler runs this instead of the job itself:

    python report.py excel-bom-compare --url http://192.0.2.10:8090
           -- D:\\Filepackage\\python\\dist\\FLOW4.exe

Standard library only, on purpose: the servers that run jobs have no
packages installed and should not need any to be monitored.

The one rule this file must never break: reporting cannot change the
job's outcome. Whatever the job exits with, this exits with too.
"""
import argparse
import collections
import datetime
import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request

Run = collections.namedtuple("Run",
                             "exit_code output started finished duration_ms")

STAMP = "%Y-%m-%d %H:%M:%S"
TAIL_CHARS = 4000
ATTEMPTS = 3
RETRY_SECONDS = 5
POST_TIMEOUT = 15
ERROR_LOG = "report_errors.log"
# nothing ran, so the exit code is ours to pick: 127 is the shell's
# "command not found", which is what actually happened
COULD_NOT_START = 127


def run_job(command):
    """Run the job to completion, capturing stdout and stderr together."""
    # the flows print Thai and emoji, which raise UnicodeEncodeError on a
    # cp874 console - force the child to speak UTF-8 so we can read it
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    started = datetime.datetime.now()
    clock = time.perf_counter()
    try:
        done = subprocess.run(command, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT)
        output = done.stdout.decode("utf-8", errors="replace")
        exit_code = done.returncode
    except OSError as exc:
        output = f"could not start {command[0]!r}: {exc}"
        exit_code = COULD_NOT_START
    return Run(exit_code, output, started, datetime.datetime.now(),
               int((time.perf_counter() - clock) * 1000))


def post_report(url, slug, token, run):
    """POST one run to the dashboard. Raises if it does not land."""
    payload = json.dumps({
        "started_at": run.started.strftime(STAMP),
        "finished_at": run.finished.strftime(STAMP),
        "duration_ms": run.duration_ms,
        "exit_code": run.exit_code,
        "host": platform.node(),
        "output_tail": run.output[-TAIL_CHARS:],
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{url.rstrip('/')}/jobs/{slug}/report", data=payload, method="POST")
    request.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        request.add_header("X-Job-Token", token)
    with urllib.request.urlopen(request, timeout=POST_TIMEOUT) as response:
        return response.status


def note(log_path, message):
    """Leave a trace locally - this is the only record if the POST failed."""
    line = f"{datetime.datetime.now().strftime(STAMP)} {message}"
    try:
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(line + "\n")
    except OSError:
        pass            # even logging must not be able to break the job
    sys.stderr.write("[report] " + message + "\n")


def report_with_retries(url, slug, token, run, log_path):
    """Try to report. Never raises. True if the dashboard took it.

    A dropped report shows on the dashboard as 'overdue', which is a
    false alarm we accept: the dashboard being unreachable is itself
    worth noticing, and a disk-backed spool is complexity we do not need.
    """
    problem = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            post_report(url, slug, token, run)
            return True
        except (urllib.error.URLError, OSError, ValueError) as exc:
            problem = exc
            if attempt < ATTEMPTS:
                time.sleep(RETRY_SECONDS)
    note(log_path, f"{slug}: could not report after {ATTEMPTS} tries: {problem}")
    return False


def main(argv=None):
    # argparse.REMAINDER does not mix with a required option placed before
    # it (the remainder swallows "--url" instead of leaving it to be
    # parsed), so the split is done by hand: everything after the first
    # "--" is the command, argparse only ever sees what comes before it.
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" in argv:
        split = argv.index("--")
        head, command = argv[:split], argv[split + 1:]
    else:
        head, command = argv, []

    parser = argparse.ArgumentParser(
        description="Run a job and report the result to the dashboard.")
    parser.add_argument("slug", help="matches a [job:<slug>] section")
    parser.add_argument("--url", required=True,
                        help="dashboard root, e.g. http://192.0.2.10:8090")
    parser.add_argument("--token", default="",
                        help="must match [jobs] token on the dashboard")
    args = parser.parse_args(head)

    if not command:
        parser.error("no command given - put it after --")

    run = run_job(command)
    report_with_retries(args.url, args.slug, args.token, run,
                        os.path.join(os.getcwd(), ERROR_LOG))

    # pass the job's own output through, as bytes: re-encoding it for a
    # cp874 console is exactly the crash we told the child to avoid
    sys.stdout.buffer.write(run.output.encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()
    return run.exit_code


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_reporter.py -v`
Expected: PASS (11 tests)

Note: `test_a_failed_post_is_logged_and_reported_as_false` takes about 10 s — two 5-second backoffs are real.

- [ ] **Step 5: Run the whole suite and commit**

Run: `python -m pytest -q`

```bash
git add reporter/report.py tests/test_reporter.py
git commit -m "Add report.py, the wrapper a Scheduled Task runs"
```

---

### Task 6: Show jobs on the dashboard

**Files:**
- Modify: `app.py` (the `dashboard` route; two new template filters)
- Modify: `templates/dashboard.html` (toggle + wrap the links view)
- Create: `templates/_jobs.html`
- Modify: `static/components.css` (append the toggle)
- Test: `tests/test_dashboard_views.py` (create)

**Interfaces:**
- Consumes: `jobs.build_rows`, `jobs.summarize` (Task 3), `db.latest_job_runs`, `db.job_runs_since` (Task 1), `app.JOBS` (Task 4).
- Produces: `GET /?view=jobs`; Jinja filters `ago` and `duration`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard_views.py`:

```python
"""The Links | Jobs toggle, and what each view shows."""
import datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
import jobs


@pytest.fixture
def client(temp_db, monkeypatch):
    monkeypatch.setattr(app_module, "JOBS", {
        "flow4": jobs.Job("flow4", "Excel BOM Compare", 86400, 3600,
                          "192.0.2.20")})
    monkeypatch.setattr(app_module, "JOB_TOKEN", "")
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def test_the_default_view_is_links(client):
    page = client.get("/").get_data(as_text=True)

    assert "Clicks today" in page


def test_the_jobs_view_lists_configured_jobs(client):
    page = client.get("/?view=jobs").get_data(as_text=True)

    assert "Excel BOM Compare" in page
    assert "192.0.2.20" in page


def test_a_job_with_no_runs_reads_as_waiting(client):
    page = client.get("/?view=jobs").get_data(as_text=True)

    assert "Waiting for first report" in page


def test_a_fresh_successful_run_reads_as_ok(client, temp_db):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    temp_db.record_job_run("flow4", now, now, 1000, 0, "SRV", "fine")

    page = client.get("/?view=jobs").get_data(as_text=True)

    assert "status-ok" in page


def test_a_run_older_than_the_schedule_reads_as_overdue(client, temp_db):
    old = (datetime.datetime.now()
           - datetime.timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    temp_db.record_job_run("flow4", old, old, 1000, 0, "SRV", "fine")

    page = client.get("/?view=jobs").get_data(as_text=True)

    assert "Overdue" in page


def test_both_counts_show_on_the_toggle_whichever_view_is_open(client):
    page = client.get("/").get_data(as_text=True)

    assert "view=jobs" in page


# ------------------------------------------------------------- the filters

@pytest.mark.parametrize("ms, text", [
    (None, "—"),
    (4500, "4s"),
    (65000, "1m 05s"),
    (859000, "14m 19s"),
])
def test_duration_reads_the_way_a_person_would_say_it(ms, text):
    assert app_module.duration(ms) == text


def test_ago_says_just_now_for_something_that_just_happened():
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    assert app_module.ago(now) == "just now"


def test_ago_counts_hours_then_days():
    then = datetime.datetime.now() - datetime.timedelta(hours=5)
    long_ago = datetime.datetime.now() - datetime.timedelta(days=3)

    assert app_module.ago(then.strftime("%Y-%m-%d %H:%M:%S")) == "5 h ago"
    assert app_module.ago(long_ago.strftime("%Y-%m-%d %H:%M:%S")) == "3 d ago"


def test_ago_of_nothing_is_never():
    assert app_module.ago(None) == "never"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_dashboard_views.py -v`
Expected: FAIL — `AttributeError: module 'app' has no attribute 'duration'`

- [ ] **Step 3: Add the filters and update the route**

In `app.py`, add after the `slugify` / `unique_slug` helpers:

```python
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
        return "\u2014"
    seconds = int(ms) // 1000
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60:02d}s"
```

Then replace the `dashboard` route body:

```python
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
```

- [ ] **Step 4: Add the toggle to `templates/dashboard.html`**

Insert this immediately after `{% block content %}` and before the `{% set pct = ... %}` line:

```html
{# One page, two questions: is the web app up, and did the batch job run. #}
<nav class="view-toggle">
  <a href="{{ url_for('dashboard') }}"
     class="{{ 'active' if view == 'links' }}">
    Links <span class="count">{{ summary.total }}</span></a>
  <a href="{{ url_for('dashboard', view='jobs') }}"
     class="{{ 'active' if view == 'jobs' }}">
    Jobs <span class="count">{{ job_summary.total }}</span>
    {%- if job_summary.overdue or job_summary.failed %}
    <span class="count count-bad">{{ job_summary.overdue + job_summary.failed }}</span>
    {%- endif %}</a>
</nav>

{% if view == 'jobs' %}{% include "_jobs.html" %}{% else %}
```

Then, immediately before the final `{% endblock %}` at the bottom of the file, add:

```html
{% endif %}
```

Everything already in the file between those two points becomes the links view, unchanged.

- [ ] **Step 5: Create `templates/_jobs.html`**

```html
{# The jobs view. Reuses the links view's status pills so a red pill means
   the same thing on both: this needs looking at. #}
<div class="page-head">
  <div>
    <h1>Scheduled jobs</h1>
    <p class="page-note">
      Jobs report in when they finish - this dashboard never contacts
      their machines. A job that stops running goes Overdue on its own.
    </p>
  </div>
</div>

<div class="stat-band">
  <div class="stat">
    <div class="stat-label">Jobs watched</div>
    <div class="stat-value">{{ job_summary.total }}</div>
  </div>
  <div class="stat">
    <div class="stat-label">Passing</div>
    <div class="stat-value ok-text">{{ job_summary.ok }}</div>
  </div>
  <div class="stat {{ 'stat-alert' if job_summary.failed }}">
    <div class="stat-label">Failed</div>
    <div class="stat-value {{ 'bad-text' if job_summary.failed else '' }}">{{ job_summary.failed }}</div>
  </div>
  <div class="stat {{ 'stat-alert' if job_summary.overdue }}">
    <div class="stat-label">Overdue</div>
    <div class="stat-value {{ 'bad-text' if job_summary.overdue else '' }}">{{ job_summary.overdue }}</div>
  </div>
  <div class="stat">
    <div class="stat-label">Reports in 24 h</div>
    <div class="stat-value">{{ job_summary.runs_24h }}</div>
  </div>
</div>

{% if job_rows %}
<div class="table-wrap">
<table class="links-table">
  <thead>
    <tr>
      <th>Job</th>
      <th>Status</th>
      <th>Last run</th>
      <th class="num">Took</th>
      <th>Next expected</th>
      <th class="num">Exit</th>
    </tr>
  </thead>
  <tbody>
  {% for r in job_rows %}
    <tr>
      <td class="cell-link">
        <div class="link-name">
          <a href="{{ url_for('job_detail', slug=r.job.slug) }}">{{ r.job.name }}</a>
        </div>
        <div class="short-link">{{ r.job.slug }}</div>
        {% if r.job.host %}<div class="link-url">on {{ r.job.host }}</div>{% endif %}
      </td>
      <td data-label="Status">
        {% if r.status == 'ok' %}
          <span class="status status-ok"><span class="dot"></span>Passing</span>
        {% elif r.status == 'failed' %}
          <span class="status status-bad"><span class="dot"></span>Failed</span>
        {% elif r.status == 'overdue' %}
          <span class="status status-bad"><span class="dot"></span>Overdue</span>
        {% else %}
          <span class="status status-off"><span class="dot"></span>Waiting for first report</span>
        {% endif %}
      </td>
      <td data-label="Last run">
        {{ r.run["started_at"] | ago if r.run else "never" }}
        {% if r.run %}<div class="dim">{{ r.run["started_at"][5:16] }}</div>{% endif %}
      </td>
      <td class="num" data-label="Took">
        {% if r.run %}{{ r.run["duration_ms"] | duration }}{% else %}&mdash;{% endif %}
      </td>
      <td data-label="Next expected">
        {% if r.next_expected %}
          {{ r.next_expected.strftime('%m-%d %H:%M') }}
        {% else %}<span class="dim">unknown</span>{% endif %}
      </td>
      <td class="num" data-label="Exit">
        {% if r.run %}{{ r.run["exit_code"] }}{% else %}&mdash;{% endif %}
      </td>
    </tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% else %}
<div class="empty">
  <p>No jobs configured yet. Add a <code>[job:&lt;slug&gt;]</code> section to
     <code>config.ini</code> on this server, then point that job's Scheduled
     Task at <code>report.py</code>.</p>
</div>
{% endif %}
```

- [ ] **Step 6: Append the toggle styling to `static/components.css`**

```css
/* ---- Links | Jobs toggle -------------------------------------------- */
/* A pressed-in track with one raised, lit tab - the same slab language
   as the stat tiles, so the page still reads as one object. */
.view-toggle {
  display: inline-flex;
  gap: var(--s1);
  margin-bottom: var(--s5);
  padding: var(--s1);
  background: var(--surface-2);
  border: 1px solid var(--edge);
  border-radius: var(--r-pill);
  box-shadow: inset 0 1px 3px var(--underside);
}
.view-toggle a {
  display: inline-flex;
  align-items: center;
  gap: var(--s2);
  padding: 7px var(--s4);
  border-radius: var(--r-pill);
  font-size: 13.5px;
  font-weight: 650;
  color: var(--ink-3);
  text-decoration: none;
  transition: color .15s ease, background .15s ease;
}
.view-toggle a:hover { color: var(--ink); }
.view-toggle a.active {
  color: var(--accent-ink);
  background: var(--grad-accent);
  box-shadow: var(--e1), var(--lip);
}
.view-toggle .count {
  min-width: 20px;
  padding: 1px 7px;
  border-radius: var(--r-pill);
  background: var(--accent-soft);
  color: var(--ink-2);
  font-size: 11.5px;
  font-weight: 700;
  text-align: center;
}
.view-toggle a.active .count {
  background: rgba(255, 255, 255, 0.22);
  color: var(--accent-ink);
}
.view-toggle .count-bad { background: var(--bad-soft); color: var(--bad); }
.view-toggle a.active .count-bad {
  background: var(--bad); color: #FFFFFF;
}
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_dashboard_views.py -v`
Expected: FAIL on `test_the_jobs_view_lists_configured_jobs` and others that render `_jobs.html`, with `BuildError: Could not build url for endpoint 'job_detail'` — that route arrives in Task 7.

To keep this task independently green, add the route stub to `app.py` now, directly after the `report_job_run` route:

```python
@app.route("/jobs/<slug>")
def job_detail(slug):
    """Run history for one job. Filled in by the next task."""
    return redirect(url_for("dashboard", view="jobs"))
```

Re-run: `python -m pytest tests/test_dashboard_views.py -v`
Expected: PASS (14 tests, counting the parametrised ones)

- [ ] **Step 8: Look at it**

Run: `python app.py` and open `http://localhost:8090/?view=jobs`.
Expected: the toggle renders, Jobs shows "Waiting for first report" for `excel-bom-compare`, and clicking Links returns the original dashboard unchanged.

- [ ] **Step 9: Run the whole suite and commit**

Run: `python -m pytest -q`

```bash
git add app.py templates/dashboard.html templates/_jobs.html static/components.css tests/test_dashboard_views.py
git commit -m "Add a Jobs view beside Links on the dashboard"
```

---

### Task 7: Read a failure without logging in

**Files:**
- Modify: `app.py` (replace the `job_detail` stub from Task 6)
- Create: `templates/job_detail.html`
- Modify: `static/components.css` (append the output block)
- Test: `tests/test_job_detail.py` (create)

**Interfaces:**
- Consumes: `db.recent_job_runs` (Task 1), `jobs.derive_status` (Task 3), `app.JOBS` (Task 4).
- Produces: `GET /jobs/<slug>` rendering `job_detail.html`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_job_detail.py`:

```python
"""One job's history - the page that replaces remoting in to read a log."""
import datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
import jobs


@pytest.fixture
def client(temp_db, monkeypatch):
    monkeypatch.setattr(app_module, "JOBS", {
        "flow4": jobs.Job("flow4", "Excel BOM Compare", 86400, 3600,
                          "192.0.2.20")})
    monkeypatch.setattr(app_module, "JOB_TOKEN", "")
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def record(temp_db, when, exit_code=0, output="Status: SUCCESS"):
    stamp = when.strftime("%Y-%m-%d %H:%M:%S")
    temp_db.record_job_run("flow4", stamp, stamp, 840000, exit_code,
                           "JOBHOST01", output)


def test_shows_the_jobs_name_and_host(client, temp_db):
    record(temp_db, datetime.datetime.now())

    page = client.get("/jobs/flow4").get_data(as_text=True)

    assert "Excel BOM Compare" in page
    assert "192.0.2.20" in page


def test_shows_the_captured_output_of_the_latest_run(client, temp_db):
    record(temp_db, datetime.datetime.now(), exit_code=1,
           output="Error in Excel.py: boom")

    page = client.get("/jobs/flow4").get_data(as_text=True)

    assert "Error in Excel.py: boom" in page


def test_lists_runs_newest_first(client, temp_db):
    now = datetime.datetime.now()
    record(temp_db, now - datetime.timedelta(days=2), output="older run")
    record(temp_db, now, output="newer run")

    page = client.get("/jobs/flow4").get_data(as_text=True)

    assert page.index("newer run") < page.index("older run")


def test_a_job_that_has_never_run_still_renders(client):
    response = client.get("/jobs/flow4")

    assert response.status_code == 200
    assert "No runs reported yet" in response.get_data(as_text=True)


def test_an_unknown_job_goes_back_to_the_dashboard(client):
    response = client.get("/jobs/not-a-job")

    assert response.status_code == 302
    assert "view=jobs" in response.headers["Location"]


def test_thai_and_emoji_in_the_output_render(client, temp_db):
    record(temp_db, datetime.datetime.now(),
           output="🚀 เริ่มรัน EXCEL-FLOW")

    page = client.get("/jobs/flow4").get_data(as_text=True)

    assert "เริ่มรัน" in page
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_job_detail.py -v`
Expected: FAIL — the stub redirects, so `test_shows_the_jobs_name_and_host` gets a 302

- [ ] **Step 3: Replace the stub route in `app.py`**

```python
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
```

- [ ] **Step 4: Create `templates/job_detail.html`**

```html
{% extends "base.html" %}
{% block title %}{{ job.name }} · Link Watch{% endblock %}
{% block content %}

<div class="page-head">
  <div>
    <h1>{{ job.name }}</h1>
    <p class="page-note">
      <code>{{ job.slug }}</code>
      {%- if job.host %} &middot; runs on {{ job.host }}{% endif %}
      &middot; expected every {{ (job.expect_every // 3600) }} h
    </p>
  </div>
  <div class="actions">
    {% if status == 'ok' %}
      <span class="status status-ok"><span class="dot"></span>Passing</span>
    {% elif status == 'failed' %}
      <span class="status status-bad"><span class="dot"></span>Failed</span>
    {% elif status == 'overdue' %}
      <span class="status status-bad"><span class="dot"></span>Overdue</span>
    {% else %}
      <span class="status status-off"><span class="dot"></span>Waiting for first report</span>
    {% endif %}
    <a class="btn" href="{{ url_for('dashboard', view='jobs') }}">All jobs</a>
  </div>
</div>

{% if runs %}
<div class="table-wrap">
<table class="links-table">
  <thead>
    <tr>
      <th>Started</th>
      <th>Result</th>
      <th class="num">Took</th>
      <th>Host</th>
      <th>Reported</th>
    </tr>
  </thead>
  <tbody>
  {% for run in runs %}
    <tr>
      <td data-label="Started">
        {{ run["started_at"] }}
        <div class="dim">{{ run["started_at"] | ago }}</div>
      </td>
      <td data-label="Result">
        {% if run["ok"] %}
          <span class="status status-ok"><span class="dot"></span>Passed</span>
        {% else %}
          <span class="status status-bad"><span class="dot"></span>Exit {{ run["exit_code"] }}</span>
        {% endif %}
      </td>
      <td class="num" data-label="Took">{{ run["duration_ms"] | duration }}</td>
      <td class="dim" data-label="Host">{% if run["host"] %}{{ run["host"] }}{% else %}&mdash;{% endif %}</td>
      <td class="dim" data-label="Reported">{{ run["received_at"][5:16] }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
</div>

{# The latest run's output, which is the reason not to remote in. #}
<h2 class="section-head">What it printed &middot; {{ runs[0]["started_at"] }}</h2>
<pre class="job-output">{{ runs[0]["output_tail"] or "(the job printed nothing)" }}</pre>

{% else %}
<div class="empty">
  <p>No runs reported yet. Point this job's Scheduled Task at
     <code>report.py</code> and it will appear here after its next run.</p>
</div>
{% endif %}

{% endblock %}
```

- [ ] **Step 5: Append the output styling to `static/components.css`**

```css
/* ---- a job's captured output ---------------------------------------- */
/* Recessed, monospaced, scrollable: a window onto the server's console,
   so nobody has to open one themselves. */
.section-head {
  margin: var(--s7) 0 var(--s3);
  font-size: 15px;
  font-weight: 650;
  color: var(--ink-2);
}
.job-output {
  max-height: 460px;
  overflow: auto;
  margin: 0;
  padding: var(--s4) var(--s5);
  background: var(--bar-grad);
  color: var(--bar-ink);
  border: 1px solid var(--edge-strong);
  border-radius: var(--r-md);
  box-shadow: inset 0 2px 6px rgba(0, 0, 0, .35);
  font-family: var(--mono);
  font-size: 12.5px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_job_detail.py -v`
Expected: PASS (6 tests)

- [ ] **Step 7: Prove the whole path end to end by hand**

Terminal 1:

```bash
python app.py
```

Terminal 2 — run a fake job through the real wrapper against the real dashboard:

```bash
python reporter/report.py excel-bom-compare --url http://127.0.0.1:8090 -- python -c "print('pretend flow4 ran'); raise SystemExit(0)"
```

Expected: the command exits 0, and `http://localhost:8090/?view=jobs` shows **Passing**. Click the job name: the detail page shows `pretend flow4 ran`.

Then prove a failure is visible:

```bash
python reporter/report.py excel-bom-compare --url http://127.0.0.1:8090 -- python -c "print('something broke'); raise SystemExit(2)"
```

Expected: the command exits 2, the dashboard shows **Failed**, and the detail page shows `Exit 2` and `something broke`.

- [ ] **Step 8: Run the whole suite and commit**

Run: `python -m pytest -q`
Expected: every test passes, including the original link tests

```bash
git add app.py templates/job_detail.html static/components.css tests/test_job_detail.py
git commit -m "Show one job history and what it printed"
```

---

## Deployment (after the plan is implemented)

These steps run on the servers, not in this repo. They are listed here so the
work is not considered finished at the last commit.

1. Copy the project to `D:\All Project for PCK\DashBoard Check link` on **192.0.2.10** and restart `python app.py`. The Jobs tab shows `excel-bom-compare` as *Waiting for first report*.
2. Copy `reporter\report.py` to **192.0.2.20**, for example to `D:\Filepackage\python\report.py`.
3. Fix the exit code of `D:\Filepackage\python\EXCEL_BOM_COMPARE\FLOW4.py` on .209. It catches `CalledProcessError`, counts the failure and still exits 0, so the wrapper would report SUCCESS with scripts failing. Add `import sys` at the top if absent, and at the end of the file:

   ```python
   sys.exit(1 if fail else 0)
   ```

4. Create the Scheduled Task on .209 with this action:

   ```
   Program:   C:\Users\<user>\AppData\Local\Programs\Python\Python313\python.exe
   Arguments: D:\Filepackage\python\report.py excel-bom-compare
              --url http://192.0.2.10:8090
              -- D:\Filepackage\python\dist\FLOW4.exe
   Start in:  D:\Filepackage\python\EXCEL_BOM_COMPARE
   ```

   Set it to **"Run only when user is logged on"** under `<user>`. `refresh_Excel.py` drives Excel through `win32com`, and "run whether user is logged on or not" executes in session 0 where Excel COM fails.

   **"Start in" is mandatory, not optional.** The wrapper writes
   `report_errors.log` to its working directory. A Scheduled Task with a blank
   "Start in" runs with a working directory of `C:\Windows\System32`, where a
   service account cannot write — the wrapper swallows that failure by design and
   falls back to stderr, which Task Scheduler discards. A failed report would then
   leave no trace anywhere, and step 5 below would have nothing to find.

5. Run the task by hand once and confirm the report lands on the dashboard. If it does not, look for `report_errors.log` in the task's *Start in* folder.
6. For each remaining scheduled project: add a `[job:<slug>]` section to `config.ini` on .92, repeat steps 2 and 4 on that project's host, and confirm its exit code is honest.

## Verification checklist

- [ ] `python -m pytest -q` passes, including the pre-existing link tests
- [ ] `app.py` is under 500 lines; if not, move the job routes to a blueprint
- [ ] `reporter/report.py` imports nothing outside the standard library
- [ ] Nothing added in this plan makes an outbound request from the dashboard to a job host
- [ ] The Links view renders exactly as it did before the change
