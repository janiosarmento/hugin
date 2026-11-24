"""Merging posts: a refactor (R) that fuses other posts into the original."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from textual.app import App
from textual.widgets import Input

from hugin import drafting, drafts_only
import hugin.tui.draft_screens as ed
from hugin.config import EmbeddingsConfig, FrontmatterConfig, HuginConfig, LinksConfig
from hugin.engines import Engine
from hugin.scanner import Post
from hugin.tui.post_picker import PostPickerScreen, matches
from hugin.tui.review import HuginScreen

TITLES = [
    "Café for cats", "Litter box basics", "The naïve kitten", "Feeding an old cat",
    "Brushing long hair", "Cat toys at home", "Visiting the vet", "Moving house with cats",
    "Night zoomies",
]


def _posts(tmp_path):
    out = []
    for i, title in enumerate(TITLES):
        path = tmp_path / f"p{i}.md"
        body = f"Body of {title}. ![photo](/img/p{i}.jpg) [source](https://example.com/{i})"
        path.write_text(f"---\ntitle: {title}\n---\n{body}\n")
        out.append(Post(path, {"title": title}, body, False,
                        date=datetime.now() - timedelta(days=i + 1), mtime=path.stat().st_mtime))
    return out


# --- Picker filter ---

def test_filter_matches_words_in_any_order_without_accents():
    assert matches("kitten naive", "The naïve kitten", "p2.md")
    assert matches("CAFE", "Café for cats", "p0.md")
    assert matches("", "Anything", "x.md")
    assert matches("p7", "Moving house with cats", "p7.md")  # file name counts
    assert not matches("kitten old", "The naïve kitten", "p2.md")


def test_checked_posts_stay_visible_and_enter_confirms(tmp_path):
    posts = _posts(tmp_path)
    result = {}

    async def go():
        app = App()
        async with app.run_test(size=(120, 50)) as pilot:
            app.push_screen(
                PostPickerScreen(posts, posts[0], include_drafts=True),
                lambda r: result.setdefault("v", r),
            )
            await pilot.pause()
            screen = app.screen
            await pilot.press(*"vet")
            await pilot.pause()
            shown = [cand["title"] for cand, cb in screen._checkboxes if cb.display]
            assert shown == ["Visiting the vet"]
            next(cb for cand, cb in screen._checkboxes if cb.display).value = True
            screen.query_one("#picker-search", Input).value = "zoomies"
            await pilot.pause()
            shown = [cand["title"] for cand, cb in screen._checkboxes if cb.display]
            assert shown == ["Night zoomies", "Visiting the vet"]
            screen.query_one("#picker-search", Input).focus()
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(go())
    assert [item["title"] for item in result["v"]] == ["Visiting the vet"]


def test_empty_pick_is_an_answer_only_when_allowed(tmp_path):
    posts = _posts(tmp_path)
    for allow, expected in ((True, []), (False, "unset")):
        result = {}

        async def go():
            app = App()
            async with app.run_test(size=(120, 50)) as pilot:
                app.push_screen(
                    PostPickerScreen(posts, posts[0], allow_empty=allow),
                    lambda r: result.setdefault("v", r),
                )
                await pilot.pause()
                app.screen._confirm()
                await pilot.pause()

        asyncio.run(go())
        assert result.get("v", "unset") == expected


# --- Message and checks ---

def test_merge_message_has_every_post_and_the_merge_task(tmp_path):
    posts = _posts(tmp_path)
    message = drafting.build_message(posts[3:5], "keep it short", posts[0], merged=posts[1:3])
    assert message.count("<original>") == 3
    assert "Litter box basics" in message and "The naïve kitten" in message
    assert "This is a MERGE: the 3 posts above become ONE post" in message
    assert "This is a REFACTOR" not in message


def test_merge_losses_cover_every_post_and_use_the_merge_floor(tmp_path):
    posts = _posts(tmp_path)
    source = drafting.source_text(posts[0], posts[1:2])
    new = "Short. ![photo](/img/p0.jpg) [source](https://example.com/0)"
    losses = drafting.refactor_losses(source, new, min_words=40)
    assert ("image", "/img/p1.jpg") in losses
    assert any(kind == "external link" and "example.com/1" in d for kind, d in losses)
    assert ("length", f"{len(new.split())} words, under 40: check that no content was lost") in losses


def test_merge_floor_is_60_percent_of_the_longest_post(tmp_path):
    short, long_ = _posts(tmp_path)[:2]
    long_.content = "word " * 100
    assert drafting.merge_min_words(short, [long_]) == 60


# --- Front matter ---

def test_merged_from_stays_while_any_merged_post_exists(tmp_path):
    (tmp_path / "a.md").write_text("---\ntitle: A\n---\nBody\n")
    (tmp_path / "b.md").write_text("---\ntitle: B\n---\nBody\n")
    merge = tmp_path / "merged.md"
    merge.write_text("---\ntitle: M\ndraft: false\nmerged_from:\n- a.md\n- b.md\n---\nBody\n")

    assert drafts_only.published_with_fields(tmp_path) == {}
    (tmp_path / "a.md").unlink()
    assert drafts_only.published_with_fields(tmp_path) == {}
    (tmp_path / "b.md").unlink()
    assert drafts_only.published_with_fields(tmp_path) == {merge: ["merged_from"]}
    drafts_only.strip_fields(merge, ["merged_from"])
    assert merge.read_text() == "---\ntitle: M\ndraft: false\n---\nBody\n"


# --- The whole flow from the main screen ---

def _host(tmp_path, posts):
    site = MagicMock()
    site.post_url.return_value = "/x"
    site.warnings = []
    index = MagicMock()
    index.has_no_outgoing.return_value = False
    index.get_post_url.side_effect = lambda p: f"/{p.path.stem}/"
    index.get_link_keywords.return_value = ""
    index.link_keywords_for_path.return_value = ""
    index.rank_by_text.side_effect = RuntimeError("no embeddings in tests")

    class Host(App):
        def on_mount(self):
            self.push_screen(HuginScreen(
                posts=posts, all_posts=list(posts),
                engine=Engine("t", "http://localhost/v1", "m", 30, None),
                pool={}, state={}, directory=tmp_path,
                config=HuginConfig(LinksConfig(), EmbeddingsConfig(), FrontmatterConfig()),
                site=site, index=index,
            ))

    return Host()


def test_R_merges_picked_posts_into_one_draft(tmp_path, monkeypatch):
    seen = {}

    async def fake_ask(engine, message, system=None):
        if "category" in (system or "").lower() or "categor" in message[:200].lower():
            return "TBD"
        seen.setdefault("message", message)
        body = " ".join(
            f"![photo](/img/p{i}.jpg) [source](https://example.com/{i})" for i in (0, 6)
        )
        return "Cats in one post\n\n" + ("word " * 40) + body

    async def no_check(engine, body, original=None, **kwargs):
        seen["checked_against"] = original
        return body, [], []

    monkeypatch.setattr(drafting, "call_llm", fake_ask)
    monkeypatch.setattr(ed.verify, "check_and_fix", no_check)
    posts = _posts(tmp_path)

    async def go():
        app = _host(tmp_path, posts)
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.press("R")
            await pilot.pause()
            prompt = app.screen
            assert isinstance(prompt, ed.DraftPromptScreen)
            await pilot.click("#btn-draft-merge")
            await pilot.pause()
            picker = app.screen
            assert isinstance(picker, PostPickerScreen)
            await pilot.press(*"vet visiting")
            await pilot.pause()
            next(cb for _, cb in picker._checkboxes if cb.display).value = True
            await pilot.press("enter")
            await pilot.pause()
            assert app.screen is prompt
            assert [p.filename for p in prompt.merged] == ["p6.md"]
            assert "Merge — Café for cats + 1 post" in str(
                prompt.query_one("#draft-title").render()
            )
            # The merged post is no writing sample
            assert all(p.filename != "p6.md" for p in prompt._recent + prompt._pool)
            prompt.query_one("#draft-prompt").focus()
            await pilot.press(*"fuse", "ctrl+s")
            await pilot.pause(0.8)
            assert isinstance(app.screen, HuginScreen)

            draft = (tmp_path / "cats-in-one-post.md").read_text()
            assert "refactor_of: p0.md" in draft
            assert "merged_from:\n- p6.md" in draft
            assert seen["message"].count("<original>") == 2
            assert "Body of Visiting the vet" in seen["message"]
            assert "Body of Visiting the vet" not in seen["message"].split("<original>")[0]
            assert "Body of Visiting the vet" in seen["checked_against"]

            # Deleting the merged post suggests the merge as redirect target
            vet = next(p for p in screen.all_posts if p.filename == "p6.md")
            replacement = screen._replacement_for(vet)
            assert replacement and replacement["title"] == "Cats in one post"

    asyncio.run(go())
