import hugin.log as log
from hugin.echo import LLM_ERRORS, pick_category

import asyncio
import httpx
import pytest


def test_log_exception_writes_traceback_and_never_raises(tmp_path, monkeypatch):
    path = tmp_path / "sub" / "hugin.log"
    monkeypatch.setattr(log, "LOG_PATH", path)
    try:
        raise ValueError("boom")
    except ValueError:
        log.log_exception("unit test")
    text = path.read_text()
    assert "unit test" in text and "ValueError: boom" in text and "Traceback" in text

    monkeypatch.setattr(log, "LOG_PATH", tmp_path)  # a directory: open() fails
    log.log_exception("must not raise")


def test_log_rotates_when_big(tmp_path, monkeypatch):
    path = tmp_path / "hugin.log"
    path.write_text("x" * 50)
    monkeypatch.setattr(log, "LOG_PATH", path)
    monkeypatch.setattr(log, "MAX_LOG_BYTES", 10)
    log.log_exception("after rotation")
    assert (tmp_path / "hugin.log.1").read_text() == "x" * 50
    assert "after rotation" in path.read_text()


def _engine():
    from hugin.engines import Engine

    return Engine("t", "http://localhost/v1", "m", 30, None)


def test_pick_category_falls_back_only_on_llm_failures(monkeypatch):
    import hugin.llm as llm

    async def net_down(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(llm, "call_llm", net_down)
    assert asyncio.run(pick_category(_engine(), "t", "b", ["Cats", "Dogs"])) == "Cats"

    async def bug(*a, **k):
        raise NameError("typo")

    monkeypatch.setattr(llm, "call_llm", bug)
    with pytest.raises(NameError):  # a programming error must surface, not hide
        asyncio.run(pick_category(_engine(), "t", "b", ["Cats", "Dogs"]))


def test_llm_errors_cover_expected_failures_only():
    for exc in (httpx.ReadTimeout("t"), ValueError("loop"), KeyError("choices"), IndexError()):
        assert isinstance(exc, LLM_ERRORS)
    assert not isinstance(NameError("x"), LLM_ERRORS)


def test_notifications_of_warning_and_error_are_logged_in_full():
    import hugin.log as log

    log.log_notification("all fine", "information")
    log.log_notification("Error: connection refused\nsecond line", "error")
    log.log_notification("careful", "warning")
    text = log.LOG_PATH.read_text()
    assert "all fine" not in text
    assert "NOTIFICATION ERROR\nError: connection refused\nsecond line" in text
    assert "NOTIFICATION WARNING\ncareful" in text
