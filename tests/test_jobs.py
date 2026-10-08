"""Reading [job:...] sections out of config.ini."""
import configparser
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jobs


def cfg(text):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(text)
    return parser


# ----------------------------------------------------------- parse_duration

@pytest.mark.parametrize("text, seconds", [
    ("30m", 1800),
    ("6h", 21600),
    ("24h", 86400),
    ("7d", 604800),
    (" 12H ", 43200),
])
def test_reads_the_duration_forms_the_config_file_documents(text, seconds):
    assert jobs.parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "daily", "24", "h", "0h", "-3h", "1w"])
def test_rejects_anything_it_cannot_read_rather_than_guessing(text):
    with pytest.raises(jobs.ConfigError):
        jobs.parse_duration(text)


# --------------------------------------------------------------- load_jobs

def test_reads_a_job_section_keyed_by_its_slug():
    loaded = jobs.load_jobs(cfg("""
[job:excel-bom-compare]
name         = Excel BOM Compare (FLOW4)
expect_every = 24h
grace        = 2h
host         = 192.0.2.20
"""))

    assert list(loaded) == ["excel-bom-compare"]
    job = loaded["excel-bom-compare"]
    assert job.name == "Excel BOM Compare (FLOW4)"
    assert job.expect_every == 86400
    assert job.grace == 7200
    assert job.host == "192.0.2.20"


def test_grace_defaults_to_an_hour_when_not_given():
    loaded = jobs.load_jobs(cfg("""
[job:nightly]
expect_every = 24h
"""))

    assert loaded["nightly"].grace == 3600


def test_name_falls_back_to_the_slug():
    loaded = jobs.load_jobs(cfg("""
[job:nightly]
expect_every = 24h
"""))

    assert loaded["nightly"].name == "nightly"


def test_ignores_sections_that_are_not_jobs():
    loaded = jobs.load_jobs(cfg("""
[server]
port = 8090

[process:some-app]
link = some-app
"""))

    assert loaded == {}


def test_a_broken_job_is_skipped_without_taking_the_good_ones_down(capsys):
    """One typo in config.ini must not stop the dashboard from starting."""
    loaded = jobs.load_jobs(cfg("""
[job:broken]
expect_every = every day

[job:fine]
expect_every = 24h
"""))

    assert list(loaded) == ["fine"]
    assert "broken" in capsys.readouterr().out


def test_a_job_with_no_expect_every_is_skipped():
    """Without it there is no such thing as overdue, which is the point."""
    assert jobs.load_jobs(cfg("""
[job:no-schedule]
name = Something
""")) == {}


def test_keeps_config_order():
    loaded = jobs.load_jobs(cfg("""
[job:zebra]
expect_every = 24h

[job:apple]
expect_every = 24h
"""))

    assert list(loaded) == ["zebra", "apple"]
