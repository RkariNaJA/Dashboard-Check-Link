# Scheduled Job Monitoring — Design

**Date:** 2026-10-01
**Status:** Approved for planning
**Affects:** `DashBoard Check link` (deployed on 192.0.2.10:8090)

## Problem

The dashboard models everything as a **link**: a URL, a `/go/<slug>` short link
that counts clicks, and an HTTP health check every 30 minutes. A `[process:*]`
section in `config.ini` adds a Start button that launches the app with
`subprocess.Popen` on the dashboard's own machine.

That model cannot express a scheduled batch job:

| Dashboard assumes | A scheduled job |
|---|---|
| Has a URL (`links.url` is `NOT NULL`) | Has no endpoint |
| "online / offline" | "last run passed at 06:00, took 14 min" |
| `why_not_start` refuses when the URL answers | Nothing to poll |
| Launches detached, discards exit code | The exit code **is** the result |
| Start command runs locally | Jobs live on other servers entirely |

Today the only way to know whether a nightly job ran is to RDP into the server
that runs it and read a log file. A task that silently stops being scheduled is
invisible until someone notices the missing output days later.

## Goals

1. Show, on the existing dashboard, whether each scheduled job **ran** and
   whether it **passed**.
2. Detect a job that **stopped running at all** — the failure that is invisible
   today.
3. Adding a new scheduled project is a config block plus a one-line change to
   its Scheduled Task. No code.
4. **The dashboard initiates no outbound connection.** It does not need network
   or share access to any machine that runs a job.
5. Failures are diagnosable in the browser — no RDP to read a log.

## Non-goals (explicitly deferred)

- **Start buttons for remote jobs.** Running a job on demand requires an agent
  on the remote host and a route from 192.0.2.x to that host. Dropped from
  this design; it is additive later and changes nothing here.
- **Pull mode** (dashboard reads a job's log file over SMB). Push covers every
  known job and needs no reachability. A per-job `status_file` fallback can be
  added later against the same tables and UI.
- **Adding jobs through the web UI.** Jobs are declared in `config.ini`, the
  same as `[process:*]` today.
- **Alerting** (email, LINE). The dashboard shows state; nothing pushes it.

## Architecture

Direction is inverted from the link health check: **jobs report to the
dashboard** rather than the dashboard polling jobs.

```
  192.0.2.20                              192.0.2.10
  ┌───────────────────────────┐               ┌──────────────────────┐
  │ Task Scheduler            │               │ Dashboard (Flask)    │
  │   └─ report.py <slug> --  │  HTTP POST    │                      │
  │        FLOW4.exe          │ ────────────► │ /jobs/<slug>/report  │
  │      (runs the real job,  │   outbound    │        │             │
  │       captures result)    │     only      │        ▼             │
  └───────────────────────────┘               │  job_runs (SQLite)   │
                                              │        │             │
  jobhost01, fs01, fs02, .186 …             │        ▼             │
  (same wrapper, same POST)   ────────────►   │  /?view=jobs         │
                                              └──────────────────────┘
```

Every arrow points at .92. No server needs to be reachable *from* .92.

### Why push

The `.env` on .209 shows scheduled work spread across at least five hosts
(`fileserver01`, `fileserver02`, `jobhost01`, `192.0.2.30`,
`192.0.2.20`), on a different subnet from the dashboard. Pulling status
would require the dashboard to reach all of them.

Push also produces **uniform data**. The flows were written at different times
and log differently — `FLOW4.py` writes `Status: SUCCESS` to a text file, FLOW2
writes `ssis_run_log.txt` in its own format. Parsing each one means a parser per
flow, growing forever. A wrapper reports identical fields for every job, Python
or not.

**Absence is the signal.** A job that never runs sends nothing, and the
dashboard marks it overdue. Detecting a stopped schedule requires no
cooperation from the job at all.

## Component 1: `report.py` (the wrapper)

A single file, copied to each host that runs jobs. **Standard library only** —
`urllib.request`, not `requests` — so no `pip install` on five servers.

```
python report.py <slug> --url http://192.0.2.10:8090 [--token T] -- <command…>
```

The Scheduled Task's action changes from:

```
D:\Filepackage\python\dist\FLOW4.exe
```

to:

```
python D:\Filepackage\python\report.py excel-bom-compare
       --url http://192.0.2.10:8090 -- D:\Filepackage\python\dist\FLOW4.exe
```

Behaviour:

1. Record `started_at`.
2. Run the command with `subprocess.run`, stdout and stderr combined and
   captured. Child env gets `PYTHONIOENCODING=utf-8` — the flows print Thai and
   emoji, which raise `UnicodeEncodeError` on a cp874 console.
3. Record `finished_at`, `duration_ms`, `exit_code`.
4. POST JSON to `<url>/jobs/<slug>/report`.
5. **Exit with the child's exit code**, so Task Scheduler's own Last Result
   stays truthful.

Payload:

```json
{
  "started_at":  "2026-10-01 06:00:03",
  "finished_at": "2026-10-01 06:14:22",
  "duration_ms": 859000,
  "exit_code":   0,
  "host":        "JOBHOST01",
  "output_tail": "…last 4000 characters, UTF-8, errors=replace…"
}
```

### Reporting must never break the job

If the POST fails — dashboard restarting, network blip — the wrapper retries 3
times with short backoff, writes a line to a local `report_errors.log`, and
**still exits with the child's code**. A monitoring failure must never turn a
successful job into a failed one.

The cost of this choice: a dropped report shows as a false "overdue". That is
accepted — the dashboard being unreachable is itself worth noticing, and a
disk-backed retry spool is complexity this does not yet need.

## Component 2: dashboard changes

### Schema (`db.py`)

Jobs are **declared in `config.ini`**, consistent with `[process:*]`. Only runs
are stored, keyed by slug — so there is no registry to keep in sync.

```sql
CREATE TABLE IF NOT EXISTS job_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    slug         TEXT NOT NULL,
    started_at   TEXT NOT NULL,          -- local time, from the job's host
    finished_at  TEXT NOT NULL,
    duration_ms  INTEGER NOT NULL,
    exit_code    INTEGER,
    ok           INTEGER NOT NULL,       -- exit_code == 0
    host         TEXT NOT NULL DEFAULT '',
    output_tail  TEXT NOT NULL DEFAULT '',
    received_at  TEXT NOT NULL           -- dashboard's own clock
);
CREATE INDEX IF NOT EXISTS idx_job_runs_slug_time ON job_runs(slug, started_at);
```

Both clocks are stored deliberately: `started_at` is what the job believes,
`received_at` is what the dashboard observed. A host with a wrong clock shows up
as a gap between them instead of as a mystery.

Retention: 90 days, purged by the existing health-check scheduler job rather
than a new one.

### Config (`config.ini`)

```ini
[jobs]
; Shared secret the wrappers send as X-Job-Token. Blank = no check.
token = change-me

[job:excel-bom-compare]
name         = Excel BOM Compare (FLOW4)
expect_every = 24h          ; 30m / 6h / 24h / 7d
grace        = 2h           ; slack before calling it overdue
host         = 192.0.2.20   ; display only
```

Adding a project = one block here + pointing its Scheduled Task at the wrapper.

### Status derivation (`jobs.py`)

Computed at render time from config plus the latest run. No background polling
is added — the only new scheduled work is the retention purge.

| Status | Condition | Colour |
|---|---|---|
| `never` | no runs recorded | grey — "waiting for first report" |
| `overdue` | `now - latest.started_at > expect_every + grace` | red |
| `failed` | latest run `ok = 0` | red |
| `ok` | latest run `ok = 1`, not overdue | green |

Precedence is **never → overdue → failed → ok**. Overdue outranks failed: a job
that failed last night and has not run since has two problems, and the one you
cannot see from the log is that it stopped.

### Routes (`app.py`)

| Route | Purpose |
|---|---|
| `POST /jobs/<slug>/report` | Ingest. 404 unknown slug, 401 bad token, 400 malformed. |
| `GET /?view=jobs` | Dashboard in Jobs mode (`view=links` is the default). |
| `GET /jobs/<slug>` | Run history and captured output for one job. |

An unknown slug returns **404 rather than silently accepting**, so a typo in a
Scheduled Task surfaces at the wrapper instead of becoming a job that is
permanently "waiting for first report".

`RESERVED_SLUGS` (`app.py:36`) gains `"jobs"`, or a link could be given a slug
that shadows the route.

### UI

A segmented toggle sits above the summary strip on the existing dashboard page:

```
┌──────────────┬──────────────┐
│  Links (12)  │   Jobs (4)   │     → /?view=links   /?view=jobs
└──────────────┴──────────────┘
```

Same page, same summary strip, same CSS tokens. The strip's counters change
meaning per view:

- **Links:** total / online / offline / clicks today
- **Jobs:** total / ok / failed / overdue / runs in last 24 h

A job row shows name, host, status pill, last run as relative time ("2 h ago"),
duration, next expected, and exit code. The row links to `/jobs/<slug>`, which
shows the last 20 runs and the captured output — this is what replaces RDP'ing
in to read a log.

### Keeping files small

`app.py` is 344 lines and the project limit is 500. Job status logic,
`expect_every` parsing and config loading go in a new `jobs.py`; `app.py` gains
only thin routes. If it still crosses 500, the job routes move to a Flask
blueprint.

## Prerequisite outside this repo

`FLOW4.py` on .209 catches `subprocess.CalledProcessError`, counts the failure,
and never re-raises (`FLOW4.py:56`). It exits 0 even when 3 of 6 scripts failed,
so the wrapper would report SUCCESS. One line at the end of the file fixes it:

```python
sys.exit(1 if fail else 0)
```

Every job wrapped by `report.py` must exit non-zero on failure, or its status is
meaningless. This is worth checking for each project as it is onboarded.

## Error handling

| Case | Behaviour |
|---|---|
| Dashboard unreachable when job finishes | 3 retries, log locally, exit with child's code. Job shows overdue. |
| Unknown slug posted | 404; wrapper logs it; nothing stored. |
| Bad or missing token, when `[jobs] token` is set | 401; nothing stored. If the setting is blank, no token is checked and the header is ignored. |
| Malformed JSON / missing fields | 400; nothing stored. |
| Job killed by Task Scheduler timeout | Wrapper dies with it; no report; shows overdue. |
| Non-UTF-8 bytes in job output | Decoded with `errors="replace"`; never crashes ingest. |
| Two reports for the same run | Both stored; latest by `started_at` wins for status. |

## Testing

Mirrors the existing pytest layout and fixtures in `tests/conftest.py`.

**`tests/test_jobs.py`**
- `expect_every` parsing: `30m`, `6h`, `24h`, `7d`, and rejection of garbage
- status precedence: never / overdue / failed / ok, including failed-and-overdue
- a job exactly at the grace boundary is not yet overdue
- retention purge deletes runs older than 90 days and keeps newer ones

**`tests/test_report_endpoint.py`**
- a valid POST stores a run and the dashboard then shows it as `ok`
- non-zero `exit_code` stores `ok = 0`
- unknown slug → 404, bad token → 401, malformed body → 400
- Thai text and emoji in `output_tail` round-trip intact

**`tests/test_reporter.py`**
- wrapper runs a child and captures its exit code and output
- wrapper POSTs to a local collecting server (extends the `live_server` fixture
  pattern to accept POST)
- **wrapper exits with the child's code even when the POST fails** — the single
  most important test in this design

## Rollout

1. Build and deploy the dashboard changes to
   `D:\All Project for PCK\DashBoard Check link` on .92; restart it.
2. Add `[job:excel-bom-compare]` to `config.ini`. It shows as "waiting for first
   report".
3. Copy `report.py` to .209.
4. Apply the `sys.exit` fix to `FLOW4.py`.
5. Create the Scheduled Task on .209 pointing at the wrapper. Run it once by
   hand and confirm the report lands.
6. Repeat 3–5 for each remaining scheduled project, one at a time.

Each project is independent. A wrapper that is wrong on one host affects only
that job's row.

## Open question for step 5

`refresh_Excel.py` drives Excel through `win32com`, and `.env` sets
`DESKTOP_PATH=C:\Users\<user>\Desktop` and a user-profile `PYTHON_PATH`.
That job needs a real desktop session — a task configured "run whether user is
logged on or not" executes in session 0, where Excel COM fails. The wrapper does
not change this either way, but the Scheduled Task for FLOW4 must be created as
"run only when user is logged on" under that account. Worth confirming on the
box before step 5.
