"""Trash for deleted posts: nothing Hugin removes is gone for good."""

import shutil
from datetime import datetime
from pathlib import Path

from hugin.engines import CONFIG_DIR

TRASH_DIR = CONFIG_DIR / "trash"


def trash_location() -> str:
    """The trash folder as shown to the user (home abbreviated to ~)."""
    try:
        return "~/" + str(TRASH_DIR.relative_to(Path.home()))
    except ValueError:
        return str(TRASH_DIR)


def move_to_trash(path: Path) -> Path:
    """Move `path` into the trash as `<timestamp>-<name>`; returns the new path."""
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = TRASH_DIR / f"{stamp}-{path.name}"
    n = 1
    while target.exists():
        target = TRASH_DIR / f"{stamp}-{n}-{path.name}"
        n += 1
    shutil.move(str(path), str(target))
    return target
