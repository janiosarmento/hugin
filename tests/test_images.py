"""Images pasted in an external editor: convention detection and moving them into the blog."""

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from textual.app import App
from textual.widgets import Input

from hugin import images
from hugin.project import ImagesSettings, ProjectConfig, load_project, save_project
from hugin.tui import review
from hugin.tui.images_screen import ImageConventionScreen
from hugin.tui.review import HuginScreen


def _post(day, content="", **metadata):
    return SimpleNamespace(date=datetime(2026, 11, day) if day else None, metadata=metadata, content=content)


# --- references -----------------------------------------------------------------


def test_site_path_keeps_only_images_on_this_site():
    base = "https://sarmento.org/"
    assert images.site_path("/images/2026/11/a%20b.avif?x=1#top") == "/images/2026/11/a b.avif"
    assert images.site_path("https://sarmento.org/images/a.webp", base) == "/images/a.webp"
    assert images.site_path("https://cdn.example/images/a.webp", base) is None
    assert images.site_path("//cdn.example/a.jpg") is None
    assert images.site_path("images/a.jpg") is None  # page-relative
    assert images.site_path("/posts/some-page/") is None  # not an image


def test_post_refs_reads_front_matter_markdown_html_and_figures():
    content = (
        '![Cat](/images/a.avif "A cat")\n![Spaced](</images/b c.png>)\n'
        '<img class="x" src="/images/d.webp">\n{{< figure src="/images/e.jpg" >}}'
    )
    refs = images.post_refs({"thumbnail": "/images/t.avif", "title": "Not an image",
                             "gallery": [{"src": "/images/g.jpg"}]}, content)
    assert refs == ["/images/t.avif", "/images/g.jpg", "/images/a.avif", "/images/b c.png",
                    "/images/d.webp", "/images/e.jpg"]


# --- detection -------------------------------------------------------------------


def test_detect_learns_root_and_date_folders_from_recent_posts():
    posts = [
        _post(30, "no images in the newest post"),  # skipped: says nothing
        _post(22, thumbnail="/images/canetas/iraurita.avif"),
        _post(8, thumbnail="/images/2026/11/copyright.avif"),
        _post(6, "![a](/images/kairos/k1.webp)", thumbnail="/images/kairos/k.avif"),
        _post(4, thumbnail="https://sarmento.org/images/2026/11/gilead.avif"),
        _post(None, "![x](/old/x.jpg)"),  # undated: ignored
    ]
    found = images.detect(posts, base_url="https://sarmento.org/")
    assert found.convention == images.Convention("static/images", "/images", "YYYY/MM")
    assert "4 recent posts with images" in found.evidence
    assert "2 use date folders, 2 use topic folders" in found.evidence


def test_detect_uses_only_the_ten_newest_posts_with_images():
    recent = [_post(28 - i, thumbnail=f"/wp-content/uploads/topic{i}/a.avif") for i in range(10)]
    old_wp = [_post(5 - i % 5, f"![x](/old-uploads/2019/0{1 + i % 9}/x.jpg)") for i in range(20)]
    found = images.detect(recent + old_wp, static_dir="static")
    assert found.convention == images.Convention("static/wp-content/uploads", "/wp-content/uploads", "")


def test_detect_without_local_images():
    assert images.detect([_post(1, "![x](https://elsewhere.example/a.jpg)"), _post(2)]) is None


def test_detect_root_level_images():
    found = images.detect([_post(1, "![x](/photo.jpg)")], static_dir="static")
    assert found.convention == images.Convention("static", "", "")


# --- inbox references -----------------------------------------------------------------


def test_resolve_finds_inbox_images_in_every_shape_an_editor_writes(tmp_path):
    home = tmp_path / "home"
    inbox = home / "Pictures" / "typora inbox"
    static = home / "projects" / "blog" / "static"
    post_dir = home / "projects" / "blog" / "content" / "post"
    (static / "images").mkdir(parents=True)
    (static / "images" / "site.png").write_bytes(b"x")

    def kind(ref):
        hit = images.resolve(ref, post_dir, static, inbox)
        return hit and (hit[0], hit[1].name)

    assert kind(f"{inbox}/a.png") == ("inbox", "a.png")
    assert kind(f"{inbox}/b c.png") == ("inbox", "b c.png")
    assert kind(f"{str(inbox).replace(' ', '%20')}/c%20d.png") == ("inbox", "c d.png")
    assert kind(f"file://{inbox}/d.png") == ("inbox", "d.png")
    # Typora with typora-root-url: relative to the static folder
    assert kind("/../../../Pictures/typora%20inbox/Winged%20Bubble.png") == ("inbox", "Winged Bubble.png")
    # A site image linked by a path that is not a site path
    assert kind("../../static/images/site.png") == ("static", "site.png")
    assert kind(f"{static}/images/site.png") == ("static", "site.png")
    # Nothing to fix
    assert kind("/images/site.png") is None
    assert kind("/images/missing.png") is None
    assert kind("https://example.com/a.png") is None
    assert kind("data:image/png;base64,xx") is None


def test_safe_name():
    assert images.safe_name("Screen Shot 2026-11-22 at 19.07.PNG") == "Screen-Shot-2026-11-22-at-19.07.png"
    assert images.safe_name("Quiet Desk, Neon Media Storm.avif") == "Quiet-Desk-Neon-Media-Storm.avif"
    assert images.safe_name("café.jpg") == "caf.jpg"
    assert images.safe_name("???.jpg") == "image.jpg"


# --- adopting ---------------------------------------------------------------------------


@pytest.fixture
def blog(tmp_path):
    root = tmp_path / "blog"
    (root / "content" / "post").mkdir(parents=True)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    return SimpleNamespace(root=root, inbox=inbox, posts=root / "content" / "post")


def _write_post(blog, name, body, date="2026-11-22", **fm):
    extra = "".join(f"{k}: {v}\n" for k, v in fm.items())
    path = blog.posts / name
    path.write_text(f"---\ntitle: T\ndate: {date}\n{extra}---\n{body}\n", encoding="utf-8")
    return path


def _inbox_image(blog, name, data=b"img"):
    (blog.inbox / name).write_bytes(data)
    return blog.inbox / name


CONVENTION = images.Convention("static/images", "/images", "YYYY/MM")


def test_adopt_moves_images_to_the_date_folder_and_rewrites_links(blog):
    _inbox_image(blog, "Screen Shot 1.png", b"one")
    _inbox_image(blog, "cover.avif")
    (blog.root / "static/images/2026/11").mkdir(parents=True)
    (blog.root / "static/images/2026/11/cover.avif").write_bytes(b"taken")
    post = _write_post(
        blog, "p.md",
        f"Look:\n\n![shot](<{blog.inbox}/Screen Shot 1.png>)\n\n![shot again]({blog.inbox}/Screen%20Shot%201.png)",
        thumbnail=f"{blog.inbox}/cover.avif",
    )

    result = images.adopt(post, blog.root, CONVENTION, inbox=blog.inbox)

    assert result.moved == [
        ("cover.avif", "/images/2026/11/cover-2.avif"),  # name taken: not overwritten
        ("Screen Shot 1.png", "/images/2026/11/Screen-Shot-1.png"),
        ("Screen Shot 1.png", "/images/2026/11/Screen-Shot-1.png"),
    ]
    text = post.read_text()
    assert "thumbnail: /images/2026/11/cover-2.avif" in text
    assert text.count("![shot](/images/2026/11/Screen-Shot-1.png)") == 1
    assert "![shot again](/images/2026/11/Screen-Shot-1.png)" in text
    assert str(blog.inbox) not in text
    assert (blog.root / "static/images/2026/11/Screen-Shot-1.png").read_bytes() == b"one"
    assert (blog.root / "static/images/2026/11/cover.avif").read_bytes() == b"taken"
    assert list(blog.inbox.iterdir()) == []


def test_adopt_reuses_the_folder_of_the_posts_other_images(blog):
    _inbox_image(blog, "new.avif")
    post = _write_post(blog, "p.md", f"![old](/images/canetas/old.avif)\n![new]({blog.inbox}/new.avif)",
                       thumbnail="/images/canetas/thumb.avif")
    images.adopt(post, blog.root, CONVENTION, inbox=blog.inbox)
    assert "![new](/images/canetas/new.avif)" in post.read_text()
    assert (blog.root / "static/images/canetas/new.avif").is_file()


def test_adopt_without_date_folders_or_date(blog):
    _inbox_image(blog, "a.png")
    post = blog.posts / "undated.md"
    post.write_text(f"---\ntitle: T\n---\n![a]({blog.inbox}/a.png)\n")
    images.adopt(post, blog.root, images.Convention("static/wp-content/uploads", "/wp-content/uploads"),
                 inbox=blog.inbox)
    assert "![a](/wp-content/uploads/a.png)" in post.read_text()


def test_adopt_links_the_same_image_in_a_translation(blog):
    _inbox_image(blog, "shared.avif")
    pt = _write_post(blog, "pt.md", f"![pt]({blog.inbox}/shared.avif)")
    en = _write_post(blog, "en.md", f"![en]({blog.inbox}/shared.avif)")
    moved = {}
    images.adopt(pt, blog.root, CONVENTION, inbox=blog.inbox, moved_before=moved)
    images.adopt(en, blog.root, CONVENTION, inbox=blog.inbox, moved_before=moved)
    assert "![en](/images/2026/11/shared.avif)" in en.read_text()

    # A later session has no memory, but finds the file where it was moved
    later = _write_post(blog, "later.md", f"![again]({blog.inbox}/shared.avif)")
    result = images.adopt(later, blog.root, CONVENTION, inbox=blog.inbox)
    assert result.moved == [("shared.avif", "/images/2026/11/shared.avif")]


def test_adopt_reports_missing_files_and_leaves_their_links(blog):
    post = _write_post(blog, "p.md", f"![gone]({blog.inbox}/gone.png)")
    before = post.read_text()
    result = images.adopt(post, blog.root, CONVENTION, inbox=blog.inbox)
    assert result.missing == ["gone.png"] and not result.changed
    assert post.read_text() == before


def test_posts_to_adopt(blog):
    _write_post(blog, "clean.md", "![x](/images/x.png)")
    linked = _write_post(blog, "linked.md", f"![x]({blog.inbox}/x.png)")
    (blog.root / "content/en").mkdir()
    other = blog.root / "content/en/other.md"
    other.write_text(f"![y](<{blog.inbox}/y z.png>)")
    assert images.posts_to_adopt(blog.root / "content", blog.root / "static", blog.inbox) == [other, linked]


def test_adopt_copies_images_dragged_from_elsewhere_and_makes_plain_links_images(blog, tmp_path):
    # Typora inserts a dragged AVIF as a plain link relative to the post, and copies nothing
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "tabby cat.avif").write_bytes(b"cat")
    (downloads / "notes.pdf").write_bytes(b"pdf")
    (blog.root / "static/images").mkdir(parents=True)
    (blog.root / "static/images/site.jpg").write_bytes(b"s")
    post = _write_post(
        blog, "p.md",
        " [tabby cat.avif](../../../Downloads/tabby%20cat.avif) \n"
        "[a pdf](../../../Downloads/notes.pdf)\n"
        "[site photo](../../static/images/site.jpg)\n"
        "[gone](../../../Downloads/gone.avif)",
    )
    assert images.needs_adopting(post, blog.root / "static", blog.inbox)
    assert images.posts_to_adopt(blog.root / "content", blog.root / "static", blog.inbox) == [post]

    result = images.adopt(post, blog.root, CONVENTION, inbox=blog.inbox)
    assert result.moved == [("tabby cat.avif", "/images/2026/11/tabby-cat.avif")]
    body = post.read_text().split("---\n")[2]
    assert body == (
        "![tabby cat.avif](/images/2026/11/tabby-cat.avif)\n"
        "[a pdf](../../../Downloads/notes.pdf)\n"
        "[site photo](../../static/images/site.jpg)\n"
        "[gone](../../../Downloads/gone.avif)\n"
    )
    assert (blog.root / "static/images/2026/11/tabby-cat.avif").read_bytes() == b"cat"
    assert (downloads / "tabby cat.avif").is_file()  # a copy: the original stays
    assert not images.needs_adopting(post, blog.root / "static", blog.inbox)


def test_a_dragged_image_linked_from_the_static_folder_is_found(tmp_path):
    # With typora-root-url, Typora writes a dragged file relative to static/, not to the post
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    (home / "Downloads" / "cat.avif").write_bytes(b"c")
    static = home / "projects" / "blog" / "static"
    post_dir = home / "projects" / "blog" / "content" / "posts"
    hit = images.resolve("../../../Downloads/cat.avif", post_dir, static, tmp_path / "inbox")
    assert hit == ("local", home / "Downloads" / "cat.avif")


def test_images_inside_the_site_are_never_copied(blog):
    (blog.root / "content/post/bundle").mkdir()
    (blog.root / "content/post/bundle/a.png").write_bytes(b"a")
    post = _write_post(blog, "bundle/index.md", "![a](a.png)")
    assert images.resolve("a.png", post.parent, blog.root / "static", blog.inbox) is None
    assert not images.needs_adopting(post, blog.root / "static", blog.inbox)


def test_adopt_follows_typora_links_relative_to_the_static_folder(blog):
    _inbox_image(blog, "Winged Bubble.png", b"w")
    rel = "/" + "/".join([".."] * 2) + f"/{blog.inbox.name}/Winged%20Bubble.png"  # static -> blog -> tmp
    post = _write_post(blog, "p.md", f"![Winged]({rel})")
    result = images.adopt(post, blog.root, CONVENTION, blog.root / "static", inbox=blog.inbox)
    assert result.moved == [("Winged Bubble.png", "/images/2026/11/Winged-Bubble.png")]
    assert "![Winged](/images/2026/11/Winged-Bubble.png)" in post.read_text()


def test_adopt_turns_file_paths_to_site_images_into_site_paths(blog):
    (blog.root / "static/images/canetas").mkdir(parents=True)
    (blog.root / "static/images/canetas/a b.png").write_bytes(b"a")
    post = _write_post(blog, "p.md", "![a](../../static/images/canetas/a%20b.png)\n![ok](/images/canetas/a%20b.png)")
    result = images.adopt(post, blog.root, CONVENTION, blog.root / "static", inbox=blog.inbox)
    assert result.relinked == ["/images/canetas/a%20b.png"] and not result.moved
    assert post.read_text().count("](/images/canetas/a%20b.png)") == 2


def test_destination_is_where_adopt_would_put_new_images(blog):
    post = _write_post(blog, "p.md", "![old](/images/canetas/old.avif)")
    assert images.destination(post, blog.root, CONVENTION) == (blog.root / "static/images/canetas", "/images/canetas")
    fresh = _write_post(blog, "q.md", "No images yet", date="2025-03-09")
    assert images.destination(fresh, blog.root, CONVENTION) == (blog.root / "static/images/2025/03", "/images/2025/03")


# --- settings ---------------------------------------------------------------------------


def test_images_settings_round_trip(tmp_path):
    blog = tmp_path / "blog"
    (blog / "content" / "post").mkdir(parents=True)
    (blog / "hugo.toml").write_text('baseURL = "https://x.example/"\n')
    posts = blog / "content" / "post"

    save_project(posts, ProjectConfig())
    assert "[images]" not in (blog / "hugin" / "config.toml").read_text()
    assert not load_project(posts).images.configured

    save_project(posts, ProjectConfig(images=ImagesSettings("static/images", "/images", "YYYY/MM")))
    assert '[images]\n# Where images' in (blog / "hugin" / "config.toml").read_text()
    assert load_project(posts).images == ImagesSettings("static/images", "/images", "YYYY/MM")


# --- confirmation screen ------------------------------------------------------------------


def _run_screen(screen, keys):
    result = {}

    async def go():
        app = App()
        async with app.run_test(size=(120, 40)) as pilot:
            app.push_screen(screen, lambda r: result.setdefault("v", r))
            await pilot.pause()
            await keys(app, pilot)
            await pilot.pause(0.2)

    asyncio.run(go())
    return result.get("v", "unset")


DETECTED = images.Detection(images.Convention("static/images", "/images", "YYYY/MM"), "4 recent posts")


def test_confirmation_returns_the_edited_convention(tmp_path):
    async def keys(app, pilot):
        assert "4 recent posts" in str(app.screen.query_one("#images-evidence").render())
        assert "static/images/20" in str(app.screen.query_one("#images-example").render())
        app.screen.query_one("#images-url", Input).value = "img/"
        await pilot.press("ctrl+s")

    assert _run_screen(ImageConventionScreen(DETECTED, tmp_path, 1), keys) == images.Convention(
        "static/images", "/img", "YYYY/MM")


def test_confirmation_rejects_a_folder_outside_the_site(tmp_path):
    async def keys(app, pilot):
        app.screen.query_one("#images-dir", Input).value = "../elsewhere"
        await pilot.press("ctrl+s")
        assert "inside the site" in str(app.screen.query_one("#images-error").render())
        await pilot.press("escape")

    assert _run_screen(ImageConventionScreen(DETECTED, tmp_path, 1), keys) is None


# --- review screen hook -------------------------------------------------------------------


class FakeApp:
    def __init__(self):
        self.screens = []

    def push_screen(self, screen, callback):
        self.screens.append((screen, callback))


class FakeScreen:
    _adopt_inbox_images = HuginScreen._adopt_inbox_images
    _move_inbox_images = HuginScreen._move_inbox_images
    _image_settings = HuginScreen._image_settings
    _static_root = HuginScreen._static_root
    _base_url = HuginScreen._base_url

    def __init__(self, directory):
        self.directory = directory
        self.all_posts = [_post(20, thumbnail="/images/2026/11/a.avif")]
        self.site = SimpleNamespace(config={"baseURL": "https://x.example/"})
        self.app = FakeApp()
        self._adopted_images = {}
        self.notices = []

    def notify(self, message, **kw):
        self.notices.append((message, kw.get("severity", "information")))


@pytest.fixture
def hooked(blog, monkeypatch):
    (blog.root / "hugo.toml").write_text('baseURL = "https://x.example/"\n')
    monkeypatch.setattr(images, "INBOX", blog.inbox)
    synced = []
    monkeypatch.setattr(review, "save_and_sync",
                        lambda app, directory, config, what: (save_project(directory, config), synced.append(what)))
    return SimpleNamespace(blog=blog, synced=synced, screen=FakeScreen(blog.posts))


def test_first_move_asks_for_the_convention_then_saves_it(hooked):
    blog, screen = hooked.blog, hooked.screen
    _inbox_image(blog, "a.png")
    post = _write_post(blog, "p.md", f"![a]({blog.inbox}/a.png)")
    done = []

    screen._adopt_inbox_images([post], lambda: done.append(True))
    modal, callback = screen.app.screens[0]
    assert isinstance(modal, ImageConventionScreen) and not done
    callback(images.Convention("static/images", "/images", "YYYY/MM"))

    assert done == [True] and hooked.synced == ["image folders"]
    assert "![a](/images/2026/11/a.png)" in post.read_text()
    assert load_project(blog.posts).images.configured
    assert screen.notices[0][0].startswith("Moved 1 image(s)")

    # Next time it does not ask
    _inbox_image(blog, "b.png")
    second = _write_post(blog, "q.md", f"![b]({blog.inbox}/b.png)")
    screen._adopt_inbox_images([second], lambda: done.append(True))
    assert len(screen.app.screens) == 1 and done == [True, True]
    assert "![b](/images/2026/11/b.png)" in second.read_text()


def test_declining_leaves_the_images_and_continues(hooked):
    blog, screen = hooked.blog, hooked.screen
    _inbox_image(blog, "a.png")
    post = _write_post(blog, "p.md", f"![a]({blog.inbox}/a.png)")
    done = []
    screen._adopt_inbox_images([post], lambda: done.append(True))
    screen.app.screens[0][1](None)
    assert done == [True] and str(blog.inbox) in post.read_text()
    assert screen.notices[0][1] == "warning" and not hooked.synced


def test_posts_without_inbox_images_go_straight_on(hooked):
    post = _write_post(hooked.blog, "p.md", "![x](/images/x.png)")
    done = []
    hooked.screen._adopt_inbox_images([post, Path("/nonexistent.md")], lambda: done.append(True))
    assert done == [True] and hooked.screen.app.screens == []


# --- Typora preview root ------------------------------------------------------------------


def test_typora_keys_are_added_to_yaml_front_matter_and_removed_exactly(tmp_path):
    post = tmp_path / "p.md"
    original = "---\ntitle: T\ndate: 2026-11-22\n---\nBody\ntypora-root-url: stays, it is body text\n"
    post.write_text(original)

    assert images.add_typora_keys(post, Path("/Users/me/blog/static"), Path("/Users/me/blog/static/images/x"))
    text = post.read_text()
    assert text.startswith(
        "---\ntypora-root-url: /Users/me/blog/static\n"
        "typora-copy-images-to: /Users/me/blog/static/images/x\ntitle: T\n")
    assert not images.add_typora_keys(post, Path("/elsewhere"))  # never twice

    assert images.posts_with_typora_keys(tmp_path) == [post]
    assert images.strip_typora_keys(post)
    assert post.read_text() == original
    assert not images.strip_typora_keys(post)
    assert images.posts_with_typora_keys(tmp_path) == []


def test_typora_root_url_alone(tmp_path):
    post = tmp_path / "p.md"
    post.write_text("---\ntitle: T\n---\nBody\n")
    assert images.add_typora_keys(post, Path("/x/static"))
    assert post.read_text() == "---\ntypora-root-url: /x/static\ntitle: T\n---\nBody\n"


def test_typora_keys_skip_toml_front_matter(tmp_path):
    post = tmp_path / "p.md"
    post.write_text('+++\ntitle = "T"\n+++\nBody\n')
    assert not images.add_typora_keys(post, Path("/x"))
    assert post.read_text() == '+++\ntitle = "T"\n+++\nBody\n'


def test_typora_paths_are_quoted_only_when_yaml_needs_it(tmp_path):
    post = tmp_path / "p.md"
    post.write_text("---\ntitle: T\n---\nBody\n")
    images.add_typora_keys(post, Path("/Users/me/My Blogs/blog #1: cats/static"))
    assert post.read_text().startswith('---\ntypora-root-url: "/Users/me/My Blogs/blog #1: cats/static"\n')


# --- Typora's indented images ---------------------------------------------------------------


def test_tidy_unindents_images_alone_on_their_line():
    text = (
        "Intro.\n"
        "\n"
        " ![cat](/images/cat.avif) \n"
        "\n"
        "- a list item\n"
        "\n"
        "  ![in the item](/images/item.png)\n"
        "\n"
        "Text  ![inline](/images/i.png) stays.\n"
        "```\n"
        "\n"
        " ![code](/images/code.png)\n"
        "```\n"
        "\n"
        "    ![four spaces is code](/images/x.png)\n"
    )
    assert images.tidy_image_lines(text) == text.replace(
        " ![cat](/images/cat.avif) \n", "![cat](/images/cat.avif)\n")


def test_tidy_post_and_adopt_leave_no_indented_image(blog, tmp_path):
    post = _write_post(blog, "p.md", "Intro.\n\n ![a](/images/a.png) ")
    assert images.tidy_post(post) and "\n![a](/images/a.png)\n" in post.read_text()
    assert not images.tidy_post(post)

    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "b.avif").write_bytes(b"b")
    dragged = _write_post(blog, "q.md", "Intro.\n\n [b.avif](../../../Downloads/b.avif) ")
    images.adopt(dragged, blog.root, CONVENTION, inbox=blog.inbox)
    assert "\n![b.avif](/images/2026/11/b.avif)\n" in dragged.read_text()


def test_adopt_moves_images_typora_copied_under_the_post_folder(blog):
    # Typora joined the absolute copy folder onto the post's folder
    static = blog.root / "static"
    intended = static / "images/2026/11/a cat.webp"
    misplaced = blog.posts / str(intended).lstrip("/")
    misplaced.parent.mkdir(parents=True)
    misplaced.write_bytes(b"cat")
    (static / "images/2026/11").mkdir(parents=True)
    (static / "images/2026/11/kept.webp").write_bytes(b"k")
    (blog.posts / str(static / "images/2026/11/kept.webp").lstrip("/")).write_bytes(b"k")
    link = f"/../content/post{quote(str(intended))}"
    post = _write_post(blog, "p.md", f"![a]({link})\n\n![k](/../content/post{static}/images/2026/11/kept.webp)")
    assert images.posts_to_adopt(blog.root / "content", static, blog.inbox) == [post]

    result = images.adopt(post, blog.root, CONVENTION, inbox=blog.inbox)
    assert result.moved == [("a cat.webp", "/images/2026/11/a%20cat.webp"),
                            ("kept.webp", "/images/2026/11/kept.webp")]  # a copy already in place
    assert "![a](/images/2026/11/a%20cat.webp)\n\n![k](/images/2026/11/kept.webp)" in post.read_text()
    assert intended.read_bytes() == b"cat" and (static / "images/2026/11/kept.webp").read_bytes() == b"k"
    assert sorted(p.name for p in blog.posts.iterdir()) == ["p.md"]  # the stray folders are gone
