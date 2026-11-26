"""Shift+E opens the current post in $EDITOR and reloads it afterwards."""

from contextlib import contextmanager
from pathlib import Path

import pytest

from hugin.tui import review
from hugin.tui.review import STATE_BROWSING, STATE_LOADING, STATE_REVIEWING, HuginScreen


class FakeApp:
    def __init__(self):
        self.screens = []

    @contextmanager
    def suspend(self):
        yield

    def push_screen(self, screen, callback=None):
        self.screens.append(screen)
        screen.dismiss = lambda *a: self.screens.remove(screen)  # no running app to pop it

    def call_from_thread(self, fn, *args):
        return fn(*args)


class FakeScreen:
    action_external_editor = HuginScreen.action_external_editor
    _adopt_inbox_images = HuginScreen._adopt_inbox_images
    _image_settings = HuginScreen._image_settings
    _static_root = HuginScreen._static_root
    _base_url = HuginScreen._base_url
    _featured_field = HuginScreen._featured_field
    _take_featured = HuginScreen._take_featured
    _optimize_images = HuginScreen._optimize_images

    def run_worker(self, work, **_):
        work()

    def __init__(self, post_path: Path):
        self._state = STATE_BROWSING
        self.posts = [type("Post", (), {"path": post_path})()]
        self.directory = post_path.parent
        self.site = type("Site", (), {"config": {}})()
        self.current_index = 0
        self.all_posts = []
        self.app = FakeApp()
        self.notices: list[tuple[str, str]] = []
        self.reloaded: list[int] = []
        self.details_updated = 0

    def notify(self, message, *, severity="information", **_):
        self.notices.append((message, severity))

    def _reload_post(self, post, index):
        self.reloaded.append(index)

    def _update_detail_panel(self):
        self.details_updated += 1


@pytest.fixture(autouse=True)
def no_hugin_editor(monkeypatch):
    monkeypatch.delenv("HUGIN_EDITOR", raising=False)


def test_warns_when_editor_is_not_set(monkeypatch, tmp_path):
    monkeypatch.delenv("EDITOR", raising=False)
    ran = []
    monkeypatch.setattr(review.subprocess, "run", lambda *a, **k: ran.append(a))
    screen = FakeScreen(tmp_path / "post.md")

    screen.action_external_editor()

    assert ran == []
    assert screen.reloaded == []
    assert screen.notices and screen.notices[0][1] == "warning"
    assert "$EDITOR" in screen.notices[0][0]


def test_blank_editor_counts_as_unset(monkeypatch, tmp_path):
    monkeypatch.setenv("EDITOR", "   ")
    ran = []
    monkeypatch.setattr(review.subprocess, "run", lambda *a, **k: ran.append(a))
    screen = FakeScreen(tmp_path / "post.md")

    screen.action_external_editor()

    assert ran == []
    assert screen.notices[0][1] == "warning"


def test_runs_editor_with_arguments_then_reloads(monkeypatch, tmp_path):
    post_path = tmp_path / "post.md"
    monkeypatch.setenv("EDITOR", "code --wait")
    ran = []
    monkeypatch.setattr(review.subprocess, "run", lambda cmd, **k: ran.append(cmd))
    screen = FakeScreen(post_path)

    screen.action_external_editor()

    assert ran == [["code", "--wait", str(post_path)]]
    assert screen.reloaded == [0]
    assert screen.details_updated == 1
    assert screen.notices == []


@pytest.mark.parametrize("state", [STATE_LOADING, STATE_REVIEWING])
def test_ignored_outside_browsing(monkeypatch, tmp_path, state):
    monkeypatch.setenv("EDITOR", "hx")
    ran = []
    monkeypatch.setattr(review.subprocess, "run", lambda *a, **k: ran.append(a))
    screen = FakeScreen(tmp_path / "post.md")
    screen._state = state

    screen.action_external_editor()

    assert ran == []
    assert screen.reloaded == []


def test_hugin_editor_wins_over_editor(monkeypatch, tmp_path, images_configured):
    post_path = tmp_path / "post.md"
    monkeypatch.setenv("EDITOR", "hx")
    monkeypatch.setenv("HUGIN_EDITOR", "open -W -n -a Typora")
    ran = []
    monkeypatch.setattr(review.subprocess, "run", lambda cmd, **k: ran.append(cmd))
    screen = FakeScreen(post_path)

    screen.action_external_editor()

    assert ran == [["open", "-W", "-n", "-a", "Typora", str(post_path)]]
    assert screen.reloaded == [0]


@pytest.fixture
def images_configured(monkeypatch):
    from hugin.project import ImagesSettings, ProjectConfig

    monkeypatch.setattr(review, "load_project", lambda d: ProjectConfig(
        images=ImagesSettings("static/images", "/images", "YYYY/MM")))


def test_typora_gets_its_keys_only_while_it_is_open(monkeypatch, tmp_path, images_configured):
    post_path = tmp_path / "post.md"
    original = "---\ntitle: T\ndate: 2026-11-22\n---\n![a](/images/canetas/a.png)\n"
    post_path.write_text(original)
    monkeypatch.setenv("HUGIN_EDITOR", "open -W -n -a Typora")
    seen = []
    monkeypatch.setattr(review.subprocess, "run", lambda cmd, **k: seen.append(post_path.read_text()))
    screen = FakeScreen(post_path)

    screen.action_external_editor()

    assert f'typora-root-url: {tmp_path / "static"}\n' in seen[0]
    # Pasted images go straight to the folder the post's images use
    assert f'typora-copy-images-to: {tmp_path / "static/images/canetas"}\n' in seen[0]
    assert (tmp_path / "static/images/canetas").is_dir()
    assert post_path.read_text() == original
    assert screen.reloaded == [0]


def test_typora_shows_the_featured_image_atop_the_body(monkeypatch, tmp_path, images_configured):
    post_path = tmp_path / "post.md"
    original = "---\ntitle: T\ndate: 2026-11-22\nthumbnail: /images/old.avif\n---\nBody\n"
    post_path.write_text(original)
    monkeypatch.setenv("HUGIN_EDITOR", "open -W -n -a Typora")

    def typora(cmd, **k):
        # The author drops a new image on the featured line
        post_path.write_text(post_path.read_text().replace("](/images/old.avif)", "](/images/new.avif)"))

    monkeypatch.setattr(review.subprocess, "run", typora)
    screen = FakeScreen(post_path)
    screen.all_posts = [type("Post", (), {"metadata": {"thumbnail": "/images/x.avif"}})()]

    screen.action_external_editor()

    assert post_path.read_text() == original.replace("/images/old.avif", "/images/new.avif")
    assert ("Featured image (thumbnail): /images/new.avif", "information") in screen.notices
    assert screen.reloaded == [0]


def test_other_editors_get_no_preview_root(monkeypatch, tmp_path):
    post_path = tmp_path / "post.md"
    post_path.write_text("---\ntitle: T\n---\nBody\n")
    monkeypatch.setenv("EDITOR", "hx")
    seen = []
    monkeypatch.setattr(review.subprocess, "run", lambda cmd, **k: seen.append(post_path.read_text()))
    FakeScreen(post_path).action_external_editor()
    assert "typora-root-url" not in seen[0]


def test_preview_root_is_removed_even_when_the_editor_fails(monkeypatch, tmp_path, images_configured):
    post_path = tmp_path / "post.md"
    original = "---\ntitle: T\n---\nBody\n"
    post_path.write_text(original)
    monkeypatch.setenv("HUGIN_EDITOR", "typora")

    def boom(cmd, **k):
        raise FileNotFoundError("typora")

    monkeypatch.setattr(review.subprocess, "run", boom)
    with pytest.raises(FileNotFoundError):
        FakeScreen(post_path).action_external_editor()
    assert post_path.read_text() == original


def test_typora_without_confirmed_image_folder_asks_first(monkeypatch, tmp_path):
    from hugin.project import ProjectConfig

    post_path = tmp_path / "post.md"
    post_path.write_text("---\ntitle: T\n---\nBody\n")
    monkeypatch.setattr(review, "load_project", lambda d: ProjectConfig())
    monkeypatch.setenv("HUGIN_EDITOR", "typora")
    ran = []
    monkeypatch.setattr(review.subprocess, "run", lambda cmd, **k: ran.append(post_path.read_text()))
    screen = FakeScreen(post_path)
    screen.all_posts = []
    pushed = []
    screen.app.push_screen = lambda modal, callback: pushed.append(callback)

    screen.action_external_editor()
    assert ran == [] and len(pushed) == 1  # the confirmation comes before the editor

    pushed[0](None)  # declined: Typora still opens, with the preview root only
    assert "typora-root-url" in ran[0] and "typora-copy-images-to" not in ran[0]


def test_png_from_typora_becomes_a_light_image_with_variants(monkeypatch, tmp_path, images_configured):
    import shutil
    import subprocess

    if shutil.which("magick") is None:
        pytest.skip("magick not installed")
    monkeypatch.setattr("hugin.trash.TRASH_DIR", tmp_path / "trash")
    site = tmp_path / "site"
    (site / "config" / "_default").mkdir(parents=True)
    (site / "config" / "_default" / "hugo.toml").write_text('title = "Café"\n')
    (site / "config" / "_default" / "params.toml").write_text(
        "[imageProcessing.content]\n  widths = [480, 800]\n")
    images_dir = site / "static" / "images" / "2026" / "11"
    images_dir.mkdir(parents=True)
    post_path = site / "content" / "posts" / "post.md"
    post_path.parent.mkdir(parents=True)
    post_path.write_text("---\ntitle: T\ndate: 2026-11-22\n---\nBody\n")
    monkeypatch.setenv("HUGIN_EDITOR", "open -W -n -a Typora")
    real_run = subprocess.run

    def run(cmd, **k):
        if cmd[0] == "magick":
            return real_run(cmd, **k)
        # The author drops a PNG on the featured line and another in the body
        real_run(["magick", "-size", "1200x600", "plasma:", str(images_dir / "cat.png")], check=True)
        text = post_path.read_text().replace(review.featured.PLACEHOLDER, "![](/images/2026/11/cat.png)")
        post_path.write_text(text.replace("Body", "Body\n\n![A cat](/images/2026/11/cat.png)"))

    monkeypatch.setattr(review.subprocess, "run", run)
    screen = FakeScreen(post_path)
    screen.all_posts = [type("Post", (), {"metadata": {"thumbnail": "/images/x.avif"}})()]

    screen.action_external_editor()

    text = post_path.read_text()
    kept = sorted(p.name for p in images_dir.iterdir())
    full = next(n for n in kept if n.startswith("cat.") and not n.endswith(".png"))
    assert len(kept) == 3 and not any(n.endswith(".png") for n in kept)
    assert {n.rsplit(".", 1)[0] for n in kept} == {"cat", "cat-480w", "cat-800w"}
    assert f"thumbnail: /images/2026/11/{full}" in text
    assert f"![A cat](/images/2026/11/{full})" in text
    assert any(m.startswith("Converted 1 image(s)") for m, _ in screen.notices)
    assert ("Created 2 smaller version(s) for srcset", "information") in screen.notices
    assert screen.reloaded == [0]
