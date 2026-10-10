"""Safe file writing shared by everything that persists data."""

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` so a crash never leaves a truncated file.

    Writes a temp file in the same directory, then renames it over `path`.
    An existing file keeps its permission bits (mkstemp would make it 0600).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            os.chmod(tmp_name, path.stat().st_mode & 0o7777)
        else:
            # mkstemp is 0600; a new file gets the usual umask-based mode
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(tmp_name, 0o666 & ~umask)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def quarantine(path: Path) -> Path | None:
    """Move an unreadable file aside (`<name>.corrupt[-N]`) instead of losing it.

    Without this the next save would overwrite the broken file. Returns the
    new path, or None when the file could not be moved.
    """
    path = Path(path)
    target = path.with_name(path.name + ".corrupt")
    n = 1
    while target.exists():
        target = path.with_name(f"{path.name}.corrupt-{n}")
        n += 1
    try:
        os.replace(path, target)
    except OSError:
        return None
    return target
