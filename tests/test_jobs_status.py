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


def test_a_run_with_an_unparseable_started_at_reads_as_never():
    """Ingest validates started_at, so this can only reach derive_status
    through a row that did not come through ingest - a hand-edited or
    restored database, say. That should not be able to crash the whole
    jobs view, let alone the links view that happens to share a page
    with it."""
    bad_run = {"started_at": "not-a-timestamp", "ok": 1}

    assert jobs.derive_status(DAILY, bad_run, NOW) == jobs.STATUS_NEVER


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


def test_exactly_at_the_grace_boundary_is_not_yet_overdue():
    """Pins `>` rather than `>=`: a job is late only once it is PAST its grace."""
    assert jobs.derive_status(DAILY, run(25), NOW) == jobs.STATUS_OK


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
