"""The per-link detail pop-up on the dashboard and the history behind it."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module


@pytest.fixture
def client(temp_db, monkeypatch):
    monkeypatch.setattr(app_module, "JOBS", {})
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def add_link(db, name="Forming Box", slug="forming-box"):
    with db.get_conn() as conn:
        return conn.execute(
            "INSERT INTO links (name, slug, url) VALUES (?, ?, ?)",
            (name, slug, "http://192.0.2.10:5174/")).lastrowid


def add_check(db, link_id, when, ok, code=None, ms=None, error=None):
    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO checks (link_id, checked_at, ok, status_code,
                                   response_ms, error)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (link_id, when, ok, code, ms, error))


def test_rows_carry_recent_checks_newest_first(temp_db):
    lid = add_link(temp_db)
    add_check(temp_db, lid, "2026-11-06 09:00:00", 1, 200, 12)
    add_check(temp_db, lid, "2026-11-06 09:30:00", 0, error="ConnectionError")

    row = app_module.load_dashboard_rows()[0]

    assert [c["checked_at"] for c in row["history"]] == [
        "2026-11-06 09:30:00", "2026-11-06 09:00:00"]
    # the latest check is still the newest one
    assert row["check"]["checked_at"] == "2026-11-06 09:30:00"


def test_history_is_capped(temp_db):
    lid = add_link(temp_db)
    for i in range(app_module.HISTORY_CHECKS + 5):
        add_check(temp_db, lid, f"2026-11-06 {i // 60:02d}:{i % 60:02d}:00", 1, 200, 5)

    row = app_module.load_dashboard_rows()[0]

    assert len(row["history"]) == app_module.HISTORY_CHECKS


def test_uptime_is_the_share_of_ok_checks(temp_db):
    lid = add_link(temp_db)
    for i, ok in enumerate([1, 1, 1, 0]):
        add_check(temp_db, lid, f"2026-11-06 10:0{i}:00", ok, 200 if ok else None, 5)

    row = app_module.load_dashboard_rows()[0]

    assert row["uptime"] == 75


def test_uptime_is_none_before_the_first_check(temp_db):
    add_link(temp_db)

    row = app_module.load_dashboard_rows()[0]

    assert row["uptime"] is None and row["history"] == []


def test_each_link_gets_a_detail_dialog(client, temp_db):
    lid = add_link(temp_db)
    add_check(temp_db, lid, "2026-11-06 09:30:00", 0, error="ConnectionError")

    page = client.get("/").get_data(as_text=True)

    assert '<dialog class="detail is-down"' in page          # lit red, like its tile
    assert f'id="detail-{lid}"' in page
    assert f'data-detail="detail-{lid}"' in page          # the tile opens it
    assert "/go/forming-box" in page
    assert "ConnectionError" in page
    assert f"/links/{lid}/edit" in page


def test_the_dashboard_no_longer_reloads_with_a_meta_tag(client, temp_db):
    """A meta refresh would close an open pop-up mid-read; the page's
    script reloads instead, and waits while a pop-up is open."""
    add_link(temp_db)

    page = client.get("/").get_data(as_text=True)

    assert 'http-equiv="refresh"' not in page
