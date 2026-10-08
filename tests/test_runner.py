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


# ----------------------------------------------- ports: refuse, stop, restart

import socket
import subprocess


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve_on(port):
    """A real listener in a separate process, like an old copy left running."""
    child = subprocess.Popen([sys.executable, "-m", "http.server", str(port),
                              "--bind", "127.0.0.1"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 15
        while runner.port_owner(port) is None and time.time() < deadline:
            time.sleep(0.2)
    except BaseException:
        child.kill()   # never leave a stray server behind if waiting blows up
        raise
    return child


def test_reads_an_optional_port():
    processes = runner.load_processes(cfg(r"""
[process:back]
link    = dash
command = py .\serve.py
cwd     = D:\Projects\pps
port    = 5001

[process:front]
link    = dash
command = npm run dev
cwd     = D:\Projects\pps\frontend
"""))

    assert [p.port for p in processes["dash"]] == [5001, None]


def test_skips_a_section_whose_port_is_not_a_number(capsys):
    processes = runner.load_processes(cfg(r"""
[process:back]
link    = dash
command = py .\serve.py
cwd     = D:\Projects\pps
port    = five-thousand
"""))

    assert processes == {}
    assert "back" in capsys.readouterr().out


def test_port_owner_finds_the_process_listening_on_a_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]

        assert runner.port_owner(port) == os.getpid()


def test_port_owner_is_none_for_a_free_port():
    assert runner.port_owner(free_port()) is None


def test_launch_refuses_when_its_port_is_already_taken(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        proc = runner.Proc("demo", "demo-link", "echo should-not-run",
                           str(tmp_path), port)

        with pytest.raises(runner.LaunchError) as exc:
            runner.launch(proc, str(tmp_path / "logs"))

    message = str(exc.value)
    assert f"port {port}" in message and str(os.getpid()) in message
    assert not (tmp_path / "logs" / "demo.log").exists()   # nothing was started


def test_stop_port_kills_the_old_copy_and_frees_the_port():
    port = free_port()
    child = serve_on(port)
    try:
        assert runner.port_owner(port) == child.pid

        assert runner.stop_port(port) == child.pid
        assert runner.port_owner(port) is None
    finally:
        child.kill()


def test_stop_port_does_nothing_when_the_port_is_free():
    assert runner.stop_port(free_port()) is None


def test_stop_port_never_kills_link_watch_itself():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]

        with pytest.raises(runner.LaunchError, match="Link Watch"):
            runner.stop_port(port)


def test_restart_link_replaces_the_old_copy_with_a_fresh_one(tmp_path):
    port = free_port()
    old = serve_on(port)
    # Unquoted, like the real config.ini commands - launch() hands the string to
    # `cmd /c`, which does not survive an embedded quoted path.
    command = f"py -m http.server {port} --bind 127.0.0.1"
    procs = {"dash": [runner.Proc("web", "dash", command, str(tmp_path), port)]}
    try:
        results = runner.restart_link("dash", procs, str(tmp_path / "logs"))

        assert [(r.name, r.ok) for r in results] == [("web", True)]
        assert f"stopped PID {old.pid}" in results[0].message
        deadline = time.time() + 15
        while runner.port_owner(port) in (None, old.pid) and time.time() < deadline:
            time.sleep(0.2)
        new_pid = runner.port_owner(port)
        assert new_pid not in (None, old.pid)
    finally:
        old.kill()
        if runner.port_owner(port):
            runner.stop_port(port)


def test_restart_link_still_starts_a_process_with_no_port(tmp_path):
    logs = tmp_path / "logs"
    procs = {"dash": [runner.Proc("demo", "dash", "echo restarted", str(tmp_path))]}

    results = runner.restart_link("dash", procs, str(logs))

    assert [(r.name, r.ok) for r in results] == [("demo", True)]
    assert "no port" in results[0].message
    assert "restarted" in log_containing(logs / "demo.log", "restarted")
