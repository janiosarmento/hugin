"""write_flag sets or drops a boolean frontmatter flag and leaves the rest of the file alone."""

from hugin.writer import write_flag


def _write(tmp_path, text):
    path = tmp_path / "post.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_true_goes_before_description_and_tags(tmp_path):
    path = _write(tmp_path, "---\ntitle: 'Café'\ndate: 2026-01-02T10:00:00\ndescription: Short\ntags:\n- cats\n---\nBody\n")
    write_flag(path, "factcheck", True)
    assert path.read_text() == (
        "---\ntitle: 'Café'\ndate: 2026-01-02T10:00:00\nfactcheck: true\n"
        "description: Short\ntags:\n- cats\n---\nBody\n"
    )


def test_true_without_tail_fields_goes_at_the_end(tmp_path):
    path = _write(tmp_path, "---\ntitle: Post\n---\nBody\n")
    write_flag(path, "factcheck", True)
    assert path.read_text() == "---\ntitle: Post\nfactcheck: true\n---\nBody\n"


def test_existing_entry_is_replaced_in_place(tmp_path):
    path = _write(tmp_path, "---\ntitle: Post\nfactcheck: false\ntags: []\n---\nBody\n")
    write_flag(path, "factcheck", True)
    assert path.read_text() == "---\ntitle: Post\nfactcheck: true\ntags: []\n---\nBody\n"


def test_false_removes_the_entry(tmp_path):
    path = _write(tmp_path, "---\ntitle: Post\nfactcheck: true\n---\nBody\n")
    write_flag(path, "factcheck", False)
    assert path.read_text() == "---\ntitle: Post\n---\nBody\n"


def test_false_without_entry_does_not_touch_the_file(tmp_path):
    text = "---\ntitle:   Post\n---\nBody\n"
    path = _write(tmp_path, text)
    write_flag(path, "factcheck", False)
    assert path.read_text() == text


def test_lastmod_is_left_alone(tmp_path):
    path = _write(tmp_path, "---\ntitle: Post\nlastmod: 2020-01-01T00:00:00\n---\nBody\n")
    write_flag(path, "factcheck", True)
    assert "lastmod: 2020-01-01T00:00:00\n" in path.read_text()


def test_body_line_with_the_key_is_not_touched(tmp_path):
    path = _write(tmp_path, "---\ntitle: Post\n---\nfactcheck: true\n")
    write_flag(path, "factcheck", False)
    assert path.read_text() == "---\ntitle: Post\n---\nfactcheck: true\n"


def test_toml_frontmatter(tmp_path):
    path = _write(tmp_path, '+++\ntitle = "Post"\ntags = ["a"]\n+++\nBody\n')
    write_flag(path, "factcheck", True)
    assert path.read_text() == '+++\ntitle = "Post"\nfactcheck = true\ntags = ["a"]\n+++\nBody\n'
    write_flag(path, "factcheck", False)
    assert path.read_text() == '+++\ntitle = "Post"\ntags = ["a"]\n+++\nBody\n'


def test_file_without_frontmatter_gets_one(tmp_path):
    path = _write(tmp_path, "Body\n")
    write_flag(path, "factcheck", True)
    assert path.read_text() == "---\nfactcheck: true\n---\nBody\n"
