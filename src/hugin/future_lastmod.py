"""Published posts whose last-modified date is in the future: they block a sync.

A lastmod a year ahead (a typo, a wrong date picked) tells readers and
search engines the post changed on a day that has not come, and nothing
on the site flags it. Hugo reads the date from `lastmod` or `modified`
(keys in any case); both are checked. Drafts are not published, so they
may hold any date.

YAML and TOML front matter are both read: the check covers every post
of the site, including those Hugin does not otherwise handle.

A date with a timezone is compared with the current instant; one without
is local time, as Hugo reads it with no timezone configured. A bare day
(2026-11-27) is midnight of that day, so today never counts as future.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import frontmatter

LASTMOD_KEYS = ("lastmod", "modified")
_TOML = re.compile(r"\A\+\+\+[ \t]*\r?\n(.*?)^\+\+\+[ \t]*$", re.S | re.M)


@dataclass
class FuturePost:
    path: Path
    title: str
    lastmod: datetime


def _as_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _metadata(text: str) -> dict:
    """The front matter of `text`, YAML or TOML; raises when it cannot be parsed."""
    if text.startswith("+++"):
        match = _TOML.match(text)
        return tomllib.loads(match.group(1)) if match else {}
    return frontmatter.loads(text).metadata


def _draft(metadata: dict) -> bool:
    draft = metadata.get("draft", False)
    if isinstance(draft, str):
        return draft.strip().lower() in ("true", "yes", "on", "1")
    return bool(draft)


def _is_future(when: datetime, now: datetime) -> bool:
    if when.tzinfo is None:
        return when > now.replace(tzinfo=None)
    return when > now


def find(content_dir: Path, now: datetime | None = None) -> list[FuturePost]:
    """Posts under `content_dir` that are not drafts and have a lastmod after `now`."""
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.astimezone()
    found = []
    for path in sorted(content_dir.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
            metadata = _metadata(text)
        except Exception:  # unreadable or unparsable: nothing to compare
            continue
        if _draft(metadata):
            continue
        fields = {str(k).lower(): v for k, v in metadata.items()}
        for key in LASTMOD_KEYS:
            when = _as_datetime(fields.get(key))
            if when is not None and _is_future(when, now):
                found.append(FuturePost(path, str(metadata.get("title") or ""), when))
                break
    return found
