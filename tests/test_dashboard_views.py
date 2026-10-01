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
