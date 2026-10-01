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
