"""Per-blog checklist settings: image checks, source domains, kept address, links to replaced posts."""

import struct
from datetime import datetime
from unittest.mock import MagicMock

import frontmatter

from hugin import drafting, image_check
from hugin import research as web
from hugin.project import ProjectConfig, load_project, save_project
from hugin.scanner import Post
from hugin.tui.project_settings import _domains


def _png(path, width):
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", width, 10) + b"\x00" * 20)
    return path


def _avif(path, width):
    ftyp = struct.pack(">I", 20) + b"ftypavif" + b"\x00\x00\x00\x00avif"
    # A thumbnail first, then the image: the widest one counts
    ispe = b"".join(
        struct.pack(">I", 20) + b"ispe" + b"\x00\x00\x00\x00" + struct.pack(">II", w, 10)
        for w in (160, width)
    )
    path.write_bytes(ftyp + ispe)
    return path


# --- image widths and alt text ---

def test_widths_are_read_from_headers(tmp_path):
    assert image_check.image_width(_png(tmp_path / "a.png", 1300)) == 1300
    assert image_check.image_width(_avif(tmp_path / "a.avif", 900)) == 900
    (tmp_path / "x.svg").write_text("<svg/>")
    assert image_check.image_width(tmp_path / "x.svg") is None


def test_problems_list_narrow_images_and_poor_alt_text(tmp_path):
    static = tmp_path / "static"
    (static / "images").mkdir(parents=True)
    _png(static / "images/wide.png", 1600)
    _png(static / "images/narrow.png", 800)
    _avif(static / "images/cover.avif", 1000)
    body = (
        "![A cat asleep](/images/wide.png)\n![](/images/narrow.png)\n"
        "![narrow.png](/images/narrow.png)\n![Remote](https://cdn.example/x.png)\n"
    )
    found = image_check.problems({"thumbnail": "/images/cover.avif"}, body, static, min_width=1200)
    assert found == [
        "/images/cover.avif: 1000 px wide (at least 1200)",
        "/images/narrow.png: 800 px wide (at least 1200)",
        "/images/narrow.png: no alt text",
        "/images/narrow.png: alt text is a file name (narrow.png)",
    ]
    # Width off: alt text is still checked
    assert len(image_check.problems({}, body, static)) == 2


# --- source domains ---

def _brave(*urls):
    return {"web": {"results": [{"url": u, "title": u, "description": ""} for u in urls]}}


def test_trusted_sources_come_first_and_excluded_ones_never_appear():
    web.activate_sources(["cornell.edu", "https://www.icatcare.org/"], ["petblog.example"])
    data = _brave(
        "https://petblog.example/a", "https://shop.example/b",
        "https://www.vet.cornell.edu/c", "https://icatcare.org/d",
    )
    urls = [r.url for r in web.parse_brave(data, 3)]
    assert urls == ["https://www.vet.cornell.edu/c", "https://icatcare.org/d", "https://shop.example/b"]
    assert web.is_trusted("https://vet.cornell.edu/x") and not web.is_trusted("https://notcornell.edu/")


def test_settings_round_trip_and_activate_sources(tmp_path):
    (tmp_path / "hugo.toml").write_text('title = "Blog"\n')
    posts = tmp_path / "content/post"
    posts.mkdir(parents=True)
    config = ProjectConfig()
    config.writing.keep_url = True
    config.research.trusted = ["cornell.edu"]
    config.research.excluded = ["petblog.example"]
    config.checks.min_image_width = 1200
    save_project(posts, config)
    web.activate_sources([], [])

    loaded = load_project(posts)
    assert loaded.writing.keep_url is True
    assert loaded.research.trusted == ["cornell.edu"] and loaded.research.excluded == ["petblog.example"]
    assert loaded.checks.min_image_width == 1200
    assert web.is_trusted("https://cornell.edu/a")
    # The default rules are not frozen into the file by keep_url
    assert "constraints" not in (tmp_path / "hugin/config.toml").read_text()


def test_domains_field_is_forgiving():
    assert _domains("https://www.Cornell.edu/feline, icatcare.org  aafp.org,aafp.org") == [
        "cornell.edu", "icatcare.org", "aafp.org",
    ]


# --- kept address ---

def test_a_kept_address_gives_the_draft_the_original_slug_date_and_url(tmp_path):
    original = Post(tmp_path / "old-cats.md", {
        "title": "Old", "slug": "cats", "date": datetime(2019, 5, 1, 10, 0), "url": "/cats/",
    }, "Body", False)
    (tmp_path / "cats.md").write_text("---\ntitle: taken\n---\n")  # the file name is taken
    path = drafting.create_draft(
        tmp_path, "New Title\n\nNew body", "rewrite", keep=drafting.kept_address(original),
    )
    meta = frontmatter.load(str(path)).metadata
    assert path.name == "cats-1.md"
    assert meta["slug"] == "cats" and meta["url"] == "/cats/"
    assert str(meta["date"]).startswith("2019-05-01")


def test_without_a_slug_the_file_name_is_kept(tmp_path):
    original = Post(tmp_path / "my-post.md", {"title": "Old", "date": "2020-02-02T08:00:00"}, "", False)
    keep = drafting.kept_address(original)
    assert keep["slug"] == "my-post" and keep["url"] is None and keep["date"].year == 2020


# --- links never point to posts on their way out ---

def test_replaced_posts_are_not_link_targets_unless_their_address_lives_on(tmp_path):
    from hugin.tui.review import HuginScreen

    def post(name, **meta):
        return Post(tmp_path / name, {"title": name, **meta}, "", False)

    old = post("old.md")
    merged = post("merged.md")
    new = post("new.md", refactor_of="old.md", merged_from=["merged.md"])
    kept_old = post("kept-old.md")
    kept_new = post("kept-new.md", refactor_of="kept-old.md")
    screen = HuginScreen.__new__(HuginScreen)
    screen.all_posts = [old, merged, new, kept_old, kept_new, post("other.md")]
    screen.index = MagicMock()
    urls = {"old.md": "/old/", "merged.md": "/merged/", "new.md": "/new/",
            "kept-old.md": "/kept/", "kept-new.md": "/kept/", "other.md": "/other/"}
    screen.index.get_post_url.side_effect = lambda p: urls[p.filename]

    assert {p.filename for p in screen._replaced_posts()} == {"old.md", "merged.md", "kept-old.md"}
    # kept-new.md lives at kept-old.md's address, so that URL stays a target
    assert screen._replaced_urls() == {"/old/", "/merged/"}
