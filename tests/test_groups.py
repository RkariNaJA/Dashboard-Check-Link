"""Grouping the dashboard's app tiles by team."""
import os
import re
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
import db
import groups


def row(name, group="", down=False):
    """Just enough of a dashboard row for group_rows."""
    return {"link": {"name": name, "group_name": group, "enabled": 1},
            "check": {"ok": 0 if down else 1}}


def names(result):
    return [(g["name"], [r["link"]["name"] for r in g["rows"]])
            for g in result]


# --------------------------------------------------------------- group_rows

def test_groups_follow_the_configured_order():
    rows = [row("PO", "Purchase Team"), row("Forming", "Mer Team"),
            row("Mine", "My OWN")]

    result = groups.group_rows(rows, ["Mer Team", "Purchase Team",
                                      "ETC", "My OWN"])

    assert names(result) == [("Mer Team", ["Forming"]),
                             ("Purchase Team", ["PO"]),
                             ("My OWN", ["Mine"])]


def test_unlisted_groups_come_after_listed_ones_alphabetically():
    rows = [row("Z", "Zeta"), row("A", "Alpha"), row("M", "Mer Team")]

    result = groups.group_rows(rows, ["Mer Team"])

    assert [g["name"] for g in result] == ["Mer Team", "Alpha", "Zeta"]


def test_links_without_a_group_land_in_other_at_the_end():
    rows = [row("Loose"), row("Forming", "Mer Team")]

    result = groups.group_rows(rows, [])

    assert names(result) == [("Mer Team", ["Forming"]),
                             ("Other", ["Loose"])]


def test_group_names_match_ignoring_case_and_spaces():
    rows = [row("A", "mer team"), row("B", "  Mer Team ")]

    result = groups.group_rows(rows, ["Mer Team"])

    assert names(result) == [("Mer Team", ["A", "B"])]


def test_down_apps_come_first_inside_a_group_and_are_counted():
    rows = [row("Up", "Mer Team"), row("Broken", "Mer Team", down=True)]

    [group] = groups.group_rows(rows, [])

    assert [r["link"]["name"] for r in group["rows"]] == ["Broken", "Up"]
    assert group["down"] == 1


# ----------------------------------------------------------------- storage

def test_init_db_adds_the_group_column_to_an_old_database(tmp_path,
                                                          monkeypatch):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE links (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE, url TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))""")
        conn.execute("INSERT INTO links (name, slug, url) "
                     "VALUES ('Old', 'old', 'http://x/')")
    monkeypatch.setattr(db, "DB_PATH", str(path))

    db.init_db()
    db.init_db()   # running it again on every start must be harmless

    with db.get_conn() as conn:
        link = conn.execute("SELECT * FROM links").fetchone()
    assert link["group_name"] == ""


@pytest.fixture
def client(temp_db, monkeypatch):
    monkeypatch.setattr(app_module, "check_one_link", lambda *a: True)
    monkeypatch.setattr(app_module, "GROUP_ORDER", ["Mer Team", "ETC"])
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def stored_group(temp_db, slug):
    with temp_db.get_conn() as conn:
        return conn.execute("SELECT group_name FROM links WHERE slug = ?",
                            (slug,)).fetchone()[0]


def test_adding_a_link_saves_its_group(client, temp_db):
    client.post("/links/add", data={"name": "Forming Box",
                                    "url": "http://x/", "group": " Mer Team "})

    assert stored_group(temp_db, "forming-box") == "Mer Team"


def test_editing_a_link_changes_its_group(client, temp_db):
    client.post("/links/add", data={"name": "BOM", "url": "http://x/"})
    with temp_db.get_conn() as conn:
        link_id = conn.execute("SELECT id FROM links").fetchone()[0]

    client.post(f"/links/{link_id}/edit",
                data={"name": "BOM", "url": "http://x/", "slug": "bom",
                      "group": "ETC"})

    assert stored_group(temp_db, "bom") == "ETC"


def two_groups(client):
    client.post("/links/add", data={"name": "BOM Query", "url": "http://x/",
                                    "group": "ETC"})
    client.post("/links/add", data={"name": "Forming Box",
                                    "url": "http://x/", "group": "Mer Team"})


def tile_names(page):
    """Names on the app tiles - not the hidden detail pop-ups."""
    return re.findall(r'class="app-open">([^<]+)<', page)


def test_the_landing_page_shows_only_the_group_boxes_in_order(client):
    two_groups(client)

    page = client.get("/").get_data(as_text=True)

    assert page.index(">Mer Team<") < page.index(">ETC<")
    assert tile_names(page) == []


def test_opening_a_group_shows_only_that_groups_apps(client):
    two_groups(client)

    page = client.get("/?group=mer team").get_data(as_text=True)

    assert tile_names(page) == ["Forming Box"]


def test_an_unknown_group_shows_the_landing_page(client):
    two_groups(client)

    response = client.get("/?group=Nope")

    assert response.status_code == 200
    assert tile_names(response.get_data(as_text=True)) == []


def test_start_goes_back_to_the_group_it_was_pressed_in(client, temp_db):
    two_groups(client)
    with temp_db.get_conn() as conn:
        link_id = conn.execute("SELECT id FROM links").fetchone()[0]

    response = client.post(f"/links/{link_id}/start",
                           data={"group": "Mer Team"})

    assert response.headers["Location"].endswith("/?group=Mer+Team")


def test_a_group_box_counts_its_online_apps():
    rows = [row("A", "Mer Team"), row("B", "Mer Team", down=True)]

    [group] = groups.group_rows(rows, [])

    assert (group["online"], group["down"]) == (1, 1)


def test_find_matches_a_group_ignoring_case():
    result = groups.group_rows([row("A", "Mer Team")], [])

    assert groups.find(result, " mer TEAM")["name"] == "Mer Team"
    assert groups.find(result, "Nope") is None
    assert groups.find(result, None) is None


def test_the_add_form_suggests_groups_already_in_use(client):
    client.post("/links/add", data={"name": "BOM Query", "url": "http://x/",
                                    "group": "ETC"})

    page = client.get("/manage").get_data(as_text=True)

    assert '<option value="ETC">' in page


def test_a_group_typed_in_other_case_is_saved_as_the_known_name(client,
                                                                temp_db):
    client.post("/links/add", data={"name": "Forming Box",
                                    "url": "http://x/", "group": "mer  TEAM"})

    assert stored_group(temp_db, "forming-box") == "Mer Team"
