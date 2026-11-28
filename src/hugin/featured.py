"""The featured image, edited in Typora as the first line of the post's body.

Typora does not show front matter values as images, so while Hugin has a
post open in Typora, show() puts the featured image (or a line asking for
one) at the top of the body, and a front matter key marking it. On return,
take() reads that first paragraph back: when it holds only images, the last
one becomes the featured image and the paragraph leaves the body. The marker
key makes this work even when the return was missed (git sync runs take()
on any post that still has it).

Which front matter field holds the featured image (`thumbnail`, `image`…)
is resolve()d: hugin/config.toml [images] featured, else the field the
blog's posts use (detect_field()), else the one the theme's templates read
(theme_field(), for a blog whose posts have none yet), else `thumbnail`.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from hugin.fsutil import atomic_write_text
from hugin.images import IMAGE_EXTENSIONS, _yaml_front_matter

# Front matter fields themes commonly read the featured image from
COVER_FIELDS = ("thumbnail", "image", "cover", "featured_image", "featureImage")

MARKER_KEY = "hugin-featured-image"  # value: the front matter field
ALT = "Featured image"
PLACEHOLDER = "Featured image: drop one on this line"

_MARKER_LINE = re.compile(rf"^{MARKER_KEY}\s*:\s*(\S+)[ \t]*\n?", re.MULTILINE)
# A whole image, Markdown or HTML (Typora writes <img> for a resized one)
_IMAGE = re.compile(
    r"!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^)\s]+))[^)]*\)"
    r"|<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"'][^>]*>",
    re.IGNORECASE,
)

# Images (or the request for one) at the very start of a paragraph
_LEADING = re.compile(rf"(?:[ \t]*(?:{_IMAGE.pattern}|{re.escape(PLACEHOLDER)})[ \t]*\n?)+", re.IGNORECASE)
_OWN_IMAGE = re.compile(rf"!\[{re.escape(ALT)}\]\(", re.IGNORECASE)


def _split_featured_line(paragraph: str) -> tuple[str, str]:
    """(featured line, text joined to it) for a first paragraph.

    Typora may glue the paragraph after the featured line onto it. The images
    at its start are the featured line when they include show()'s own (its alt
    text, or the request for one); otherwise the whole paragraph is the line.
    """
    m = _LEADING.match(paragraph)
    if m and paragraph[m.end():].strip() and (_OWN_IMAGE.search(m.group()) or PLACEHOLDER in m.group()):
        return paragraph[:m.end()], paragraph[m.end():].lstrip()
    return paragraph, ""


def detect_field(posts) -> str | None:
    """The top-level front matter field most posts use for an image path, or None."""
    counts = Counter(
        key for post in posts for key, value in post.metadata.items()
        if isinstance(value, str) and value.strip().lower().endswith(IMAGE_EXTENSIONS)
    )
    return counts.most_common(1)[0][0] if counts else None


_THEME_PARAM = re.compile(r"\.Params\.(\w+)")


def theme_field(site_root: Path) -> str | None:
    """The cover field the site's templates read most (`.Params.image`…), or None.

    Looks in the site's own layouts/ and in each themes/*/layouts/.
    """
    names = {name.lower(): name for name in COVER_FIELDS}  # Hugo params ignore case
    counts: Counter[str] = Counter()
    folders = [site_root / "layouts", *sorted(site_root.glob("themes/*/layouts"))]
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            counts.update(names[m.lower()] for m in _THEME_PARAM.findall(text) if m.lower() in names)
    return counts.most_common(1)[0][0] if counts else None


def resolve(configured: str, posts, site_root: Path) -> str:
    """The featured image field: the configured one, the posts' own, the theme's, else thumbnail."""
    return configured or detect_field(posts) or theme_field(site_root) or "thumbnail"


def field_for(directory: Path, posts) -> str:
    """The blog's featured image field, with the settings in hugin/config.toml (see resolve())."""
    from hugin.hugo import find_site_root
    from hugin.project import load_project

    return resolve(load_project(directory).images.featured, posts, find_site_root(directory))


def _body_start(text: str, block: tuple[int, int]) -> int:
    newline = text.find("\n", block[1])
    return len(text) if newline == -1 else newline + 1


def _yaml_value(value: str) -> str:
    return value if re.fullmatch(r"[\w/.~%+-]+", value) else json.dumps(value)


def _field_value(front: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}\s*:[ \t]*(.*)$", front, re.MULTILINE)
    if not m:
        return ""
    value = m.group(1).strip()
    if value[:1] in ("'", '"'):
        try:
            import yaml

            value = str(yaml.safe_load(value) or "")
        except Exception:
            value = value.strip("'\"")
    return value


def show(post_path: Path, key: str) -> bool:
    """Put the featured image (or a line asking for one) first in the body; whether it did.

    Only in YAML front matter, and never twice.
    """
    try:
        text = post_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    block = _yaml_front_matter(text)
    if block is None or _MARKER_LINE.search(text, *block):
        return False
    value = _field_value(text[block[0]:block[1]], key)
    line = f"![{ALT}]({value})" if value else PLACEHOLDER
    start = _body_start(text, block)
    new = (text[:block[0]] + f"{MARKER_KEY}: {key}\n" + text[block[0]:start]
           + f"{line}\n\n" + text[start:])
    atomic_write_text(post_path, new)
    return True


@dataclass
class Taken:
    field: str
    old: str
    new: str  # the featured image now; == old when unchanged
    found: bool  # the first paragraph was the featured line
    unsafe: str = ""  # an image left in the body: its link is not a site path

    @property
    def changed(self) -> bool:
        return self.new != self.old


def _site_link(link: str) -> bool:
    if link.startswith(("http://", "https://")):
        return True
    return link.startswith("/") and ".." not in link.split("/") and not link.startswith(str(Path.home()))


def _set_field(front: str, key: str, value: str) -> str:
    """`front` (the YAML between the fences) with `key` set to `value`, nothing else touched."""
    lines = front.split("\n")
    entry = f"{key}: {_yaml_value(value)}"
    start = next((i for i, line in enumerate(lines) if re.match(rf"{re.escape(key)}\s*:", line)), None)
    if start is None:
        at = len(lines) - 1 if lines and lines[-1] == "" else len(lines)
        lines[at:at] = [entry]
    else:
        stop = start + 1
        while stop < len(lines) and lines[stop][:1].isspace() and lines[stop].strip():
            stop += 1
        lines[start:stop] = [entry]
    return "\n".join(lines)


def take(post_path: Path) -> Taken | None:
    """Read the featured line back into the front matter; None when show() had not run."""
    try:
        text = post_path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError):
        return None
    block = _yaml_front_matter(text)
    marker = _MARKER_LINE.search(text, *block) if block else None
    if marker is None:
        return None
    key = marker.group(1)
    text = text[:marker.start()] + text[marker.end():]
    block = _yaml_front_matter(text)
    front = text[block[0]:block[1]]
    old = _field_value(front, key)
    start = _body_start(text, block)
    body = text[start:]

    lead = len(body) - len(body.lstrip("\n"))
    rest = body[lead:]
    end = rest.find("\n\n")
    paragraph = rest if end == -1 else rest[:end]
    line, joined = _split_featured_line(paragraph)
    links = [next(g for g in m.groups() if g).strip() for m in _IMAGE.finditer(line)]
    leftover = _IMAGE.sub("", line).replace(PLACEHOLDER, "").replace(f"![{ALT}]()", "")
    result = Taken(key, old, old, found=not leftover.strip())
    if not result.found:
        # The line was edited into text: only the request for an image goes
        body = body[:lead] + rest.replace(PLACEHOLDER, "", 1).lstrip(" ")
    elif links and not _site_link(links[-1]) and links[-1] != old:  # the field's own value is safe to drop
        result.unsafe = links[-1]  # kept in the body: the image would be lost otherwise
    else:
        result.new = links[-1] if links else old
        after = rest[len(paragraph):]
        if joined:  # the line goes, the text glued to it stays the first paragraph
            body = body[:lead] + joined + after
        else:  # show() added the line and one blank line; the body's own start is kept
            body = body[:lead] + (after[2:] if after.startswith("\n\n") else after.lstrip("\n"))
        if result.changed:
            front = _set_field(front, key, result.new)
    atomic_write_text(post_path, text[:block[0]] + front + text[block[1]:start] + body)
    return result


def posts_with_marker(content_dir: Path) -> list[Path]:
    """Markdown files under `content_dir` whose front matter still marks a featured line."""
    found = []
    for path in sorted(content_dir.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        block = _yaml_front_matter(text)
        if MARKER_KEY in text and block and _MARKER_LINE.search(text, *block):
            found.append(path)
    return found
