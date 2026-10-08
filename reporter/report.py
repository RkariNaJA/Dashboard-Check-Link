"""Run a scheduled job and tell the dashboard how it went.

Task Scheduler runs this instead of the job itself:

    python report.py excel-bom-compare --url http://192.0.2.10:8090
           -- D:\\Filepackage\\python\\dist\\FLOW4.exe

Standard library only, on purpose: the servers that run jobs have no
packages installed and should not need any to be monitored.

The one rule this file must never break: reporting cannot change the
job's outcome. Whatever the job exits with, this exits with too.
"""
import argparse
import collections
import datetime
import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request

Run = collections.namedtuple("Run",
                             "exit_code output started finished duration_ms")

STAMP = "%Y-%m-%d %H:%M:%S"
TAIL_CHARS = 4000
ATTEMPTS = 3
RETRY_SECONDS = 5
POST_TIMEOUT = 15
ERROR_LOG = "report_errors.log"
# nothing ran, so the exit code is ours to pick: 127 is the shell's
# "command not found", which is what actually happened
COULD_NOT_START = 127


def run_job(command):
    """Run the job to completion, capturing stdout and stderr together."""
    # the flows print Thai and emoji, which raise UnicodeEncodeError on a
    # cp874 console - force the child to speak UTF-8 so we can read it
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    started = datetime.datetime.now()
    clock = time.perf_counter()
    try:
        done = subprocess.run(command, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT)
        output = done.stdout.decode("utf-8", errors="replace")
        exit_code = done.returncode
    except OSError as exc:
        output = f"could not start {command[0]!r}: {exc}"
        exit_code = COULD_NOT_START
    return Run(exit_code, output, started, datetime.datetime.now(),
               int((time.perf_counter() - clock) * 1000))


def build_payload(run):
    """The wire contract, as a named thing both sides can be read against."""
    return {
        "started_at": run.started.strftime(STAMP),
        "finished_at": run.finished.strftime(STAMP),
        "duration_ms": run.duration_ms,
        "exit_code": run.exit_code,
        "host": platform.node(),
        "output_tail": run.output[-TAIL_CHARS:],
    }


def post_report(url, slug, token, run):
    """POST one run to the dashboard. Raises if it does not land."""
    payload = json.dumps(build_payload(run)).encode("utf-8")
    request = urllib.request.Request(
        f"{url.rstrip('/')}/jobs/{slug}/report", data=payload, method="POST")
    request.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        request.add_header("X-Job-Token", token)
    with urllib.request.urlopen(request, timeout=POST_TIMEOUT) as response:
        return response.status


def note(log_path, message):
    """Leave a trace locally - this is the only record if the POST failed."""
    line = f"{datetime.datetime.now().strftime(STAMP)} {message}"
    try:
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(line + "\n")
    except OSError:
        pass            # even logging must not be able to break the job
    # same guarantee for stderr: a broken pipe, or a cp874 console choking
    # on non-ASCII text from str(exc), must not be able to raise out of
    # here either - there is no exception class worth crashing the job for
    try:
        sys.stderr.write("[report] " + message + "\n")
    except Exception:
        pass


def report_with_retries(url, slug, token, run, log_path):
    """Try to report. Never raises. True if the dashboard took it.

    A dropped report shows on the dashboard as 'overdue', which is a
    false alarm we accept: the dashboard being unreachable is itself
    worth noticing, and a disk-backed spool is complexity we do not need.
    """
    problem = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            post_report(url, slug, token, run)
            return True
        except Exception as exc:          # same reasoning as note(): there is no
            problem = exc                 # exception class worth crashing the job for
            if attempt < ATTEMPTS:
                time.sleep(RETRY_SECONDS)
    note(log_path, f"{slug}: could not report after {ATTEMPTS} tries: {problem}")
    return False


def main(argv=None):
    # argparse.REMAINDER does not mix with a required option placed before
    # it (the remainder swallows "--url" instead of leaving it to be
    # parsed), so the split is done by hand: everything after the first
    # "--" is the command, argparse only ever sees what comes before it.
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" in argv:
        split = argv.index("--")
        head, command = argv[:split], argv[split + 1:]
    else:
        head, command = argv, []

    parser = argparse.ArgumentParser(
        description="Run a job and report the result to the dashboard.")
    parser.add_argument("slug", help="matches a [job:<slug>] section")
    parser.add_argument("--url", required=True,
                        help="dashboard root, e.g. http://192.0.2.10:8090")
    parser.add_argument("--token", default="",
                        help="must match [jobs] token on the dashboard")
    args = parser.parse_args(head)

    if not command:
        parser.error("no command given - put it after --")

    run = run_job(command)
    report_with_retries(args.url, args.slug, args.token, run,
                        os.path.join(os.getcwd(), ERROR_LOG))

    # pass the job's own output through, as bytes: re-encoding it for a
    # cp874 console is exactly the crash we told the child to avoid.
    # Echoing it is a courtesy, though, not the job - a closed or broken
    # pipe here (Task Scheduler piping into something that exits early)
    # must not be able to turn a good run's exit code into a traceback's.
    # There is no exception class we would rather crash on, hence the
    # blanket catch.
    try:
        sys.stdout.buffer.write(run.output.encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
    except Exception:
        pass
    return run.exit_code


if __name__ == "__main__":
    sys.exit(main())
