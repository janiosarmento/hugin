"""Errors shown on the main screen say what went wrong, even when the exception has no message."""

import httpx

from hugin.tui.review import HuginScreen


def test_empty_read_timeout_is_described_as_a_timeout():
    assert HuginScreen._format_error(httpx.ReadTimeout("")).startswith("Timeout")


def test_exception_without_a_message_shows_its_type():
    class Silent(Exception):
        pass

    assert HuginScreen._format_error(Silent()) == "Silent"
    assert HuginScreen._format_error(KeyError("slug")) == "KeyError: 'slug'"
