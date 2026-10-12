"""The post picker hands back the post path, so the link finder can read its keywords."""

from types import SimpleNamespace

from textual.widgets import Checkbox

from hugin.scanner import Post
from hugin.tui.post_picker import PostPickerScreen


def test_confirmed_selection_carries_the_post_path(tmp_path):
    post = Post(tmp_path / "litter.md", {"title": "Litter"}, "", False)
    screen = PostPickerScreen.__new__(PostPickerScreen)  # skip compose; only the result matters
    screen._checkboxes = [
        ({"post": post, "title": "Litter", "url": "/litter-box/"}, Checkbox("Litter", value=True)),
    ]
    results = []
    screen.dismiss = results.append

    screen.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-confirm")))

    assert results == [[{
        "title": "Litter", "url": "/litter-box/", "path": str(post.path.resolve()),
    }]]
