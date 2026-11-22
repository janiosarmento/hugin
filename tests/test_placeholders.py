"""[img] placeholders: found, copied, and kept off published posts."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.app import App

from hugin import placeholders
from hugin.drafting import build_message, refactor_losses
from hugin.tui.placeholders_screen import PlaceholdersScreen, RemovePlaceholdersScreen
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
    assert placeholders.published_with_placeholders(tmp_path) == {published: 1, no_front: 1}

    assert placeholders.remove_from(published)
    assert published.read_text() == "---\ntitle: pt/a.md\ndraft: false\n---\nText.\n"
    assert not placeholders.remove_from(published)


# --- drafting -----------------------------------------------------------------------------


def test_draft_prompt_asks_for_placeholders_in_english():
    message = build_message([], "Write about cats")
    assert "[img]description[/img]" in message and "in English" in message
    assert "16:9" in message


def test_placeholder_words_do_not_hide_a_shorter_refactor():
    original = " ".join(["word"] * 100)
    shorter = " ".join(["word"] * 50) + "\n\n[img]" + " ".join(["detail"] * 80) + "[/img]\n"
    assert ("length", "50 words; the original has 100") in refactor_losses(original, shorter)


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


@pytest.mark.parametrize("key, answer", [("y", True), ("n", False), ("escape", False)])
def test_removal_question(key, answer):
    async def keys(app, pilot):
        assert "pt/a.md: 2" in str(app.screen.query_one("#remove-placeholders-posts").render())
        await pilot.press(key)

    assert _run(RemovePlaceholdersScreen([("pt/a.md", 2)]), keys)[0] is answer


class FakeApp:
    def __init__(self):
        self.screens = []

    def push_screen(self, screen, callback):
        self.screens.append((screen, callback))


class FakeScreen:
    _drop_published_placeholders = HuginScreen._drop_published_placeholders

    def __init__(self):
        self.app = FakeApp()
        self.notices = []
        self.synced = 0

    def notify(self, message, **kw):
        self.notices.append((message, kw.get("severity", "information")))

    def _sync_current_post(self):
        self.synced += 1


def test_sync_asks_then_removes_from_published_posts_only(tmp_path):
    published = _write(tmp_path, "a.md", "false")
    draft = _write(tmp_path, "b.md", "true")
    screen, done = FakeScreen(), []
    screen._drop_published_placeholders(tmp_path, lambda: done.append(True))

    modal, callback = screen.app.screens[0]
    assert isinstance(modal, RemovePlaceholdersScreen) and modal._posts == [("a.md", 1)] and not done
    callback(True)
    assert done == [True] and screen.synced == 1
    assert "[img]" not in published.read_text() and "[img]" in draft.read_text()
    assert screen.notices[-1][0] == "Removed 1 placeholder(s) from 1 post(s)"


def test_declining_cancels_the_sync(tmp_path):
    published = _write(tmp_path, "a.md", "false")
    screen, done = FakeScreen(), []
    screen._drop_published_placeholders(tmp_path, lambda: done.append(True))
    screen.app.screens[0][1](False)
    assert not done and "[img]" in published.read_text()
    assert screen.notices[-1][1] == "warning"


def test_no_placeholders_goes_straight_on(tmp_path):
    _write(tmp_path, "a.md", "true")
    screen, done = FakeScreen(), []
    screen._drop_published_placeholders(tmp_path, lambda: done.append(True))
    assert done == [True] and screen.app.screens == []


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
