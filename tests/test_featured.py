"""The featured image as the first line of the body while Typora has the post open."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from hugin import featured
from hugin.project import ImagesSettings, ProjectConfig, load_project, save_project
from hugin.tui.review import HuginScreen

POST = "---\ntitle: Cats\nthumbnail: /images/old.avif\ntags:\n  - cats\n---\nIntro paragraph.\n\nMore.\n"


def _post(tmp_path, text=POST) -> Path:
    path = tmp_path / "p.md"
    path.write_text(text)
    return path


def test_detect_field_takes_the_commonest_image_field():
    posts = [SimpleNamespace(metadata=m) for m in (
        {"thumbnail": "/images/a.avif", "title": "x.png is a title"},
        {"thumbnail": "/images/b.jpg", "image": "/c.png"},
        {"image": "/d.webp"},
        {"thumbnail": 3},
    )]
    assert featured.detect_field(posts) == "thumbnail"
    assert featured.detect_field([SimpleNamespace(metadata={"title": "T"})]) is None


def test_show_then_take_unchanged_restores_the_file(tmp_path):
    post = _post(tmp_path)
    assert featured.show(post, "thumbnail")
    assert post.read_text() == (
        "---\nhugin-featured-image: thumbnail\ntitle: Cats\nthumbnail: /images/old.avif\ntags:\n  - cats\n---\n"
        "![Featured image](/images/old.avif)\n\nIntro paragraph.\n\nMore.\n"
    )
    assert not featured.show(post, "thumbnail")  # never twice
    assert featured.posts_with_marker(tmp_path) == [post]

    taken = featured.take(post)
    assert (taken.found, taken.changed) == (True, False)
    assert post.read_text() == POST
    assert featured.take(post) is None and featured.posts_with_marker(tmp_path) == []


def test_a_new_image_on_the_line_becomes_the_featured_one(tmp_path):
    post = _post(tmp_path)
    featured.show(post, "thumbnail")
    text = post.read_text().replace(
        "![Featured image](/images/old.avif)",
        "![Featured image](/images/old.avif)![new cat.webp](/images/2026/11/new-cat.webp)",
    )
    post.write_text(text)
    taken = featured.take(post)
    assert taken.changed and taken.new == "/images/2026/11/new-cat.webp"
    assert post.read_text() == POST.replace("/images/old.avif", "/images/2026/11/new-cat.webp")


def test_a_post_without_one_gets_a_line_asking_for_it(tmp_path):
    post = _post(tmp_path, "---\ntitle: Cats\n---\n\nIntro.\n")
    featured.show(post, "image")
    assert featured.PLACEHOLDER + "\n\n\nIntro." in post.read_text()

    untouched = featured.take(post)
    assert not untouched.changed and post.read_text() == "---\ntitle: Cats\n---\n\nIntro.\n"

    featured.show(post, "image")
    post.write_text(post.read_text().replace(
        featured.PLACEHOLDER, '<img src="/up/a b.png" style="zoom:50%;" />'))
    taken = featured.take(post)
    assert taken.new == "/up/a b.png"
    assert post.read_text() == '---\ntitle: Cats\nimage: "/up/a b.png"\n---\n\nIntro.\n'


def test_a_deleted_line_changes_nothing(tmp_path):
    post = _post(tmp_path)
    featured.show(post, "thumbnail")
    post.write_text(post.read_text().replace("![Featured image](/images/old.avif)\n\n", ""))
    taken = featured.take(post)
    assert not taken.found and post.read_text() == POST


def test_an_image_still_outside_the_blog_stays_in_the_body(tmp_path):
    post = _post(tmp_path)
    featured.show(post, "thumbnail")
    post.write_text(post.read_text().replace("](/images/old.avif)", "](../../Downloads/x.avif)"))
    taken = featured.take(post)
    assert taken.unsafe == "../../Downloads/x.avif" and not taken.changed
    assert post.read_text().startswith("---\ntitle: Cats\nthumbnail: /images/old.avif\n")
    assert "![Featured image](../../Downloads/x.avif)\n\nIntro" in post.read_text()


@pytest.mark.parametrize("gap", [" ", "\n"])
def test_text_glued_to_the_featured_line_stays_in_the_body(tmp_path, gap):
    post = _post(tmp_path)
    featured.show(post, "thumbnail")
    post.write_text(post.read_text().replace(
        "![Featured image](/images/old.avif)\n\nIntro",
        f"![Featured image](/images/2026/11/new-cat.avif){gap}Intro",
    ))
    taken = featured.take(post)
    assert taken.found and taken.new == "/images/2026/11/new-cat.avif"
    assert post.read_text() == POST.replace("/images/old.avif", "/images/2026/11/new-cat.avif")


def test_glued_text_with_an_image_outside_the_blog_keeps_both(tmp_path):
    post = _post(tmp_path)
    featured.show(post, "thumbnail")
    post.write_text(post.read_text().replace(
        "![Featured image](/images/old.avif)\n\nIntro", "![Featured image](../../Downloads/x.avif) Intro"))
    taken = featured.take(post)
    assert taken.unsafe == "../../Downloads/x.avif" and not taken.changed
    assert "\n---\n![Featured image](../../Downloads/x.avif) Intro paragraph." in post.read_text()


def test_a_body_that_opens_with_its_own_image_is_not_the_featured_line(tmp_path):
    post = _post(tmp_path, POST.replace("Intro paragraph.", "![a cat](/images/cat.avif) Intro paragraph."))
    featured.show(post, "thumbnail")
    post.write_text(post.read_text().replace("![Featured image](/images/old.avif)\n\n", ""))
    taken = featured.take(post)
    assert not taken.found and not taken.changed
    assert "![a cat](/images/cat.avif) Intro paragraph." in post.read_text()


def test_a_field_value_that_is_not_a_path_never_piles_up_in_the_body(tmp_path):
    post = _post(tmp_path, POST.replace("/images/old.avif", "TBD"))
    for _ in range(2):
        featured.show(post, "thumbnail")
        taken = featured.take(post)
        assert taken.found and not taken.unsafe and not taken.changed
    assert post.read_text() == POST.replace("/images/old.avif", "TBD")


def test_toml_front_matter_is_left_alone(tmp_path):
    post = _post(tmp_path, '+++\ntitle = "T"\n+++\nBody\n')
    assert not featured.show(post, "image") and featured.take(post) is None


def test_settings_keep_a_configured_field(tmp_path):
    config = ProjectConfig()
    config.images = ImagesSettings("static/images", "/images", "YYYY/MM", featured="cover")
    save_project(tmp_path, config)
    assert load_project(tmp_path).images.featured == "cover"


# --- review screen ------------------------------------------------------------------------


class FakeScreen:
    _featured_field = HuginScreen._featured_field
    _take_featured = HuginScreen._take_featured

    def __init__(self, posts=(), directory=Path(".")):
        self.all_posts = list(posts)
        self.directory = directory
        self.notices = []

    def notify(self, message, **kw):
        self.notices.append((message, kw.get("severity", "information")))


def test_field_comes_from_the_settings_else_the_posts():
    screen = FakeScreen([SimpleNamespace(metadata={"image": "/a.png"})])
    assert screen._featured_field(None) == "image"
    assert screen._featured_field(ImagesSettings(featured="cover")) == "cover"


def _site_with_layout(root: Path, template: str, where: str = "layouts/_partials/card.html") -> Path:
    (root / "hugo.toml").write_text('title = "T"\n', encoding="utf-8")
    (root / "content" / "posts").mkdir(parents=True)
    path = root / where
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template, encoding="utf-8")
    return root / "content" / "posts"


def test_theme_field_reads_the_templates(tmp_path):
    _site_with_layout(tmp_path, "{{ with .Params.image }}{{ . }}{{ end }} {{ .Params.title }}")
    assert featured.theme_field(tmp_path) == "image"


def test_theme_field_looks_in_themes_and_ignores_case(tmp_path):
    _site_with_layout(tmp_path, "{{ .Params.Cover }}", "themes/paper/layouts/single.html")
    assert featured.theme_field(tmp_path) == "cover"


def test_theme_field_none_without_a_cover_param(tmp_path):
    _site_with_layout(tmp_path, "{{ .Params.subtitle }}")
    assert featured.theme_field(tmp_path) is None
    assert featured.theme_field(tmp_path / "nowhere") is None


def test_field_falls_back_to_the_theme_when_posts_have_none(tmp_path):
    posts_dir = _site_with_layout(tmp_path, "{{ with $page.Params.image }}{{ end }}")
    plain = [SimpleNamespace(metadata={"title": "T"})]
    assert FakeScreen(plain, posts_dir)._featured_field(None) == "image"
    assert featured.field_for(posts_dir, plain) == "image"
    # The posts' own field still wins over the theme's
    assert featured.field_for(posts_dir, [SimpleNamespace(metadata={"cover": "/a.png"})]) == "cover"


def test_field_is_thumbnail_without_settings_posts_or_theme(tmp_path):
    assert FakeScreen([], tmp_path)._featured_field(None) == "thumbnail"


@pytest.mark.parametrize("edit, notices", [
    (lambda t: t.replace("](/images/old.avif)", "](/images/new.avif)"),
     [("Featured image (thumbnail): /images/new.avif", "information")]),
    (lambda t: t.replace("![Featured image](/images/old.avif)\n\n", ""),
     [("Featured image unchanged: the post no longer starts with it", "warning")]),
    (lambda t: t.replace("](/images/old.avif)", "](/Users/me/Downloads/x.avif)"),
     [("Featured image not set: /Users/me/Downloads/x.avif is not in the blog, so it stays in the body",
       "warning")]),
    (lambda t: t, []),
])
def test_take_featured_reports(tmp_path, edit, notices, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: Path("/Users/me"))
    post = _post(tmp_path)
    plain = tmp_path / "plain.md"
    plain.write_text(POST)  # never shown: left alone
    featured.show(post, "thumbnail")
    post.write_text(edit(post.read_text()))
    screen = FakeScreen()
    screen._take_featured([post, plain])
    assert screen.notices == notices
    assert plain.read_text() == POST
