"""Images pasted in an external editor, moved to where the blog keeps its images.

An editor such as Typora copies every inserted image to one inbox folder and
links it by absolute file path. Before a post is committed, adopt() moves
each of those images into the blog's image folder and rewrites the links, so
the post links `/images/2026/11/photo.jpg` instead of a path on this Mac.

Where a blog keeps its images is learned from its own posts (detect()), then
confirmed by the user and saved in hugin/config.toml (ImagesSettings). Images
are never converted or resized: the author uploads them ready.
"""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

# Where the external editor drops inserted images (Typora: Preferences ->
# Image -> "Copy image to custom folder")
INBOX = Path.home() / "Pictures" / "typora-inbox"

RECENT_POSTS = 10  # the newest posts with local images teach the convention

# Date subfolder patterns a blog may use, as shown to the user
DATE_PATTERNS = {"": "", "YYYY": "%Y", "YYYY/MM": "%Y/%m", "YYYY/MM/DD": "%Y/%m/%d"}

IMAGE_EXTENSIONS = (".avif", ".webp", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".heic", ".tif", ".tiff", ".bmp")

# Image references in Markdown, HTML and shortcodes; front matter values are
# read from the parsed metadata
_REF = re.compile(
    r"!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^)\s]+))"
    r"|<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']"
    r"|\{\{<\s*figure\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
_YEAR = re.compile(r"(19|20)\d\d")
_TWO = re.compile(r"\d\d")


@dataclass
class Convention:
    """Where a blog's new images go and how posts link them."""

    dir: str  # folder relative to the site root, e.g. "static/images"
    url: str  # link prefix for that folder, e.g. "/images"
    date_folders: str = ""  # a DATE_PATTERNS key: "YYYY/MM", or "" for none

    def folder(self, sub: str) -> tuple[str, str]:
        """(folder relative to the site root, link prefix) for a subfolder."""
        sub = sub.strip("/")
        return (f"{self.dir}/{sub}" if sub else self.dir), (f"{self.url}/{sub}" if sub else self.url)


@dataclass
class Detection:
    convention: Convention
    evidence: str  # one line on what the posts showed


# --- references --------------------------------------------------------------


def body_refs(text: str) -> list[str]:
    """Image references in a post's Markdown/HTML, in order."""
    return [next(g for g in m.groups() if g).strip() for m in _REF.finditer(text)]


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def post_refs(metadata: dict, content: str) -> list[str]:
    """Image references in a post: front matter values that name an image, then the body."""
    meta = [s.strip() for s in _strings(metadata) if s.strip().lower().endswith(IMAGE_EXTENSIONS)]
    return meta + body_refs(content)


def site_path(ref: str, base_url: str = "") -> str | None:
    """The site path of a reference to an image on this site ("/images/a.jpg"), else None.

    Root-relative paths and full URLs on the site's own host count; other
    hosts, page-relative paths and data URIs do not.
    """
    ref = ref.strip().split(" ", 1)[0]
    if ref.startswith("//"):
        return None
    if ref.startswith("/"):
        path = ref
    elif base_url and ref.startswith(("http://", "https://")):
        own, url = urlparse(base_url), urlparse(ref)
        if not own.netloc or url.netloc.lower() != own.netloc.lower():
            return None
        path = url.path
    else:
        return None
    path = unquote(path.split("?", 1)[0].split("#", 1)[0])
    return path if path.lower().endswith(IMAGE_EXTENSIONS) else None


# --- Typora preview of site paths --------------------------------------------------

# Typora resolves "/images/a.jpg" from the disk root; this front matter key
# makes it resolve from the site's static folder instead. Added only while a
# post is open in Typora: the value is a path on this Mac, never committed.
ROOT_URL_KEY = "typora-root-url"
_ROOT_URL_LINE = re.compile(rf"^{re.escape(ROOT_URL_KEY)}\s*:.*\n?", re.MULTILINE)


def _yaml_front_matter(text: str) -> tuple[int, int] | None:
    """(start, end) offsets of the YAML front matter's body, or None."""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 3)
    return (4, end + 1) if end != -1 else None


def add_root_url(post_path: Path, static_root: Path) -> bool:
    """Point Typora's image preview at `static_root`; False when not added (TOML, already set)."""
    from hugin.fsutil import atomic_write_text

    try:
        text = post_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False  # the editor reports a missing file better than we would
    block = _yaml_front_matter(text)
    if block is None or _ROOT_URL_LINE.search(text, *block):
        return False
    # Unquoted, as Typora's own Format > Image > Use Image Root Path writes it:
    # a quoted value does not resolve. Quoted only when YAML needs it.
    value = str(static_root)
    if not re.fullmatch(r"[\w/.~ -]+", value) or value != value.strip():
        value = json.dumps(value)
    line = f"{ROOT_URL_KEY}: {value}\n"
    atomic_write_text(post_path, text[:block[0]] + line + text[block[0]:])
    return True


def remove_root_url(text: str) -> str:
    """`text` without typora-root-url lines in its YAML front matter."""
    block = _yaml_front_matter(text)
    if block is None:
        return text
    start, end = block
    return text[:start] + _ROOT_URL_LINE.sub("", text[start:end]) + text[end:]


def strip_root_url(post_path: Path) -> bool:
    """Remove typora-root-url from the post; True when the file changed."""
    from hugin.fsutil import atomic_write_text

    try:
        text = post_path.read_text(encoding="utf-8")
    except OSError:
        return False
    clean = remove_root_url(text)
    if clean == text:
        return False
    atomic_write_text(post_path, clean)
    return True


def posts_with_root_url(content_dir: Path) -> list[Path]:
    """Markdown files under `content_dir` whose front matter still has typora-root-url."""
    found = []
    for path in sorted(content_dir.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if ROOT_URL_KEY in text and remove_root_url(text) != text:
            found.append(path)
    return found


# --- detection -----------------------------------------------------------------


def _split(path: str) -> tuple[str, str, str]:
    """(root, date pattern, topic folder) of a site path such as /images/2026/11/a.jpg."""
    parts = path.strip("/").split("/")[:-1]  # folders only
    for i, part in enumerate(parts):
        if _YEAR.fullmatch(part):
            rest = parts[i + 1:]
            pattern = "YYYY"
            if rest and _TWO.fullmatch(rest[0]):
                pattern = "YYYY/MM"
                if len(rest) > 1 and _TWO.fullmatch(rest[1]):
                    pattern = "YYYY/MM/DD"
            return "/".join(parts[:i]), pattern, ""
    if len(parts) >= 2:
        return "/".join(parts[:-1]), "", parts[-1]
    return "/".join(parts), "", ""


def detect(posts, static_dir: str = "static", base_url: str = "", n: int = RECENT_POSTS) -> Detection | None:
    """The image convention of the `n` newest posts that link images on the site.

    Posts are anything with .date, .metadata and .content (scanner.Post).
    The root most of them use wins; date subfolders count when any of those
    posts uses them. None when no post links a local image.
    """
    sample = []
    for post in sorted((p for p in posts if p.date), key=lambda p: p.date, reverse=True):
        paths = [p for p in (site_path(r, base_url) for r in post_refs(post.metadata, post.content)) if p]
        if paths:
            sample.append(paths)
            if len(sample) == n:
                break
    if not sample:
        return None
    roots = Counter()
    for paths in sample:
        roots.update({_split(p)[0] for p in paths})  # each post votes once per root
    root = roots.most_common(1)[0][0]
    dates, topics, images = Counter(), 0, 0
    for paths in sample:
        mine = [_split(p) for p in paths if _split(p)[0] == root]
        images += len(mine)
        dates.update({d for _, d, _ in mine if d})
        topics += any(t for _, _, t in mine)
    pattern = dates.most_common(1)[0][0] if dates else ""
    url = "/" + root if root else ""
    evidence = (
        f"{len(sample)} recent posts with images: {roots[root]} link {images} images "
        f"under {url or '/'}; {sum(dates.values())} use date folders, {topics} use topic folders"
    )
    folder = f"{static_dir}/{root}" if root else static_dir
    return Detection(Convention(folder, url, pattern), evidence)


# --- adopting inbox images -------------------------------------------------------


@dataclass
class Adopted:
    moved: list[tuple[str, str]] = field(default_factory=list)  # (file name in the inbox, new link)
    missing: list[str] = field(default_factory=list)  # inbox links whose file is gone

    @property
    def changed(self) -> bool:
        return bool(self.moved)


def _inbox_pattern(inbox: Path) -> re.Pattern:
    """An inbox reference as an editor writes it: plain, URL-encoded, file://, in <...>."""
    plain = str(inbox).rstrip("/")
    variants = sorted({re.escape(plain), re.escape(quote(plain))}, key=len, reverse=True)
    prefix = "(?:file://)?(?:" + "|".join(variants) + ")/"
    return re.compile(rf"<({prefix}[^>\n]+)>|({prefix}[^\s)\"'<>\]]+)")


def inbox_refs(text: str, inbox: Path | None = None) -> list[str]:
    """References in `text` to files in the inbox (INBOX by default), as written, without duplicates."""
    found = [m.group(1) or m.group(2) for m in _inbox_pattern(inbox or INBOX).finditer(text)]
    return list(dict.fromkeys(found))


def _inbox_file(ref: str) -> Path:
    path = ref[len("file://"):] if ref.startswith("file://") else ref
    return Path(unquote(path))


def safe_name(name: str) -> str:
    """A file name that needs no escaping in a link: spaces and odd characters become hyphens."""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = re.sub(r"[^\w.-]+", "-", stem, flags=re.ASCII).strip("-.") or "image"
    return f"{stem}.{ext.lower()}" if ext else stem


def _free(folder: Path, name: str) -> Path:
    """`folder/name`, or name-2, name-3… when taken."""
    target = folder / name
    stem, ext = target.stem, target.suffix
    n = 2
    while target.exists():
        target = folder / f"{stem}-{n}{ext}"
        n += 1
    return target


def subfolder(convention: Convention, existing: list[str], date: datetime | None) -> str:
    """Where a post's new images go, below the convention's folder.

    The folder the post's other images already use; else its date folder
    when the blog uses them; else the folder itself.
    """
    prefix = convention.url.rstrip("/") + "/"
    used = Counter(
        p[len(prefix):].rsplit("/", 1)[0] for p in existing
        if p.startswith(prefix) and "/" in p[len(prefix):]
    )
    if used:
        return used.most_common(1)[0][0]
    fmt = DATE_PATTERNS.get(convention.date_folders, "")
    return (date or datetime.now()).strftime(fmt) if fmt else ""


def _post_date(metadata: dict) -> datetime | None:
    value = metadata.get("date") or metadata.get("publishDate")
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def posts_with_inbox_refs(content_dir: Path, inbox: Path | None = None) -> list[Path]:
    """Markdown files under `content_dir` that link an inbox image."""
    inbox = inbox or INBOX
    needles = {str(inbox), quote(str(inbox))}
    found = []
    for path in sorted(content_dir.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(n in text for n in needles) and inbox_refs(text, inbox):
            found.append(path)
    return found


def adopt(
    post_path: Path, site_root: Path, convention: Convention,
    base_url: str = "", inbox: Path | None = None,
    moved_before: dict[Path, str] | None = None,
) -> Adopted:
    """Move the post's inbox images into the blog and rewrite their links.

    Date folders use the post's front matter date (today when it has none).

    `moved_before` maps inbox files already moved (in this session) to their
    links, so the same image pasted into a translation links the same file;
    it is updated in place. The post is rewritten only when a link changed.
    """
    import frontmatter

    from hugin.fsutil import atomic_write_text

    inbox = inbox or INBOX
    result = Adopted()
    text = post_path.read_text(encoding="utf-8")
    refs = inbox_refs(text, inbox)
    if not refs:
        return result
    moved_before = moved_before if moved_before is not None else {}
    try:
        parsed = frontmatter.loads(text)
        existing = [p for p in (site_path(r, base_url) for r in post_refs(parsed.metadata, parsed.content)) if p]
        date = _post_date(parsed.metadata)
    except Exception:  # unparsable front matter: the body references still count
        existing = [p for p in (site_path(r, base_url) for r in body_refs(text)) if p]
        date = None
    folder_rel, url_prefix = convention.folder(subfolder(convention, existing, date))
    folder = site_root / folder_rel
    for ref in refs:
        source = _inbox_file(ref)
        link = moved_before.get(source)
        if link is None and source.is_file():
            folder.mkdir(parents=True, exist_ok=True)
            target = _free(folder, safe_name(source.name))
            shutil.move(str(source), target)
            link = f"{url_prefix}/{target.name}"
            moved_before[source] = link
        elif link is None:
            # Moved by an earlier run: the file kept its (safe) name in this folder
            twin = folder / safe_name(source.name)
            if not twin.is_file():
                result.missing.append(source.name)
                continue
            link = f"{url_prefix}/{twin.name}"
        text = text.replace(f"<{ref}>", link).replace(ref, link)
        result.moved.append((source.name, link))
    if result.moved:
        atomic_write_text(post_path, text)
    return result
