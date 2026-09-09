"""The Start button: when it refuses, and what it launches."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
import runner


def link_row(temp_db, link_id):
    with temp_db.get_conn() as conn:
        return conn.execute("SELECT * FROM links WHERE id = ?",
                            (link_id,)).fetchone()


def echo_process(tmp_path, text="started-by-test"):
    return {"demo": [runner.Proc("demo", "demo", f"echo {text}",
                                 str(tmp_path))]}


# ----------------------------------------------------------- why_not_start

def test_refuses_a_link_with_no_start_command_configured(temp_db, a_link,
                                                         dead_url):
    link = link_row(temp_db, a_link(dead_url))

    reason = app_module.why_not_start(link, {})

    assert "no start command" in reason.lower()


def test_refuses_a_link_that_is_already_answering(temp_db, a_link,
                                                  live_server, tmp_path):
    link = link_row(temp_db, a_link(live_server))

    reason = app_module.why_not_start(link, echo_process(tmp_path))

    assert "already online" in reason.lower()


def test_allows_a_link_that_is_not_answering(temp_db, a_link, dead_url,
                                             tmp_path):
    link = link_row(temp_db, a_link(dead_url))

    assert app_module.why_not_start(link, echo_process(tmp_path)) is None


def test_allows_a_down_link_whose_last_recorded_check_says_online(
        temp_db, a_link, dead_url, tmp_path):
    """After a reboot the newest stored check is stale and says 'online'.

    Trusting it would refuse to start the very apps this button exists
    for, so the decision must come from a live check instead.
    """
    link_id = a_link(dead_url)
    with temp_db.get_conn() as conn:
        conn.execute(
            """INSERT INTO checks (link_id, checked_at, ok, status_code)
               VALUES (?, datetime('now', 'localtime'), 1, 200)""",
            (link_id,))
    link = link_row(temp_db, link_id)

    assert app_module.why_not_start(link, echo_process(tmp_path)) is None


# ------------------------------------------------------------------ routes

def test_start_route_launches_the_app_behind_a_down_link(
        temp_db, a_link, dead_url, tmp_path, monkeypatch, log_containing):
    link_id = a_link(dead_url)
    monkeypatch.setattr(app_module, "PROCESSES", echo_process(tmp_path))
    monkeypatch.setattr(app_module, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(app_module, "START_GRACE_SECONDS", 0)

    response = app_module.app.test_client().post(
        f"/links/{link_id}/start", follow_redirects=True)

    assert response.status_code == 200
    assert "started-by-test" in log_containing(
        tmp_path / "logs" / "demo.log", "started-by-test")


def test_start_route_survives_an_unknown_link_id(temp_db, tmp_path,
                                                 monkeypatch):
    monkeypatch.setattr(app_module, "PROCESSES", echo_process(tmp_path))

    response = app_module.app.test_client().post(
        "/links/9999/start", follow_redirects=True)

    assert response.status_code == 200


# --------------------------------------------------------- what the page shows

def test_dashboard_offers_a_start_button_for_a_configured_down_link(
        temp_db, a_link, dead_url, tmp_path, monkeypatch):
    link_id = a_link(dead_url)
    monkeypatch.setattr(app_module, "PROCESSES", echo_process(tmp_path))

    html = app_module.app.test_client().get("/").get_data(as_text=True)

    assert f"/links/{link_id}/start" in html


def test_dashboard_offers_no_start_button_for_an_unconfigured_link(
        temp_db, a_link, dead_url, monkeypatch):
    link_id = a_link(dead_url)
    monkeypatch.setattr(app_module, "PROCESSES", {})

    html = app_module.app.test_client().get("/").get_data(as_text=True)

    assert f"/links/{link_id}/start" not in html


def test_dashboard_disables_the_start_button_while_the_link_is_online(
        temp_db, a_link, live_server, tmp_path, monkeypatch):
    link_id = a_link(live_server)
    monkeypatch.setattr(app_module, "PROCESSES", echo_process(tmp_path))
    from collector import check_one_link
    check_one_link(link_id, live_server, 5, False)   # record it as online

    html = app_module.app.test_client().get("/").get_data(as_text=True)

    assert "disabled" in html
    assert f"/links/{link_id}/start" not in html


# ------------------------------------------------------- start all the down

def test_start_all_starts_a_configured_link_that_is_down(
        temp_db, a_link, dead_url, tmp_path, monkeypatch, log_containing):
    a_link(dead_url)
    monkeypatch.setattr(app_module, "PROCESSES", echo_process(tmp_path))
    monkeypatch.setattr(app_module, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(app_module, "START_GRACE_SECONDS", 0)

    app_module.app.test_client().post("/services/start-all",
                                      follow_redirects=True)

    assert "started-by-test" in log_containing(
        tmp_path / "logs" / "demo.log", "started-by-test")


def test_start_all_leaves_an_already_online_link_alone(
        temp_db, a_link, live_server, tmp_path, monkeypatch):
    """The guard that stops a second copy fighting for the same port."""
    a_link(live_server)
    monkeypatch.setattr(app_module, "PROCESSES", echo_process(tmp_path))
    monkeypatch.setattr(app_module, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(app_module, "START_GRACE_SECONDS", 0)

    app_module.app.test_client().post("/services/start-all",
                                      follow_redirects=True)

    assert not (tmp_path / "logs" / "demo.log").exists()


def test_dashboard_hides_start_all_when_nothing_is_configured(
        temp_db, a_link, dead_url, monkeypatch):
    """Offering an action that provably cannot do anything is worse than
    offering nothing - it hides the real problem (an empty config)."""
    a_link(dead_url)
    monkeypatch.setattr(app_module, "PROCESSES", {})

    html = app_module.app.test_client().get("/").get_data(as_text=True)

    assert "start-all" not in html


def test_dashboard_shows_start_all_when_something_is_configured(
        temp_db, a_link, dead_url, tmp_path, monkeypatch):
    a_link(dead_url)
    monkeypatch.setattr(app_module, "PROCESSES", echo_process(tmp_path))

    html = app_module.app.test_client().get("/").get_data(as_text=True)

    assert "start-all" in html
