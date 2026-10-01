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
