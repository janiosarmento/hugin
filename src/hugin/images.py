"""Images pasted in an external editor, moved to where the blog keeps its images.

An editor such as Typora copies every inserted image to one inbox folder and
links it by a path on this Mac. Before a post is committed, adopt() moves
each of those images into the blog's image folder and rewrites the links, so
the post links `/images/2026/11/photo.jpg` instead. While Hugin has a post
open in Typora, Typora's own front matter keys send pasted images straight
to that folder (add_typora_keys()); adopt() then only tidies up.

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
# A plain link, not an image: Typora inserts some image files (AVIF) this way
_PLAIN_LINK = re.compile(r"(?<!!)\[([^\]]*)\]\(\s*(?:<([^>]+)>|([^)\s]+))\s*\)")
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


def plain_image_links(text: str) -> list[str]:
    """Targets of plain links (no `!`) to image files, in order."""
    targets = [(m.group(2) or m.group(3)).strip() for m in _PLAIN_LINK.finditer(text)]
    return [t for t in targets if unquote(t.split("?")[0]).lower().endswith(IMAGE_EXTENSIONS)]


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


# --- Typora keys while a post is open ---------------------------------------------

# Typora resolves "/images/a.jpg" from the disk root; typora-root-url makes it
# resolve from the site's static folder instead, and typora-copy-images-to
# makes it copy pasted images straight into the post's image folder (written
# then as /images/... links). Both are added only while Hugin has the post
# open in Typora: their values are paths on this Mac, never committed.
ROOT_URL_KEY = "typora-root-url"
COPY_TO_KEY = "typora-copy-images-to"
_TYPORA_LINE = re.compile(r"^typora-(?:root-url|copy-images-to)\s*:.*\n?", re.MULTILINE)


def _yaml_front_matter(text: str) -> tuple[int, int] | None:
    """(start, end) offsets of the YAML front matter's body, or None."""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 3)
    return (4, end + 1) if end != -1 else None


def _yaml_path(path: Path) -> str:
    """A path as a YAML value: unquoted, as Typora writes it (a quoted root
    url does not resolve in Typora), quoted only when YAML needs it."""
    value = str(path)
    if not re.fullmatch(r"[\w/.~ -]+", value) or value != value.strip():
        value = json.dumps(value)
    return value


def add_typora_keys(post_path: Path, static_root: Path, copy_to: Path | None = None) -> bool:
    """Add typora-root-url (and typora-copy-images-to) to the post's YAML front matter.

    False when nothing was added: TOML front matter, keys already there, or
    an unreadable file (the editor reports a missing file better).
    """
    from hugin.fsutil import atomic_write_text

    try:
        text = post_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    block = _yaml_front_matter(text)
    if block is None or _TYPORA_LINE.search(text, *block):
        return False
    lines = f"{ROOT_URL_KEY}: {_yaml_path(static_root)}\n"
    if copy_to is not None:
        lines += f"{COPY_TO_KEY}: {_yaml_path(copy_to)}\n"
    atomic_write_text(post_path, text[:block[0]] + lines + text[block[0]:])
    return True


def remove_typora_keys(text: str) -> str:
    """`text` without Typora's keys in its YAML front matter."""
    block = _yaml_front_matter(text)
    if block is None:
        return text
    start, end = block
    return text[:start] + _TYPORA_LINE.sub("", text[start:end]) + text[end:]


def strip_typora_keys(post_path: Path) -> bool:
    """Remove Typora's keys from the post; True when the file changed."""
    from hugin.fsutil import atomic_write_text

    try:
        text = post_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    clean = remove_typora_keys(text)
    if clean == text:
        return False
    atomic_write_text(post_path, clean)
    return True


def posts_with_typora_keys(content_dir: Path) -> list[Path]:
    """Markdown files under `content_dir` whose front matter still has Typora's keys."""
    found = []
    for path in sorted(content_dir.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "typora-" in text and remove_typora_keys(text) != text:
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


# --- adopting pasted images -------------------------------------------------------


@dataclass
class Adopted:
    moved: list[tuple[str, str]] = field(default_factory=list)  # (inbox file name, new link)
    relinked: list[str] = field(default_factory=list)  # links to site files made site paths
    missing: list[str] = field(default_factory=list)  # inbox links whose file is gone

    @property
    def changed(self) -> bool:
        return bool(self.moved or self.relinked)


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


def _parse(text: str, base_url: str) -> tuple[list[str], list[str], datetime | None]:
    """(every image reference as written, the site paths among them, the post's date)."""
    import frontmatter

    try:
        parsed = frontmatter.loads(text)
        refs = post_refs(parsed.metadata, parsed.content)
        date = _post_date(parsed.metadata)
    except Exception:  # unparsable front matter: the body references still count
        refs, date = body_refs(text), None
    refs = list(dict.fromkeys(refs))
    return refs, [p for p in (site_path(r, base_url) for r in refs) if p], date


def destination(post_path: Path, site_root: Path, convention: Convention, base_url: str = "") -> tuple[Path, str]:
    """(folder, link prefix) for the post's new images."""
    _, existing, date = _parse(post_path.read_text(encoding="utf-8"), base_url)
    folder_rel, url_prefix = convention.folder(subfolder(convention, existing, date))
    return site_root / folder_rel, url_prefix


def _normpath(path: str) -> Path:
    import os

    return Path(os.path.normpath(path))


def resolve(
    ref: str, post_dir: Path, static_root: Path, inbox: Path, site_root: Path | None = None,
) -> tuple[str, Path] | None:
    """("inbox" | "static" | "local", file) a local image reference points to, else None.

    An editor may write an inbox image as an absolute path, a file:// URL, or
    (with typora-root-url set) relative to the static folder, as in
    /../../../Pictures/typora-inbox/a.png; and a site image as a path relative
    to the post. "local" is an existing image file elsewhere on this Mac and
    outside the site (one dragged in from Downloads). Plain site paths
    (/images/a.jpg) are None: nothing to fix.
    """
    site_root = site_root or static_root.parent
    path = unquote(ref[len("file://"):] if ref.startswith("file://") else ref).strip()
    if not path or "://" in path or path.startswith(("//", "data:", "#")):
        return None
    if path.startswith("/"):
        candidates = [_normpath(f"{static_root}{path}"), _normpath(path)]
    else:
        candidates = [_normpath(str(post_dir / path))]
    for c in candidates:
        if c.is_relative_to(inbox):
            return "inbox", c
    if path.startswith("/") and ".." not in path.split("/") and candidates[0].is_file():
        return None  # already a site path to an existing file
    for c in candidates:
        if c.is_relative_to(static_root) and c.is_file():
            return "static", c
    for c in candidates:
        if not c.is_relative_to(site_root) and c.suffix.lower() in IMAGE_EXTENSIONS and c.is_file():
            return "local", c
    return None


def needs_adopting(post_path: Path, static_root: Path, inbox: Path | None = None) -> bool:
    """True when the post links an inbox image or a site image by a non-site path."""
    inbox = inbox or INBOX
    try:
        text = post_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    refs, _, _ = _parse(text, "")
    if any(resolve(r, post_path.parent, static_root, inbox) for r in refs):
        return True
    return any(_outside_file(r, post_path.parent, static_root, inbox) for r in plain_image_links(text))


def _outside_file(ref: str, post_dir: Path, static_root: Path, inbox: Path) -> bool:
    """True when a plain link points to an image file outside the blog (to bring in as an image)."""
    hit = resolve(ref, post_dir, static_root, inbox)
    return bool(hit) and hit[0] != "static"


def posts_to_adopt(content_dir: Path, static_root: Path, inbox: Path | None = None) -> list[Path]:
    """Markdown files under `content_dir` that need adopt()."""
    inbox = inbox or INBOX
    needles = (inbox.name, quote(inbox.name), "../", "file://", str(Path.home()), quote(str(Path.home())))
    found = []
    for path in sorted(content_dir.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(n in text for n in needles) and needs_adopting(path, static_root, inbox):
            found.append(path)
    return found


def adopt(
    post_path: Path, site_root: Path, convention: Convention, static_root: Path | None = None,
    base_url: str = "", inbox: Path | None = None,
    moved_before: dict[Path, str] | None = None,
) -> Adopted:
    """Bring the post's images from outside the blog into it and fix links to site images.

    Inbox images are moved to destination() with a link-safe name; images
    elsewhere on this Mac (Downloads) are copied there, the original kept.
    A plain link to such a file becomes an image, since Typora inserts some
    image files (AVIF) as links. Links to files already in the static folder
    by another path become site paths. Date folders use the post's front
    matter date (today when it has none).

    `moved_before` maps files already brought in (in this session) to their
    links, so the same image pasted into a translation links the same file;
    it is updated in place. The post is rewritten only when a link changed.
    """
    from hugin.fsutil import atomic_write_text

    inbox = inbox or INBOX
    static_root = static_root or site_root / "static"
    result = Adopted()
    text = post_path.read_text(encoding="utf-8")
    refs, existing, date = _parse(text, base_url)
    plain = [r for r in plain_image_links(text) if _outside_file(r, post_path.parent, static_root, inbox)]
    found = [(r, resolve(r, post_path.parent, static_root, inbox)) for r in dict.fromkeys(refs + plain)]
    found = [(r, kind, file) for r, hit in found if hit for kind, file in [hit]]
    if not found:
        return result
    moved_before = moved_before if moved_before is not None else {}
    folder_rel, url_prefix = convention.folder(subfolder(convention, existing, date))
    folder = site_root / folder_rel
    links = {}
    for ref, kind, source in found:
        if kind == "static":
            link = "/" + quote(source.relative_to(static_root).as_posix())
            result.relinked.append(link)
        else:
            link = moved_before.get(source)
            if link is None and source.is_file():
                folder.mkdir(parents=True, exist_ok=True)
                target = _free(folder, safe_name(source.name))
                if kind == "inbox":
                    shutil.move(str(source), target)
                else:
                    shutil.copy2(source, target)
                link = f"{url_prefix}/{target.name}"
                moved_before[source] = link
            elif link is None:
                # Moved by an earlier run: the file kept its (safe) name in this folder
                twin = folder / safe_name(source.name)
                if not twin.is_file():
                    result.missing.append(source.name)
                    continue
                link = f"{url_prefix}/{twin.name}"
            result.moved.append((source.name, link))
        links[ref] = link
    # Plain links to the files brought in become images, then every reference gets its link
    text = _PLAIN_LINK.sub(
        lambda m: f"![{m.group(1)}]({links[t]})" if (t := (m.group(2) or m.group(3)).strip()) in plain and t in links
        else m.group(0),
        text,
    )
    for ref, link in links.items():
        text = text.replace(f"<{ref}>", link).replace(ref, link)
    if result.changed:
        atomic_write_text(post_path, text)
    return result
