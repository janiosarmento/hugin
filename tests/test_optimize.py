import shutil
import subprocess
from pathlib import Path

import pytest

from hugin import optimize, resize

pytestmark = pytest.mark.skipif(shutil.which("magick") is None, reason="magick not installed")

WIDTHS = [480, 800]


def make_image(path: Path, width: int, height: int = 300, kind: str = "plasma:") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["magick", "-size", f"{width}x{height}", kind, str(path)], check=True)


@pytest.fixture
def site(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr("hugin.trash.TRASH_DIR", tmp_path / "trash")
    (tmp_path / "static" / "images").mkdir(parents=True)
    (tmp_path / "content" / "posts").mkdir(parents=True)
    return tmp_path


def post(site: Path, name: str, body: str, featured: str = "") -> Path:
    path = site / "content" / "posts" / name
    front = f"thumbnail: {featured}\n" if featured else ""
    path.write_text(f"---\ntitle: Café\n{front}---\n\n{body}\n", encoding="utf-8")
    return path


def run(site: Path, posts: list[Path]) -> optimize.Result:
    items, errors = optimize.plan(posts, site / "static", WIDTHS)
    assert errors == []
    return optimize.apply(items, site / "content")


def light_files(folder: Path, stem: str) -> dict[str, Path]:
    """{"": full size, "-480w": …, "-800w": …} for the converted files of `stem`."""
    found = {}
    for suffix in ("", "-480w", "-800w"):
        hits = [folder / f"{stem}{suffix}{ext}" for ext in resize.LIGHT_FORMATS
                if (folder / f"{stem}{suffix}{ext}").exists()]
        assert len(hits) <= 1, f"more than one format kept for {stem}{suffix}: {hits}"
        if hits:
            found[suffix] = hits[0]
    return found


def test_png_becomes_lightest_format_in_every_size(site: Path) -> None:
    make_image(site / "static" / "images" / "cat.png", 1440)
    path = post(site, "cat.md", "![A cat](/images/cat.png)", featured="/images/cat.png")

    result = run(site, [path])

    files = light_files(site / "static" / "images", "cat")
    assert set(files) == {"", "-480w", "-800w"}
    assert resize.image_width(files[""]) == 1200  # MAX_WIDTH: the 1440px PNG was reduced first
    assert resize.image_width(files["-800w"]) == 800
    assert resize.image_width(files["-480w"]) == 480
    assert not (site / "static" / "images" / "cat.png").exists()
    assert len(list((site / "trash").iterdir())) == 1
    text = path.read_text(encoding="utf-8")
    new = f"/images/{files[''].name}"
    assert f"thumbnail: {new}\n" in text and f"![A cat]({new})" in text
    assert ".png" not in text
    assert result.converted == [("/images/cat.png", new)]
    assert result.variants == 2
    assert not list(site.rglob("*.part"))


def test_kept_file_is_the_smaller_one(site: Path, monkeypatch) -> None:
    sizes = {".avif": 900, ".webp": 100}
    real = resize._magick

    def fake(source, out_ext, width, out):
        real(source, out_ext, width, out)
        out.write_bytes(b"x" * sizes[out_ext])

    monkeypatch.setattr(resize, "_magick", fake)
    make_image(site / "static" / "images" / "cat.png", 1000)
    path = post(site, "cat.md", "![](/images/cat.png)")

    run(site, [path])

    assert (site / "static" / "images" / "cat.webp").exists()
    assert not (site / "static" / "images" / "cat.avif").exists()


def test_never_upscales_a_converted_image(site: Path) -> None:
    make_image(site / "static" / "images" / "small.png", 600)
    path = post(site, "small.md", "![](/images/small.png)")

    run(site, [path])

    assert set(light_files(site / "static" / "images", "small")) == {"", "-480w"}


def test_other_posts_linking_the_image_follow_it(site: Path) -> None:
    make_image(site / "static" / "images" / "cat.png", 900)
    mine = post(site, "cat.md", "![](/images/cat.png)")
    translation = post(site, "cat.en.md", '<img src="https://example.com/images/cat.png" alt="x">')
    unrelated = post(site, "dog.md", "![](/images/other/cat.png)")
    before = unrelated.read_text(encoding="utf-8")

    items, _ = optimize.plan([mine], site / "static", WIDTHS)
    result = optimize.apply(items, site / "content", base_url="https://example.com/")

    name = light_files(site / "static" / "images", "cat")[""].name
    assert f'src="https://example.com/images/{name}"' in translation.read_text(encoding="utf-8")
    assert unrelated.read_text(encoding="utf-8") == before
    assert sorted(result.relinked) == sorted([mine, translation])


def test_name_clash_picks_a_free_stem(site: Path) -> None:
    make_image(site / "static" / "images" / "cat.png", 900)
    make_image(site / "static" / "images" / "cat.avif", 300, kind="xc:orange")  # another image
    path = post(site, "cat.md", "![](/images/cat.png)")

    run(site, [path])

    assert resize.image_width(site / "static" / "images" / "cat.avif") == 300
    assert set(light_files(site / "static" / "images", "cat-2")) == {"", "-480w", "-800w"}
    assert "/images/cat-2." in path.read_text(encoding="utf-8")


def test_light_images_only_get_missing_variants(site: Path) -> None:
    make_image(site / "static" / "images" / "cat.webp", 1440)
    make_image(site / "static" / "images" / "cat-480w.webp", 480)
    path = post(site, "cat.md", "![](/images/cat.webp)")
    before = path.read_text(encoding="utf-8")

    result = run(site, [path])

    assert (site / "static" / "images" / "cat-800w.webp").exists()
    assert not (site / "static" / "images" / "cat-800w.avif").exists()
    assert path.read_text(encoding="utf-8") == before
    assert (site / "static" / "images" / "cat.webp").exists()
    assert result.variants == 1


def test_nothing_to_do_plans_nothing(site: Path) -> None:
    make_image(site / "static" / "images" / "tiny.avif", 300)
    make_image(site / "static" / "images" / "cat.avif", 1200)
    for w in WIDTHS:
        make_image(site / "static" / "images" / f"cat-{w}w.avif", w)
    path = post(site, "cat.md", "![](/images/tiny.avif) ![](/images/cat.avif) ![](https://other.org/a.png)")

    assert optimize.plan([path], site / "static", WIDTHS) == ([], [])


def test_failed_conversion_keeps_the_png_and_leaves_nothing(site: Path, monkeypatch) -> None:
    make_image(site / "static" / "images" / "cat.png", 900)
    path = post(site, "cat.md", "![](/images/cat.png)")
    before = path.read_text(encoding="utf-8")
    real = resize._magick

    def failing(source, out_ext, width, out):
        if width == 800:
            raise RuntimeError("encoder crashed")
        real(source, out_ext, width, out)

    monkeypatch.setattr(resize, "_magick", failing)
    items, _ = optimize.plan([path], site / "static", WIDTHS)
    result = optimize.apply(items, site / "content")

    assert result.errors and "encoder crashed" in result.errors[0]
    assert sorted(p.name for p in (site / "static" / "images").iterdir()) == ["cat.png"]
    assert path.read_text(encoding="utf-8") == before


def test_unreadable_image_is_reported(site: Path) -> None:
    (site / "static" / "images" / "broken.png").write_bytes(b"not an image")
    path = post(site, "b.md", "![](/images/broken.png)")

    items, errors = optimize.plan([path], site / "static", WIDTHS)

    assert items == [] and errors == ["/images/broken.png: not a readable image"]


def test_no_widths_converts_full_size_only(site: Path) -> None:
    make_image(site / "static" / "images" / "cat.png", 900)
    path = post(site, "cat.md", "![](/images/cat.png)")

    items, _ = optimize.plan([path], site / "static", [])
    optimize.apply(items, site / "content")

    assert set(light_files(site / "static" / "images", "cat")) == {""}


def test_converted_name_needs_no_url_encoding(site: Path) -> None:
    make_image(site / "static" / "images" / "Café Naïve, Shot 2.png", 900)
    path = post(site, "cat.md", "![](/images/Caf%C3%A9%20Na%C3%AFve,%20Shot%202.png)",
                featured='"/images/Café Naïve, Shot 2.png"')

    run(site, [path])

    files = light_files(site / "static" / "images", "cafe-naive-shot-2")
    assert set(files) == {"", "-480w", "-800w"}
    text = path.read_text(encoding="utf-8")
    new = f"/images/{files[''].name}"
    assert f"![]({new})" in text
    assert f'thumbnail: "{new}"' in text
    assert "%" not in text and "Naïve" not in text


def test_light_image_with_odd_name_is_renamed_with_its_variants(site: Path) -> None:
    folder = site / "static" / "images"
    make_image(folder / "Meu Gato.avif", 1440, kind="xc:orange")
    make_image(folder / "Meu Gato-480w.avif", 480, kind="xc:orange")
    path = post(site, "cat.md", "![](/images/Meu%20Gato.avif)")
    other = post(site, "cat.en.md", '<img src="/images/Meu Gato.avif">')

    result = run(site, [path])

    assert sorted(p.name for p in folder.iterdir()) == ["meu-gato-480w.avif", "meu-gato-800w.avif", "meu-gato.avif"]
    assert "![](/images/meu-gato.avif)" in path.read_text(encoding="utf-8")
    assert '<img src="/images/meu-gato.avif">' in other.read_text(encoding="utf-8")
    assert result.renamed == [("/images/Meu Gato.avif", "/images/meu-gato.avif")]
    assert result.variants == 1


def test_case_only_rename_is_not_a_clash(site: Path) -> None:
    folder = site / "static" / "images"
    make_image(folder / "Gato.webp", 300, kind="xc:orange")
    path = post(site, "cat.md", "![](/images/Gato.webp)")

    run(site, [path])

    assert [p.name for p in folder.iterdir()] == ["gato.webp"]
    assert "![](/images/gato.webp)" in path.read_text(encoding="utf-8")


def test_rename_never_overwrites_another_image(site: Path) -> None:
    folder = site / "static" / "images"
    make_image(folder / "meu-gato.avif", 300, kind="xc:blue")
    make_image(folder / "Meu Gato.avif", 400, kind="xc:orange")
    path = post(site, "cat.md", "![](/images/Meu%20Gato.avif)")

    run(site, [path])

    assert resize.image_width(folder / "meu-gato.avif") == 300
    assert resize.image_width(folder / "meu-gato-2.avif") == 400
    assert "![](/images/meu-gato-2.avif)" in path.read_text(encoding="utf-8")


def test_link_safe_light_image_with_all_variants_is_left_alone(site: Path) -> None:
    folder = site / "static" / "images"
    make_image(folder / "gato.avif", 300, kind="xc:orange")
    path = post(site, "cat.md", "![](/images/gato.avif)")

    assert optimize.plan([path], site / "static", WIDTHS) == ([], [])


def test_wide_light_image_is_reduced_in_place_before_variants(site: Path) -> None:
    folder = site / "static" / "images"
    make_image(folder / "cat.avif", 2400)
    path = post(site, "cat.md", "![](/images/cat.avif)")
    before = path.read_text(encoding="utf-8")

    result = run(site, [path])

    assert sorted(p.name for p in folder.iterdir()) == ["cat-480w.avif", "cat-800w.avif", "cat.avif"]
    assert resize.image_width(folder / "cat.avif") == 1200
    assert path.read_text(encoding="utf-8") == before
    assert result.shrunk == 1 and result.variants == 2


def test_image_at_the_limit_is_not_touched(site: Path) -> None:
    folder = site / "static" / "images"
    make_image(folder / "cat.webp", 1200)
    for w in WIDTHS:
        make_image(folder / f"cat-{w}w.webp", w)

    assert optimize.plan([post(site, "cat.md", "![](/images/cat.webp)")], site / "static", WIDTHS) == ([], [])


def test_wide_animated_gif_keeps_its_frames(site: Path) -> None:
    folder = site / "static" / "images"
    subprocess.run(["magick", "-size", "1600x200", "xc:red", "xc:blue", "-set", "delay", "20",
                    str(folder / "anim.gif")], check=True)
    path = post(site, "anim.md", "![](/images/anim.gif)")

    run(site, [path])

    frames = subprocess.run(["magick", "identify", "-format", "%w\n", str(folder / "anim.gif")],
                            capture_output=True, text=True, check=True).stdout.split()
    assert frames == ["1200", "1200"]
    assert (folder / "anim-800w.gif").exists()


def test_small_png_keeps_its_width(site: Path) -> None:
    make_image(site / "static" / "images" / "cat.png", 1000)
    path = post(site, "cat.md", "![](/images/cat.png)")

    result = run(site, [path])

    assert resize.image_width(light_files(site / "static" / "images", "cat")[""]) == 1000
    assert result.shrunk == 0
