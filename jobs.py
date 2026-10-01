"""Scheduled jobs: what we expect to hear from, and how late it is.

Jobs are declared in config.ini as [job:<slug>] sections, the same way
start buttons are. Nothing here reaches out to a job's machine - jobs
report in to the dashboard, so a job we never hear from is simply late.
"""
import collections
import datetime
import re

Job = collections.namedtuple("Job", "slug name expect_every grace host")

SECTION_PREFIX = "job:"
DEFAULT_GRACE = "1h"

_UNITS = {"m": 60, "h": 3600, "d": 86400}
_DURATION = re.compile(r"\s*(\d+)\s*([mhd])\s*", re.IGNORECASE)


class ConfigError(Exception):
    """A [job:...] section the dashboard cannot use."""


def parse_duration(text):
    """'30m' / '6h' / '7d' -> seconds.

    Deliberately narrow: a schedule that is read wrong produces alerts
    that are wrong, which is worse than refusing to read it at all.
    """
    match = _DURATION.fullmatch(text or "")
    if not match:
        raise ConfigError(
            f"expected a duration like 30m, 6h or 7d - got {text!r}")
    value = int(match.group(1))
    if value < 1:
        raise ConfigError(f"duration must be at least 1 - got {text!r}")
    return value * _UNITS[match.group(2).lower()]


def load_jobs(config):
    """Map slug -> Job for every [job:<slug>] section, in config order.

    A section we cannot read is skipped with a printed reason rather than
    raising: one typo should not take the whole dashboard, links and all,
    off the air.
    """
    loaded = {}
    for section in config.sections():
        if not section.startswith(SECTION_PREFIX):
            continue
        slug = section[len(SECTION_PREFIX):].strip()
        try:
            if not slug:
                raise ConfigError("section name has no slug after 'job:'")
            expect_every = parse_duration(
                config.get(section, "expect_every", fallback=""))
            grace = parse_duration(
                config.get(section, "grace", fallback=DEFAULT_GRACE))
        except ConfigError as exc:
            print(f"[jobs] ignoring [{section}]: {exc}")
            continue
        loaded[slug] = Job(
            slug=slug,
            name=config.get(section, "name", fallback="").strip() or slug,
            expect_every=expect_every,
            grace=grace,
            host=config.get(section, "host", fallback="").strip(),
        )
    return loaded


STATUS_NEVER = "never"
STATUS_OVERDUE = "overdue"
STATUS_FAILED = "failed"
STATUS_OK = "ok"

STAMP = "%Y-%m-%d %H:%M:%S"


def _started(run):
    """When this run began. Ingest validates the format, so this is safe."""
    return datetime.datetime.strptime(run["started_at"], STAMP)


def derive_status(job, run, now):
    """never -> overdue -> failed -> ok.

    Overdue outranks failed on purpose: a job that failed last night and
    has not run since has two problems, and the one you cannot see by
    reading its log is that it stopped running at all.
    """
    if run is None:
        return STATUS_NEVER
    late_by = (now - _started(run)).total_seconds()
    if late_by > job.expect_every + job.grace:
        return STATUS_OVERDUE
    return STATUS_OK if run["ok"] else STATUS_FAILED


def next_expected(job, run):
    """When this job should next be heard from, or None before its first."""
    if run is None:
        return None
    return _started(run) + datetime.timedelta(seconds=job.expect_every)


def build_rows(loaded_jobs, latest, now):
    """One display row per configured job, in config order."""
    rows = []
    for job in loaded_jobs.values():
        run = latest.get(job.slug)
        rows.append({
            "job": job,
            "run": run,
            "status": derive_status(job, run, now),
            "next_expected": next_expected(job, run),
        })
    return rows


def summarize(rows, runs_24h):
    counts = collections.Counter(row["status"] for row in rows)
    return {
        "total": len(rows),
        "ok": counts[STATUS_OK],
        "failed": counts[STATUS_FAILED],
        "overdue": counts[STATUS_OVERDUE],
        "never": counts[STATUS_NEVER],
        "runs_24h": runs_24h,
    }
