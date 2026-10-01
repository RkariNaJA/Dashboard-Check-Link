"""The wrapper Task Scheduler runs instead of the job itself."""
import http.server
import json
import os
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "reporter"))

import report


@pytest.fixture
def collector():
    """A stub dashboard that remembers what was posted to it."""
    received = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            received.append({"path": self.path,
                             "token": self.headers.get("X-Job-Token"),
                             "body": json.loads(body)})
            self.send_response(201)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", received
    server.shutdown()


def python_that(code):
    return [sys.executable, "-c", code]


# ----------------------------------------------------------------- run_job

def test_captures_a_successful_child(tmp_path):
    run = report.run_job(python_that("print('hello')"))

    assert run.exit_code == 0
    assert "hello" in run.output
    assert run.duration_ms >= 0


def test_captures_a_failing_child_and_its_stderr():
    run = report.run_job(
        python_that("import sys; sys.stderr.write('boom'); sys.exit(3)"))

    assert run.exit_code == 3
    assert "boom" in run.output


def test_a_command_that_does_not_exist_is_not_a_crash():
    run = report.run_job(["definitely-not-a-real-program-xyz"])

    assert run.exit_code != 0
    assert "could not start" in run.output


def test_thai_and_emoji_in_child_output_survive():
    run = report.run_job(python_that(
        "print('\\U0001F680 \\u0e40\\u0e23\\u0e34\\u0e48\\u0e21\\u0e23\\u0e31\\u0e19')"))

    assert "🚀" in run.output


# ------------------------------------------------------------ posting home

def test_posts_the_run_to_the_dashboard(collector, tmp_path):
    url, received = collector
    run = report.run_job(python_that("print('done')"))

    assert report.report_with_retries(url, "flow4", "", run,
                                      str(tmp_path / "err.log")) is True
    assert received[0]["path"] == "/jobs/flow4/report"
    body = received[0]["body"]
    assert body["exit_code"] == 0
    assert "done" in body["output_tail"]
    assert body["host"]


def test_sends_the_token_when_one_is_given(collector, tmp_path):
    url, received = collector
    run = report.run_job(python_that("pass"))

    report.report_with_retries(url, "flow4", "s3cret", run,
                               str(tmp_path / "err.log"))

    assert received[0]["token"] == "s3cret"


def test_a_failed_post_is_logged_and_reported_as_false(tmp_path):
    log = tmp_path / "err.log"
    run = report.run_job(python_that("pass"))

    # port 9 is reserved and refuses immediately
    assert report.report_with_retries("http://127.0.0.1:9", "flow4", "", run,
                                      str(log)) is False
    assert "flow4" in log.read_text(encoding="utf-8")


# ---------------------------------------------------------- the whole thing

def test_main_exits_with_the_childs_code(collector):
    url, _ = collector

    code = report.main(["flow4", "--url", url, "--",
                        sys.executable, "-c", "import sys; sys.exit(7)"])

    assert code == 7


def test_main_still_exits_with_the_childs_code_when_reporting_fails(tmp_path,
                                                                    monkeypatch):
    """The whole design rests on this.

    If a dashboard being down could turn a good run into a bad one, the
    monitoring would be worse than no monitoring at all.
    """
    monkeypatch.chdir(tmp_path)

    code = report.main(["flow4", "--url", "http://127.0.0.1:9", "--",
                        sys.executable, "-c", "print('fine')"])

    assert code == 0


def test_main_needs_a_command_after_the_separator():
    with pytest.raises(SystemExit):
        report.main(["flow4", "--url", "http://127.0.0.1:9"])


def test_the_childs_own_flags_and_separator_survive_the_split(collector):
    """The wrapper consumes only the FIRST --; the child keeps the rest.

    Pins the hand-rolled argv split (the fix for the argparse.REMAINDER
    bug) against a later refactor silently eating too much or too little.
    """
    url, _ = collector
    # everything from here on is the job's own command line, including a
    # second "--" and tokens that look exactly like the wrapper's own
    # flags - none of it belongs to the wrapper.
    child_args = ["--", "--url", "nope", "subcommand", "--token", "ignored"]
    code = "import sys; sys.exit(len(sys.argv) - 1)"

    exit_code = report.main(["flow4", "--url", url, "--",
                             sys.executable, "-c", code, *child_args])

    assert exit_code == len(child_args)


def test_main_returns_the_childs_code_even_if_stdout_is_broken(monkeypatch,
                                                                collector):
    """Echoing the job's output is a courtesy; it must not be able to
    change the exit code if the pipe underneath it is already gone."""
    url, _ = collector

    class BrokenBuffer:
        def write(self, data):
            raise BrokenPipeError("broken pipe")

        def flush(self):
            raise BrokenPipeError("broken pipe")

    class FakeStdout:
        buffer = BrokenBuffer()

    monkeypatch.setattr(sys, "stdout", FakeStdout())

    code = report.main(["flow4", "--url", url, "--",
                        sys.executable, "-c", "import sys; sys.exit(5)"])

    assert code == 5
