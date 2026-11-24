"""[img] placeholders: found, copied, and kept off published posts."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from textual.app import App

from hugin import placeholders
from hugin.drafting import build_message, refactor_losses
from hugin.tui.placeholders_screen import PlaceholdersScreen
from hugin.tui.review import HuginScreen

POST = """\
---
title: Cats
draft: false
---
Intro paragraph.

[img]A British Shorthair cat on the lap of a smiling girl,
warm afternoon light. Aspect ratio 16:9.[/img]

Middle text [IMG]an inline one[/img] goes on.

```markdown
[img]an example inside a fence[/img]
```

Write `[img]like this[/img]` to mark a spot.

[img]The last one.[/img]
"""


def test_descriptions_skip_code_and_join_lines():
    assert placeholders.descriptions(POST) == [
        "A British Shorthair cat on the lap of a smiling girl, warm afternoon light. Aspect ratio 16:9.",
        "an inline one",
        "The last one.",
    ]


def test_remove_takes_whole_lines_and_leaves_code_alone():
    assert placeholders.remove(POST) == """\
---
title: Cats
draft: false
---
Intro paragraph.

Middle text goes on.

```markdown
[img]an example inside a fence[/img]
```

Write `[img]like this[/img]` to mark a spot.
"""


def test_remove_never_runs_from_one_placeholder_to_the_next():
    text = "A [img]one[/img] b.\n[img]two[/img]\n\nEnd.\n"
    assert placeholders.remove(text) == "A b.\nEnd.\n"
    assert placeholders.remove("No placeholders [img] here.\n") == "No placeholders [img] here.\n"
    assert placeholders.descriptions("[img]  [/img]") == []


def _write(folder: Path, name: str, draft, body="Text.\n\n[img]A photo.[/img]\n") -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    front = "" if draft is None else f"---\ntitle: {name}\ndraft: {draft}\n---\n"
    path.write_text(front + body)
    return path


def test_only_published_posts_count(tmp_path):
    published = _write(tmp_path, "pt/a.md", "false")
    no_front = _write(tmp_path, "b.md", None)
    _write(tmp_path, "c.md", "true")
    _write(tmp_path, "d.md", '"true"')
    _write(tmp_path, "e.md", "false", body="Clean.\n")
    in_code = _write(tmp_path, "f.md", "false", body="Use `[img]a cat[/img]` like this.\n")
    unclosed = _write(tmp_path, "g.md", "false", body="A stray [IMG] marker.\n")
    assert placeholders.published_with_marker(tmp_path) == [no_front, in_code, unclosed, published]


# --- drafting -----------------------------------------------------------------------------


def test_draft_prompt_asks_for_placeholders_in_english():
    message = build_message([], "Write about cats")
    assert "[img]description[/img]" in message and "in English" in message
    assert "16:9" in message


def test_new_posts_and_refactors_open_with_a_featured_image_placeholder(tmp_path):
    original = SimpleNamespace(path=tmp_path / "old.md", metadata={"title": "Old"}, content="Old text.")
    for message in (build_message([], "Write about cats"),
                    build_message([], "Rewrite it", original=original)):
        assert "Start the body with one placeholder for the post's featured image" in message


def test_a_field_holding_a_placeholder_is_shown_as_text():
    from rich.console import Console
    from rich.table import Table

    table = Table()
    for _ in range(3):
        table.add_column()
    HuginScreen._add_field_row(table, "prompt", "Cats [img]A cat on a sofa.[/img] end")
    console = Console(width=200, record=True)
    console.print(table)
    assert "[img]A cat on a sofa.[/img]" in console.export_text()


def test_placeholder_words_do_not_hide_a_shorter_refactor():
    original = " ".join(["word"] * 100)
    shorter = " ".join(["word"] * 50) + "\n\n[img]" + " ".join(["detail"] * 80) + "[/img]\n"
    assert ("length", "50 words; the original has 100: check that no content was lost") in refactor_losses(original, shorter)


# --- screens ------------------------------------------------------------------------------


def _run(screen, keys):
    result = {}
    copied = []

    async def go():
        app = App()
        app.copy_to_clipboard = copied.append
        async with app.run_test(size=(120, 40)) as pilot:
            app.push_screen(screen, lambda r: result.setdefault("v", r))
            await pilot.pause()
            await keys(app, pilot)
            await pilot.pause(0.2)

    asyncio.run(go())
    return result.get("v", "unset"), copied


def test_enter_copies_the_highlighted_description():
    async def keys(app, pilot):
        await pilot.press("down", "enter")
        await pilot.press("escape")

    result, copied = _run(PlaceholdersScreen("Cats [draft]", ["first", "second [x]"]), keys)
    assert copied == ["second [x]"] and result is None


class FakeApp:
    def __init__(self):
        self.screens = []

    def push_screen(self, screen, callback=None):
        self.screens.append((screen, callback))


class FakeScreen:
    PLACEHOLDER_TITLES_SHOWN = HuginScreen.PLACEHOLDER_TITLES_SHOWN
    _published_placeholders_block = HuginScreen._published_placeholders_block

    def __init__(self):
        self.app = FakeApp()
        self.notices = []

    def notify(self, message, **kw):
        self.notices.append((message, kw.get("severity", "information")))


def test_a_published_placeholder_blocks_the_sync_and_names_the_post(tmp_path):
    published = tmp_path / "a.md"
    published.write_text("---\ntitle: Cats [at] home\n---\n[img]A photo.[/img]\n")
    no_title = _write(tmp_path, "pt/b.md", None)
    _write(tmp_path, "c.md", "true")
    before = published.read_text()
    screen = FakeScreen()
    assert screen._published_placeholders_block(tmp_path)
    message, severity = screen.notices[-1]
    assert severity == "error"
    assert message.splitlines()[1:] == ["  Cats \\[at] home", "  pt/b.md", "These block the push."]
    assert no_title.exists()
    assert published.read_text() == before  # nothing is removed


def test_the_block_shows_five_titles_and_counts_the_rest(tmp_path):
    for n in range(8):
        _write(tmp_path, f"p{n}.md", "false")
    (tmp_path / "x.md").write_text("[img]no front matter[/img]\n")
    screen = FakeScreen()
    assert screen._published_placeholders_block(tmp_path)
    lines = screen.notices[-1][0].splitlines()
    assert lines[1:6] == [f"  p{n}.md" for n in range(5)]
    assert lines[-1] == "These and 4 more block the push."


def test_drafts_alone_do_not_block(tmp_path):
    _write(tmp_path, "a.md", "true")
    screen = FakeScreen()
    assert not screen._published_placeholders_block(tmp_path)
    assert screen.notices == []


def test_the_block_comes_before_any_file_is_edited(tmp_path, monkeypatch):
    from hugin.tui import review

    _write(tmp_path, "a.md", "false")
    touched = []
    monkeypatch.setattr(review.images, "posts_with_typora_keys", lambda c: touched.append(c) or [])
    monkeypatch.setattr(review, "find_site_root", lambda d: tmp_path)
    screen = FakeScreen()
    screen._state, screen.directory = review.STATE_BROWSING, tmp_path
    HuginScreen.action_git_sync(screen)
    assert screen.notices and screen.notices[-1][1] == "error"
    assert touched == [] and screen.app.screens == []


def test_review_shortcut_opens_the_list(tmp_path):
    post = _write(tmp_path, "a.md", "true")
    screen = SimpleNamespace(
        _state="browsing", posts=[SimpleNamespace(path=post, metadata={"title": "Cats"})],
        current_index=0, app=FakeApp(), notify=lambda *a, **k: None,
    )
    screen.app.push_screen = lambda s: screen.app.screens.append(s)
    from hugin.tui import review

    screen._state = review.STATE_BROWSING
    HuginScreen.action_image_placeholders(screen)
    assert isinstance(screen.app.screens[0], PlaceholdersScreen)
    assert screen.app.screens[0]._descriptions == ["A photo."]
