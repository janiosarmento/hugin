"""Draft-only front matter fields, taken out of published posts."""

from hugin import drafts_only

FRONT = """\
---
title: 'Two cats: one fat, one thin'
draft: {draft}
prompt: 'Write a post about:


  Two cats in one house


  Describe the featured image'
lastmod: 2026-11-22T22:01:52
tags:
- cats
---
Body with prompt: in it.
"""


def test_remove_field_cuts_a_multi_line_value_and_nothing_else():
    text = FRONT.format(draft="false")
    assert drafts_only.remove_field(text, "prompt") == (
        "---\ntitle: 'Two cats: one fat, one thin'\ndraft: false\n"
        "lastmod: 2026-11-22T22:01:52\ntags:\n- cats\n---\nBody with prompt: in it.\n"
    )
    assert drafts_only.remove_field(text, "missing") == text
    assert drafts_only.remove_field("No front matter\nprompt: x\n", "prompt") == "No front matter\nprompt: x\n"


def test_remove_field_refuses_a_cut_that_would_change_other_fields():
    # A value whose continuation starts at column 0 is not cut blindly
    odd = "---\nprompt: |\n  first\nsecond: line\n---\nBody\n"
    assert drafts_only.remove_field(odd, "prompt") == "---\nsecond: line\n---\nBody\n"
    broken = "---\nprompt: 'open\nnext: 1\n---\n"
    assert drafts_only.remove_field(broken, "prompt") == broken


def test_only_published_posts_lose_the_prompt(tmp_path):
    published = tmp_path / "a.md"
    published.write_text(FRONT.format(draft="false"))
    draft = tmp_path / "b.md"
    draft.write_text(FRONT.format(draft="true"))
    (tmp_path / "c.md").write_text("---\ntitle: T\n---\nprompt: in the body\n")

    assert drafts_only.published_with_fields(tmp_path) == {published: ["prompt"]}
    assert drafts_only.strip_fields(published, ["prompt"])
    assert "prompt:" not in published.read_text().split("---")[1]
    assert not drafts_only.strip_fields(published, ["prompt"])
    assert "prompt: 'Write" in draft.read_text()
    assert drafts_only.published_with_fields(tmp_path) == {}


def test_refactor_of_goes_once_the_original_is_gone(tmp_path):
    (tmp_path / "pt").mkdir()
    original = tmp_path / "pt/old-post.md"
    original.write_text("---\ntitle: Old\n---\nBody\n")
    refactor = tmp_path / "pt/new-post.md"
    refactor.write_text("---\ntitle: New\ndraft: false\nrefactor_of: old-post.md\nprompt: Rewrite it\n---\nBody\n")

    assert drafts_only.published_with_fields(tmp_path) == {refactor: ["prompt"]}
    original.unlink()
    assert drafts_only.published_with_fields(tmp_path) == {refactor: ["prompt", "refactor_of"]}
    drafts_only.strip_fields(refactor, ["prompt", "refactor_of"])
    assert refactor.read_text() == "---\ntitle: New\ndraft: false\n---\nBody\n"
