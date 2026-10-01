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
