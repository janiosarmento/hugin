"""Netlify / Cloudflare Pages _redirects file management."""

from pathlib import Path


def find_redirects_file(posts_dir: Path) -> Path | None:
    """Walk up from posts_dir to find static/_redirects.

    Returns the path inside the first static/ directory found (the file
    may not exist yet). Returns None if no static/ directory is found.
    """
    current = posts_dir.resolve()
    while True:
        static = current / "static"
        if static.is_dir():
            return static / "_redirects"
        parent = current.parent
        if parent == current:
            return None
        current = parent


def read_redirects(path: Path) -> list[tuple[str, str, str]]:
    """Parse _redirects into a list of (origin, destination, code) tuples.

    Blank lines and comment lines (# …) are ignored.
    Code defaults to '301' when omitted from the line.
    """
    if not path.exists():
        return []
    entries: list[tuple[str, str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            origin, dest = parts[0], parts[1]
            code = parts[2] if len(parts) >= 3 else "301"
            entries.append((origin, dest, code))
    return entries


def write_redirects(path: Path, entries: list[tuple[str, str, str]]) -> None:
    """Write entries back to the _redirects file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{origin} {dest} {code}" for origin, dest, code in entries]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def append_redirect(path: Path, origin: str, dest: str, code: str = "301") -> None:
    """Append a redirect, replacing any existing entry with the same origin."""
    entries = read_redirects(path)
    entries = [(o, d, c) for o, d, c in entries if o != origin]
    entries.append((origin, dest, code))
    write_redirects(path, entries)
