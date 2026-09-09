"""Tests for runner.py - launching the apps behind the tracked links."""
import configparser
import os
import time
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import runner


def cfg(text):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(text)
    return parser


# --------------------------------------------------------- load_processes

def test_reads_a_process_section_keyed_by_its_link_slug():
    processes = runner.load_processes(cfg(r"""
[process:my-app]
link    = my-app
command = npm run dev
cwd     = D:\Projects\barcode
"""))

    assert list(processes) == ["my-app"]
    proc = processes["my-app"][0]
    assert proc.name == "my-app"
    assert proc.command == "npm run dev"
    assert proc.cwd == r"D:\Projects\barcode"


def test_groups_two_processes_under_one_link_in_config_order():
    processes = runner.load_processes(cfg(r"""
[process:my-dash-backend]
link    = my-dashboard
command = py .\serve.py
cwd     = D:\Projects\pps

[process:my-dash-frontend]
link    = my-dashboard
command = py -m http.server 8080 --directory dist
cwd     = D:\Projects\pps\frontend
"""))

    names = [p.name for p in processes["my-dashboard"]]
    assert names == ["my-dash-backend", "my-dash-frontend"]


def test_ignores_sections_that_are_not_processes():
    processes = runner.load_processes(cfg(r"""
[server]
port = 8090

[health]
timeout_seconds = 10
"""))

    assert processes == {}


def test_skips_an_incomplete_process_section_instead_of_crashing(capsys):
    processes = runner.load_processes(cfg(r"""
[process:typo]
command = npm run dev

[process:good]
link    = other-app
command = npm run dev
cwd     = D:\Projects\forming
"""))

    assert list(processes) == ["other-app"]
    assert "typo" in capsys.readouterr().out


# ------------------------------------------------------------------ launch

def log_containing(log_path, wanted, timeout=15):
    """Detached children finish on their own clock - wait for the output.

    Polls for `wanted` specifically: the start marker lands immediately,
    so "file is non-empty" would return before the child wrote anything.
    """
    deadline = time.time() + timeout
    text = ""
    while time.time() < deadline:
        if log_path.exists():
            text = log_path.read_text(encoding="utf-8", errors="replace")
            if wanted.lower() in text.lower():
                return text
        time.sleep(0.1)
    return text


def test_launch_runs_the_command_and_captures_its_output(tmp_path):
    logs = tmp_path / "logs"
    proc = runner.Proc("demo", "demo-link", "echo link-watch-test",
                       str(tmp_path))

    runner.launch(proc, str(logs))

    assert "link-watch-test" in log_containing(logs / "demo.log",
                                               "link-watch-test")


def test_launch_runs_the_command_in_the_configured_folder(tmp_path):
    logs = tmp_path / "logs"
    workdir = tmp_path / "the-app"
    workdir.mkdir()
    proc = runner.Proc("demo", "demo-link", "cd", str(workdir))

    runner.launch(proc, str(logs))

    assert "the-app" in log_containing(logs / "demo.log", "the-app")


def test_launch_marks_each_start_so_repeated_starts_stay_readable(tmp_path):
    logs = tmp_path / "logs"
    proc = runner.Proc("demo", "demo-link", "echo once", str(tmp_path))

    runner.launch(proc, str(logs))

    assert "started" in log_containing(logs / "demo.log", "started").lower()


def test_launch_reports_a_readable_error_when_the_folder_is_missing(tmp_path):
    proc = runner.Proc("demo", "demo-link", "echo hi",
                       str(tmp_path / "does-not-exist"))

    with pytest.raises(runner.LaunchError) as excinfo:
        runner.launch(proc, str(tmp_path / "logs"))

    assert "does-not-exist" in str(excinfo.value)


# -------------------------------------------------------------- start_link

def test_start_link_launches_every_process_for_that_link_in_order(tmp_path):
    logs = tmp_path / "logs"
    procs = {"my-dashboard": [
        runner.Proc("back", "my-dashboard", "echo backend-up", str(tmp_path)),
        runner.Proc("front", "my-dashboard", "echo frontend-up", str(tmp_path)),
    ]}

    results = runner.start_link("my-dashboard", procs, str(logs))

    assert [(r.name, r.ok) for r in results] == [("back", True),
                                                 ("front", True)]


def test_start_link_keeps_going_when_one_process_cannot_start(tmp_path):
    logs = tmp_path / "logs"
    procs = {"my-dashboard": [
        runner.Proc("broken", "my-dashboard", "echo nope", str(tmp_path / "gone")),
        runner.Proc("front", "my-dashboard", "echo frontend-up", str(tmp_path)),
    ]}

    results = runner.start_link("my-dashboard", procs, str(logs))

    assert [(r.name, r.ok) for r in results] == [("broken", False),
                                                 ("front", True)]
    assert "frontend-up" in log_containing(logs / "front.log", "frontend-up")


def test_start_link_returns_nothing_for_a_link_with_no_processes(tmp_path):
    assert runner.start_link("unknown", {}, str(tmp_path / "logs")) == []


# --------------------------------------------------- catching a bad config

def test_reports_a_configured_slug_that_matches_no_real_link():
    processes = {"other-app": [], "typo-slug": []}

    missing = runner.unmatched_slugs(processes, ["other-app", "other"])

    assert missing == ["typo-slug"]


def test_reports_nothing_when_every_configured_slug_matches():
    processes = {"other-app": []}

    assert runner.unmatched_slugs(processes, ["other-app", "other"]) == []


def test_reports_every_slug_when_the_database_has_no_links_yet():
    processes = {"other-app": [], "my-app": []}

    missing = runner.unmatched_slugs(processes, [])

    assert sorted(missing) == ["my-app", "other-app"]
