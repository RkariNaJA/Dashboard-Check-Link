"""check_one_link must report its result, not only record it.

The Start button asks 'is this app answering right now?' before it
launches anything - a stale row in the checks table is not good enough.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector import check_one_link


def test_reports_true_when_the_link_answers(a_link, live_server):
    link_id = a_link(live_server)

    assert check_one_link(link_id, live_server, 5, False) is True


def test_reports_false_when_nothing_is_listening(a_link, dead_url):
    link_id = a_link(dead_url)

    assert check_one_link(link_id, dead_url, 5, False) is False
