"""Ctrl+Z must not stop Hugin outside the TUI."""

import signal

import pytest

from hugin.cli import _ignore_ctrl_z


@pytest.mark.skipif(not hasattr(signal, "SIGTSTP"), reason="no SIGTSTP on this platform")
def test_ignore_ctrl_z_sets_sigtstp_to_ignore():
    previous = signal.getsignal(signal.SIGTSTP)
    try:
        signal.signal(signal.SIGTSTP, signal.SIG_DFL)
        _ignore_ctrl_z()
        assert signal.getsignal(signal.SIGTSTP) == signal.SIG_IGN
    finally:
        signal.signal(signal.SIGTSTP, previous)
