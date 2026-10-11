"""write_keywords_block changes only the keywords entry of a post's frontmatter."""

from hugin.writer import write_keywords_block

BASE = (
    '---\n'
    'title: "Cats"\n'
    'author: "Janio Sarmento"\n'
    'description: A long description that the editor reflows differently when the file is saved in full\n'
    'tags:\n'
    '  - animais\n'
    '  - casa\n'
    '---\n'
    '\n'
    'Body, with "quotes".\n'
)


def test_appends_block_at_the_end_of_the_frontmatter(tmp_path):
    path = tmp_path / "post.md"
    path.write_text(BASE, encoding="utf-8")

    write_keywords_block(path, ["cats", "café"])

    assert path.read_text(encoding="utf-8") == BASE.replace(
        "  - casa\n---\n", "  - casa\nkeywords:\n- cats\n- café\n---\n",
    )


def test_replaces_block_style_keywords_and_keeps_the_rest(tmp_path):
    path = tmp_path / "post.md"
    path.write_text(BASE.replace("  - casa\n---\n", "  - casa\nkeywords:\n- velho\n- antigo\n---\n"), encoding="utf-8")

    write_keywords_block(path, ["novo"])

    assert path.read_text(encoding="utf-8") == BASE.replace("  - casa\n---\n", "  - casa\nkeywords:\n- novo\n---\n")


def test_replaces_flow_style_keywords(tmp_path):
    path = tmp_path / "post.md"
    path.write_text(BASE.replace("  - casa\n---\n", "  - casa\nkeywords: [velho, antigo]\n---\n"), encoding="utf-8")

    write_keywords_block(path, ["novo"])

    assert path.read_text(encoding="utf-8") == BASE.replace("  - casa\n---\n", "  - casa\nkeywords:\n- novo\n---\n")


def test_keywords_followed_by_another_field_keep_that_field(tmp_path):
    text = BASE.replace("tags:\n  - animais\n  - casa\n", "keywords:\n- velho\nlastmod: 2024-01-01\ntags:\n  - animais\n")
    path = tmp_path / "post.md"
    path.write_text(text, encoding="utf-8")

    write_keywords_block(path, ["novo"])

    assert path.read_text(encoding="utf-8") == text.replace("keywords:\n- velho\n", "keywords:\n- novo\n")


def test_quotes_values_that_need_it(tmp_path):
    path = tmp_path / "post.md"
    path.write_text(BASE, encoding="utf-8")

    write_keywords_block(path, ["yes", "1990", "a: b"])

    import frontmatter
    assert frontmatter.load(str(path)).metadata["keywords"] == ["yes", "1990", "a: b"]
