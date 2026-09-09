"""Start the apps that sit behind the tracked links.

Every startable app gets a [process:<name>] section in config.ini saying
which link it serves, how to start it, and where to start it from. The
browser only ever sends a link id - commands never leave the server.
"""
import collections
import datetime
import os
import subprocess

Proc = collections.namedtuple("Proc", "name link command cwd")
Result = collections.namedtuple("Result", "name ok message")

SECTION_PREFIX = "process:"

# Windows process flags: no console of its own, and out of this terminal's
# Ctrl+C group - so the apps keep running after you close the dashboard.
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


class LaunchError(Exception):
    """The process could not be started at all."""


def load_processes(config):
    """Map link slug -> [Proc, ...], keeping config.ini order."""
    processes = {}
    for section in config.sections():
        if not section.startswith(SECTION_PREFIX):
            continue
        name = section[len(SECTION_PREFIX):]
        link = config.get(section, "link", fallback="").strip()
        command = config.get(section, "command", fallback="").strip()
        cwd = config.get(section, "cwd", fallback="").strip()
        if not (link and command and cwd):
            print(f"[runner] ignoring [{section}]: "
                  f"needs link, command and cwd")
            continue
        processes.setdefault(link, []).append(Proc(name, link, command, cwd))
    return processes


def launch(proc, log_dir):
    """Start one process detached, appending its output to <name>.log.

    Raises LaunchError if it could not be started. Note that a process
    which starts and then dies on its own is NOT an error here - the
    health check is what tells you whether the app actually came up.
    """
    if not os.path.isdir(proc.cwd):
        raise LaunchError(f"folder not found: {proc.cwd}")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{proc.name}.log")
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # The child inherits its own copy of the handle, so closing ours here
    # does not cut off its output.
    with open(log_path, "a", encoding="utf-8", errors="replace") as log:
        log.write(f"\n--- started {stamp}: {proc.command} ---\n")
        log.flush()
        try:
            subprocess.Popen(
                ["cmd.exe", "/c", proc.command],
                cwd=proc.cwd,
                stdout=log,
                stderr=subprocess.STDOUT,
                # never let a child block forever on a prompt nobody sees
                stdin=subprocess.DEVNULL,
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            )
        except OSError as exc:
            raise LaunchError(f"could not start: {exc}") from None


def start_link(slug, processes, log_dir):
    """Start every process behind one link, in config order.

    One process failing does not stop the others - a link whose backend
    starts but whose frontend does not is still worth knowing about.
    """
    results = []
    for proc in processes.get(slug, []):
        try:
            launch(proc, log_dir)
            results.append(Result(proc.name, True, "started"))
        except LaunchError as exc:
            results.append(Result(proc.name, False, str(exc)))
    return results


def unmatched_slugs(processes, known_slugs):
    """Configured slugs that match no real link - i.e. buttons that will
    never appear. Silently missing buttons are near-impossible to
    diagnose, so the app shouts about these at startup.
    """
    known = set(known_slugs)
    return [slug for slug in processes if slug not in known]
