"""Start the apps that sit behind the tracked links.

Every startable app gets a [process:<name>] section in config.ini saying
which link it serves, how to start it, and where to start it from. The
browser only ever sends a link id - commands never leave the server.
"""
import collections
import datetime
import os
import subprocess
import time

# port = the TCP port the app listens on (optional). With it, Start refuses to
# launch over an old copy that still holds the port, and Restart can stop that
# old copy first.
Proc = collections.namedtuple("Proc", "name link command cwd port",
                              defaults=(None,))
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
        port_text = config.get(section, "port", fallback="").strip()
        if port_text and not port_text.isdigit():
            print(f"[runner] ignoring [{section}]: port must be a number")
            continue
        port = int(port_text) if port_text else None
        processes.setdefault(link, []).append(
            Proc(name, link, command, cwd, port))
    return processes


def port_owner(port):
    """PID of the process listening on TCP `port` on this machine, or None.

    Reads `netstat -ano`. A listening socket is the one whose foreign address
    ends in ':0' - matched that way rather than by the word LISTENING, which
    Windows translates on non-English servers.
    """
    out = ""
    for proto in ("TCP", "TCPv6"):
        out += subprocess.run(["netstat", "-ano", "-p", proto],
                              capture_output=True, text=True,
                              errors="replace").stdout
    for line in out.splitlines():
        parts = line.split()
        if (len(parts) >= 5 and parts[0].upper() == "TCP"
                and parts[1].endswith(f":{port}") and parts[2].endswith(":0")
                and parts[-1].isdigit()):
            return int(parts[-1])
    return None


def stop_port(port, timeout=10):
    """Stop whatever is listening on `port` (and its children).

    Returns the PID that was stopped, or None if the port was already free.
    Refuses to touch Link Watch itself or a Windows system process.
    """
    pid = port_owner(port)
    if pid is None:
        return None
    if pid == os.getpid():
        raise LaunchError(f"port {port} is Link Watch itself - not stopping it")
    if pid <= 4:
        raise LaunchError(f"port {port} is held by Windows (PID {pid}) - "
                          f"not stopping it")
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                   capture_output=True)
    deadline = time.time() + timeout
    while port_owner(port) is not None:
        if time.time() > deadline:
            raise LaunchError(f"PID {pid} on port {port} did not stop "
                              f"within {timeout}s")
        time.sleep(0.2)
    return pid


def launch(proc, log_dir):
    """Start one process detached, appending its output to <name>.log.

    Raises LaunchError if it could not be started. Note that a process
    which starts and then dies on its own is NOT an error here - the
    health check is what tells you whether the app actually came up.
    """
    if not os.path.isdir(proc.cwd):
        raise LaunchError(f"folder not found: {proc.cwd}")
    if proc.port:
        # Without this the new copy dies on "address already in use" and the
        # old version keeps serving - while the button still says Started.
        pid = port_owner(proc.port)
        if pid is not None:
            raise LaunchError(f"port {proc.port} is already in use by PID {pid} "
                              f"- an old copy is probably still running. "
                              f"Use Restart to stop it and start fresh.")
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


def restart_link(slug, processes, log_dir):
    """Stop the old copy of every process behind one link, then start fresh.

    A process with a port is stopped by whoever holds that port. One without
    a port cannot be found, so it is only started - the message says so.
    """
    results = []
    for proc in processes.get(slug, []):
        try:
            if proc.port:
                pid = stop_port(proc.port)
                stopped = f"stopped PID {pid}, " if pid else "was not running, "
            else:
                stopped = "no port configured, so no old copy was stopped; "
            launch(proc, log_dir)
            results.append(Result(proc.name, True, stopped + "started"))
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
