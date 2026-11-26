import shutil
import subprocess
from pathlib import Path

import pytest

from hugin import resize

pytestmark = pytest.mark.skipif(shutil.which("magick") is None, reason="magick not installed")


def make_image(path: Path, width: int, height: int = 100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["magick", "-size", f"{width}x{height}", "xc:orange", str(path)], check=True)


def width_of(path: Path) -> int:
    return resize.image_width(path)


@pytest.fixture
def site(tmp_path: Path) -> Path:
    (tmp_path / "config" / "_default").mkdir(parents=True)
    (tmp_path / "config" / "_default" / "params.toml").write_text(
        "[imageProcessing.content]\n  enabled = true\n  widths = [800, 480]\n"
    )
    (tmp_path / "static").mkdir()
    return tmp_path


def test_creates_variants_keeping_format(site: Path) -> None:
    for ext in ("avif", "webp", "png", "jpg", "gif"):
        make_image(site / "static" / "img" / f"cat.{ext}", 1440)

    assert resize.main([str(site)]) == 0

    for ext in ("avif", "webp", "png", "jpg", "gif"):
        for w in (480, 800):
            variant = site / "static" / "img" / f"cat-{w}w.{ext}"
            assert variant.exists(), variant
            assert width_of(variant) == w
    assert not list((site / "static").rglob("*.part"))


def test_never_upscales(site: Path) -> None:
    make_image(site / "static" / "small.avif", 600)

    assert resize.main([str(site)]) == 0

    assert (site / "static" / "small-480w.avif").exists()
    assert not (site / "static" / "small-800w.avif").exists()


def test_keeps_existing_variants_and_skips_variant_files(site: Path) -> None:
    make_image(site / "static" / "cat.webp", 1440)
    make_image(site / "static" / "cat-480w.webp", 123)  # hand-made, must stay untouched

    assert resize.main([str(site)]) == 0

    assert width_of(site / "static" / "cat-480w.webp") == 123
    assert (site / "static" / "cat-800w.webp").exists()
    assert not (site / "static" / "cat-480w-480w.webp").exists()


def test_dry_run_writes_nothing(site: Path) -> None:
    make_image(site / "static" / "cat.avif", 1440)

    assert resize.main([str(site), "--dry-run"]) == 0

    assert sorted(p.name for p in (site / "static").iterdir()) == ["cat.avif"]


def test_removes_partial_leftovers(site: Path) -> None:
    make_image(site / "static" / "cat.avif", 1440)
    (site / "static" / "cat-480w.avif.part").write_bytes(b"broken")

    assert resize.main([str(site)]) == 0

    assert not (site / "static" / "cat-480w.avif.part").exists()
    assert width_of(site / "static" / "cat-480w.avif") == 480


def test_broken_image_reports_failure(site: Path) -> None:
    (site / "static" / "broken.avif").write_bytes(b"not an image")

    assert resize.main([str(site)]) == 1
    assert not (site / "static" / "broken-480w.avif").exists()


def test_widths_come_from_params(site: Path) -> None:
    assert resize.load_widths(site) == [480, 800]
    (site / "config" / "_default" / "params.toml").write_text("[sidebar]\n")
    with pytest.raises(SystemExit):
        resize.load_widths(site)
