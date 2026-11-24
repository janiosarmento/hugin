"""Editorial checklist: lastmod only on real changes, kept links read, rules sent to the LLM."""

import asyncio

import frontmatter

from hugin import drafting, prompts, verify
from hugin import research as web
from hugin.scanner import Post
from hugin.writer import save_post, write_summary, write_tags

POST = "---\ntitle: Café\nlastmod: 2020-01-01T00:00:00\ntags:\n- cats\n---\nBody\n"


def _lastmod(path):
    return str(frontmatter.load(str(path)).metadata["lastmod"])


# --- lastmod ---

def test_tags_and_description_leave_lastmod_alone(tmp_path):
    path = tmp_path / "post.md"
    path.write_text(POST)
    write_tags(path, ["cats", "food"])
    write_summary(path, "A short summary.")
    assert _lastmod(path) == "2020-01-01 00:00:00"


def test_a_new_body_or_title_moves_lastmod(tmp_path):
    for change in ("content", "title"):
        path = tmp_path / f"{change}.md"
        path.write_text(POST)
        post = frontmatter.load(str(path))
        if change == "content":
            post.content = "New body"
        else:
            post.metadata["title"] = "Résumé"
        save_post(path, post)
        assert not _lastmod(path).startswith("2020"), change


def test_a_new_file_gets_lastmod(tmp_path):
    path = tmp_path / "new.md"
    save_post(path, frontmatter.Post("Body", title="New"))
    assert "lastmod" in frontmatter.load(str(path)).metadata


# --- links kept from the original ---

def test_kept_links_become_sources_and_dead_ones_are_reported(monkeypatch):
    async def fake_read(urls):
        pages = {
            "https://ok.example/a": web.PageRead("https://ok.example/a", text="Cats sleep 15 hours. " * 30),
            "https://gone.example/b": web.PageRead("https://gone.example/b", error="HTTP 404"),
            "https://robots.example/c": web.PageRead("https://robots.example/c", error="HTTP 403"),
        }
        return [pages[u] for u in urls]

    monkeypatch.setattr(verify, "read_links", fake_read)
    draft = (
        "[a](https://ok.example/a) [b](https://gone.example/b) "
        "[c](https://robots.example/c) [found](https://found.example/d)"
    )
    research = web.Research("q", "English", "US", "en", None)
    research.results = [web.SearchResult(1, "Found", "https://found.example/d", "")]
    sources, dead = asyncio.run(verify.kept_link_sources(draft, research, None))
    assert [(n, url) for n, _, url, _ in sources] == [(101, "https://ok.example/a")]
    # A refused robot is not a dead page
    assert dead == [("dead link", "https://gone.example/b  (HTTP 404)")]


# --- rules sent along ---

def test_every_draft_gets_the_structure_and_health_rules(tmp_path):
    message = drafting.build_message([], "write about cat food")
    assert prompts.text("writer.structure") in message
    assert prompts.text("writer.health") in message
    assert "no clickbait" in message


def test_vague_and_health_are_checked_even_without_references():
    prompt = verify.build_verify_prompt("Studies show cats can eat grapes.", facts=False)
    assert '"vague"' in prompt and '"health"' in prompt
    assert verify.KINDS["vague"] == "vague attribution"
    assert verify.KINDS["health"] == "health risk"


def test_merge_and_refactor_never_pad(tmp_path):
    original = Post(tmp_path / "a.md", {"title": "A"}, "word " * 50, False)
    other = Post(tmp_path / "b.md", {"title": "B"}, "word " * 20, False)
    assert "Never pad it" in drafting.build_message([], "x", original)
    assert "nothing may be padded" in drafting.build_message([], "x", original, merged=[other])
