"""Which Hugin is running, to tell at a glance whether it is up to date.

Hugin runs from its git checkout (editable install), so the version comes
from git when the program starts: the build number is the commit count of
HEAD, which only grows, plus the short hash and date of that commit. A
checkout with uncommitted changes to tracked files says so. Without git
(an installed copy), only the package version is known.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from functools import cache
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GIT_TIMEOUT = 5


@dataclass(frozen=True)
class Version:
    base: str
    build: int | None = None
    commit: str | None = None
    date: str | None = None
    dirty: bool = False

    def short(self) -> str:
        """e.g. "build 259 · 0f7ec97 · 2026-11-11"."""
        if self.build is None:
            return f"{self.base} (build unknown)"
        text = f"build {self.build} · {self.commit} · {self.date}"
        return text + " · local changes" if self.dirty else text

    def __str__(self) -> str:
        return f"hugin {self.base}" + (f", {self.short()}" if self.build is not None else " (build unknown)")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True, text=True, check=True, timeout=GIT_TIMEOUT,
    ).stdout.strip()


@cache
def current() -> Version:
    """The running version, read once per process (later pulls do not change it)."""
    try:
        base = package_version("hugin")
    except PackageNotFoundError:
        base = "unknown"
    if not (REPO / ".git").exists():
        return Version(base)
    try:
        build = int(_git("rev-list", "--count", "HEAD"))
        commit, date = _git("log", "-1", "--format=%h %cs").split()
        dirty = bool(_git("status", "--porcelain", "--untracked-files=no"))
    except (OSError, subprocess.SubprocessError, ValueError):
        return Version(base)
    return Version(base, build, commit, date, dirty)
