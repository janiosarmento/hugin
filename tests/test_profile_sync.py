"""`o` works on the post as it is on disk, and its new link profile becomes the post's keywords."""

import asyncio
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import frontmatter
from click.testing import CliRunner
from textual.app import App

import hugin.cli as cli
import hugin.tui.review as review
from hugin.config import EmbeddingsConfig, FrontmatterConfig, HuginConfig, LinksConfig
from hugin.engines import Engine
from hugin.scanner import Post
from hugin.tui.review import HuginScreen
from hugin.writer import write_keywords_block

PROFILE = "café, hugo, python"
BODY = "word " * 700  # budget: 2 links


def _posts(tmp_path):
    posts = []
    for i in range(3):
        path = tmp_path / f"p{i}.md"
        path.write_text(f'---\ntitle: "P{i}"\n---\n\n{BODY}', encoding="utf-8")
        posts.append(Post(path, {"title": f"P{i}"}, BODY, False,
                          date=datetime.now() - timedelta(days=i + 1),
                          mtime=path.stat().st_mtime))
    return posts


def _press_o(tmp_path, monkeypatch, posts, profile=PROFILE):
    prompts = []

    async def fake_llm(engine, prompt, **kw):
        prompts.append(prompt)
        return "[]"

    monkeypatch.setattr(review, "call_llm", fake_llm)
    site = MagicMock()
    site.post_url.return_value = "/x"
    site.warnings = []
    index = MagicMock()
    index.has_no_outgoing.return_value = False
    index.get_post_url.return_value = "/x"
    index.get_link_keywords.return_value = profile
    index.link_keywords_for_path.return_value = "alpha, beta"
    index.find_similar.return_value = [
        {"title": "T", "url": "/t/", "path": "/blog/t.md", "score": 0.8},
    ]
    index.find_by_shared_tags.return_value = []
    index._cache = {}

    class Host(App):
        def on_mount(self):
            self.push_screen(HuginScreen(
                posts=posts, all_posts=list(posts),
                engine=Engine("t", "http://localhost/v1", "m", 30, None),
                pool={}, state={}, directory=tmp_path,
                config=HuginConfig(LinksConfig(), EmbeddingsConfig(), FrontmatterConfig()),
                site=site, index=index,
            ))

    async def go():
        async with Host().run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            await pilot.press("o")
            await pilot.pause(1.0)

    asyncio.run(go())
    return prompts, index


def test_o_writes_the_new_profile_into_keywords(tmp_path, monkeypatch):
    posts = _posts(tmp_path)

    _press_o(tmp_path, monkeypatch, posts)

    assert frontmatter.load(str(posts[0].path)).metadata["keywords"] == ["café", "hugo", "python"]
    assert posts[0].metadata["keywords"] == ["café", "hugo", "python"]


def test_o_leaves_the_file_alone_when_keywords_already_match(tmp_path, monkeypatch):
    posts = _posts(tmp_path)
    write_keywords_block(posts[0].path, ["café", "hugo", "python"])
    before = posts[0].path.read_text(encoding="utf-8")
    os.utime(posts[0].path, (1, 1))
    posts[0].metadata["keywords"] = ["café", "hugo", "python"]
    posts[0].mtime = 1

    _press_o(tmp_path, monkeypatch, posts)

    assert posts[0].path.read_text(encoding="utf-8") == before
    assert posts[0].path.stat().st_mtime == 1


def test_o_reads_edits_made_outside_hugin(tmp_path, monkeypatch):
    posts = _posts(tmp_path)
    posts[0].path.write_text('---\ntitle: "P0"\n---\n\n' + "edited " * 700, encoding="utf-8")
    os.utime(posts[0].path, (posts[0].mtime + 10, posts[0].mtime + 10))

    prompts, index = _press_o(tmp_path, monkeypatch, posts)

    assert "edited edited" in prompts[0]
    assert "word word" not in prompts[0]
    assert posts[0].content.startswith("edited")
    index.update_post.assert_called()


def test_write_keywords_block_creates_missing_frontmatter(tmp_path):
    path = tmp_path / "bare.md"
    path.write_text("Just a body.\n", encoding="utf-8")

    write_keywords_block(path, ["cats"])

    post = frontmatter.load(str(path))
    assert post.metadata == {"keywords": ["cats"]}
    assert post.content == "Just a body."


def test_build_profiles_writes_keywords(tmp_path, monkeypatch):
    path = tmp_path / "cats.md"
    path.write_text('---\ntitle: "Cats"\n---\n\nBody.\n', encoding="utf-8")
    stored: dict[str, str] = {}

    class FakeIndex:
        def __init__(self, **_):
            pass

        def _load_cache(self):
            pass

        def get_link_keywords(self, post):
            return stored.get(post.filename, "")

        def store_link_keywords(self, post, keywords):
            stored[post.filename] = keywords

    async def fake_llm(engine, prompt, **kw):
        return "cats, pets, café"

    monkeypatch.setattr(cli, "EmbeddingIndex", FakeIndex)
    monkeypatch.setattr(cli, "get_engine", lambda _: None)
    monkeypatch.setattr(cli, "call_llm", fake_llm)

    result = CliRunner().invoke(cli.build_profiles, [str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert frontmatter.load(str(path)).metadata["keywords"] == ["cats", "pets", "café"]
