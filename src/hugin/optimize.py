"""A post's images made light before they reach the blog.

An image the author drops in as PNG (or JPG, TIFF…) becomes AVIF or WebP,
whichever is smaller, at its own width and at each width the theme's
params.toml lists in [imageProcessing.content] widths (cat.avif,
cat-800w.webp, cat-480w.avif…). Every size picks its own format: the theme
looks for each width as .avif, then .webp. Links in every post that uses the
image move to the new file, and the original goes to Hugin's trash.

Images already in AVIF, WebP or GIF keep their format and only get the
width variants they lack. No image is ever made wider than it is, and
none stays wider than MAX_WIDTH: a wider one is reduced first, before
anything else is done with it.

Every file ends up with a name no link has to escape (images.slug():
"Foto do Gato.png" -> foto-do-gato.avif); a light image with another
name is renamed, its existing variants with it.

plan() is quick (it only reads widths); apply() does the encoding.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from hugin import images, resize
from hugin.fsutil import atomic_write_text
from hugin.trash import move_to_trash

CONVERT = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".heic")
KEEP = (".avif", ".webp", ".gif")
MAX_WIDTH = 1200  # no image on the blog is wider (kpar's limit)


@dataclass
class Item:
    source: Path  # the file in the static folder
    link: str  # its site path, "/images/cat.png"
    convert: bool  # to the lighter of AVIF/WebP; else only variants in its own format
    widths: list[int]  # variants to create, all narrower than the original
    rename: bool = False  # a light image whose name is not link-safe
    sizes: list[int] = field(default_factory=list)  # every configured width: variants that move with a rename
    shrink: bool = False  # wider than MAX_WIDTH: reduced to it first


@dataclass
class Result:
    converted: list[tuple[str, str]] = field(default_factory=list)  # (old site path, new site path)
    renamed: list[tuple[str, str]] = field(default_factory=list)  # light images given a link-safe name
    variants: int = 0
    shrunk: int = 0  # images reduced to MAX_WIDTH
    relinked: list[Path] = field(default_factory=list)  # posts whose links changed
    errors: list[str] = field(default_factory=list)


def _site_path(ref: str, base_url: str) -> str | None:
    """images.site_path() that keeps a raw space ("/images/My Cat.png", as <img src> or <…> may hold)."""
    return images.site_path(ref.strip().replace(" ", "%20"), base_url)


def _static_file(link: str, static_root: Path) -> Path | None:
    path = (static_root / link.lstrip("/")).resolve()
    return path if path.is_relative_to(static_root.resolve()) and path.is_file() else None


def plan(
    posts: list[Path], static_root: Path, widths: list[int], base_url: str = "",
) -> tuple[list[Item], list[str]]:
    """(the work the posts' images need, images that could not be read)."""
    items: list[Item] = []
    errors: list[str] = []
    seen: set[Path] = set()
    for post in posts:
        try:
            text = post.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        refs, _, _ = images._parse(text, base_url)
        for link in dict.fromkeys(p for p in (_site_path(r, base_url) for r in refs) if p):
            source = _static_file(link, static_root)
            if source is None or source in seen:
                continue
            seen.add(source)
            ext = source.suffix.lower()
            if ext in CONVERT:
                missing, convert, rename = widths, True, False
            elif ext in KEEP and not resize.is_variant(source, widths):
                missing = [w for w in widths if not resize.variant_path(source, w).exists()]
                convert, rename = False, images.safe_name(source.name) != source.name
            else:
                continue
            try:
                original = resize.image_width(source)
            except (subprocess.CalledProcessError, ValueError, IndexError):
                errors.append(f"{link}: not a readable image")
                continue
            shrink = original > MAX_WIDTH
            narrower = [w for w in missing if w < min(original, MAX_WIDTH)]
            if convert or narrower or rename or shrink:
                items.append(Item(source, link, convert, narrower, rename, widths, shrink))
    return items, errors


def _own_files(source: Path, widths: list[int]) -> list[Path]:
    """The source and the width variants it already has."""
    return [source, *(v for w in widths if (v := resize.variant_path(source, w)).exists())]


def _free_stem(source: Path, widths: list[int], exts: tuple[str, ...]) -> str:
    """slug() of the source's name, or slug-2, slug-3… when a file it would write is taken.

    The source's own files do not count: on a case-insensitive disk Cat.avif
    is the file cat.avif would name.
    """
    own = _own_files(source, widths)
    base, n = images.slug(source.stem), 1
    stem = base

    def taken(path: Path) -> bool:
        return path.exists() and not any(path.samefile(o) for o in own)

    while any(
        taken(source.parent / f"{name}{ext}")
        for name in [stem, *(f"{stem}-{w}w" for w in widths)]
        for ext in exts
    ):
        n += 1
        stem = f"{base}-{n}"
    return stem


def _rename(source: Path, widths: list[int]) -> Path:
    """Move a light image and its existing variants to a link-safe name; the new path."""
    stem = _free_stem(source, widths, (source.suffix.lower(),))
    target = source.with_name(f"{stem}{source.suffix.lower()}")
    for width in widths:
        old = resize.variant_path(source, width)
        if old.exists():
            old.rename(resize.variant_path(target, width))
    source.rename(target)
    return target


def relink(content_dir: Path, old: str, new: str, base_url: str = "") -> list[Path]:
    """Point every post under `content_dir` that links site path `old` to `new`; the posts changed.

    Each reference keeps the way it was written (full URL, percent-encoding);
    only its file name changes.
    """
    old_name = old.rsplit("/", 1)[-1]
    new_name = quote(new.rsplit("/", 1)[-1])
    changed = []
    for path in sorted(content_dir.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if old_name not in text and quote(old_name) not in text:
            continue
        refs, _, _ = images._parse(text, base_url)
        new_text = text
        for ref in refs:
            if _site_path(ref, base_url) != old:
                continue
            target = ref.strip()
            replacement = target.rsplit("/", 1)[0] + "/" + new_name
            new_text = re.sub(rf"(?<![\w/.%-]){re.escape(target)}(?![\w/.%-])",
                              lambda _m: replacement, new_text)
        if new_text != text:
            atomic_write_text(path, new_text)
            changed.append(path)
    return changed


def _convert(item: Item) -> list[Path]:
    """The light files for `item`, the full-size one first; none left behind on failure."""
    stem = _free_stem(item.source, item.widths, resize.LIGHT_FORMATS)
    folder = item.source.parent
    created: list[Path] = []
    try:
        full = MAX_WIDTH if item.shrink else None  # straight from the original: one encoding, not two
        created.append(resize.write_lightest(item.source, full, folder / stem))
        for width in item.widths:
            created.append(resize.write_lightest(item.source, width, folder / f"{stem}-{width}w"))
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return created


def _relink(result: Result, content_dir: Path, old: str, new: str, base_url: str) -> None:
    for post in relink(content_dir, old, new, base_url):
        if post not in result.relinked:
            result.relinked.append(post)


def apply(
    items: list[Item], content_dir: Path, base_url: str = "", progress=None,
) -> Result:
    """Do the work plan() found. `progress(done, total, name)` is called before each image."""
    result = Result()
    for done, item in enumerate(items):
        if progress is not None:
            progress(done, len(items), item.source.name)
        try:
            if item.convert:
                created = _convert(item)
                new = item.link.rsplit("/", 1)[0] + "/" + created[0].name
                _relink(result, content_dir, item.link, new, base_url)
                move_to_trash(item.source)
                result.converted.append((item.link, new))
                result.variants += len(created) - 1
                result.shrunk += item.shrink
                continue
            source = item.source
            if item.shrink:
                resize.shrink_in_place(source, MAX_WIDTH)
                result.shrunk += 1
            if item.rename:
                source = _rename(source, item.sizes)
                new = item.link.rsplit("/", 1)[0] + "/" + source.name
                _relink(result, content_dir, item.link, new, base_url)
                result.renamed.append((item.link, new))
            for width in item.widths:
                resize.resize(source, width)
                result.variants += 1
        except (RuntimeError, OSError, subprocess.CalledProcessError) as e:
            message = str(e).strip().splitlines()
            result.errors.append(f"{item.link}: {message[0] if message else repr(e)}")
    return result
