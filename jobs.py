"""Scheduled jobs: what we expect to hear from, and how late it is.

Jobs are declared in config.ini as [job:<slug>] sections, the same way
start buttons are. Nothing here reaches out to a job's machine - jobs
report in to the dashboard, so a job we never hear from is simply late.
"""
import collections
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
