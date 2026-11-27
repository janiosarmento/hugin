"""Published posts with a last-modified date in the future block a sync."""

from datetime import datetime, timedelta, timezone

from hugin import future_lastmod

# 2026-11-27 12:00 at UTC-3
NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone(timedelta(hours=-3)))


def _write(folder, name, lastmod, draft="false", key="lastmod"):
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    field = "" if lastmod is None else f"{key}: {lastmod}\n"
    path.write_text(f"---\ntitle: {path.stem}\ndraft: {draft}\n{field}---\nBody.\n")
    return path


def _found(folder):
    return [p.path for p in future_lastmod.find(folder, NOW)]


def test_only_published_posts_with_a_future_lastmod_count(tmp_path):
    ahead = _write(tmp_path, "en/ahead.md", "2027-11-27T10:00:00-03:00")
    _write(tmp_path, "past.md", "2026-11-26T10:00:00-03:00")
    _write(tmp_path, "draft.md", "2027-11-27T10:00:00-03:00", draft="true")
    _write(tmp_path, "quoted-draft.md", "2027-11-27", draft='"true"')
    _write(tmp_path, "none.md", None)
    _write(tmp_path, "garbage.md", "not a date")
    assert _found(tmp_path) == [ahead]


def test_timezones_are_compared_as_instants(tmp_path):
    # 14:30 UTC is 11:30 at UTC-3: past; 15:30 UTC is 12:30: future
    _write(tmp_path, "a.md", "2026-11-27T14:30:00Z")
    later = _write(tmp_path, "b.md", "2026-11-27T15:30:00+00:00")
    assert _found(tmp_path) == [later]


def test_a_naive_date_is_local_time(tmp_path):
    _write(tmp_path, "a.md", "2026-11-27T11:59:00")
    later = _write(tmp_path, "b.md", "2026-11-27T12:01:00")
    assert _found(tmp_path) == [later]


def test_a_bare_day_is_future_only_from_tomorrow(tmp_path):
    _write(tmp_path, "today.md", "2026-11-27")
    tomorrow = _write(tmp_path, "tomorrow.md", "2026-11-28")
    assert _found(tmp_path) == [tomorrow]


def test_the_modified_alias_and_any_key_case_count(tmp_path):
    a = _write(tmp_path, "a.md", "2027-01-01", key="modified")
    b = _write(tmp_path, "b.md", "2027-01-01", key="lastMod")
    assert _found(tmp_path) == [a, b]


def test_toml_front_matter_is_checked(tmp_path):
    path = tmp_path / "t.md"
    path.write_text('+++\ntitle = "T"\nlastmod = 2027-11-27T10:00:00-03:00\n+++\nBody.\n')
    assert _found(tmp_path) == [path]


def test_the_title_and_date_are_reported(tmp_path):
    _write(tmp_path, "cafe.md", "2027-11-27T10:00:00-03:00")
    [post] = future_lastmod.find(tmp_path, NOW)
    assert post.title == "cafe"
    assert post.lastmod.year == 2027


def test_a_toml_draft_is_skipped(tmp_path):
    path = tmp_path / "t.md"
    path.write_text('+++\ntitle = "T"\ndraft = true\nlastmod = 2027-11-27\n+++\nBody.\n')
    assert _found(tmp_path) == []


# --- the sync -----------------------------------------------------------------------------


class FakeApp:
    def __init__(self):
        self.screens = []

    def push_screen(self, screen, callback=None):
        self.screens.append((screen, callback))


class FakeScreen:
    from hugin.tui.review import HuginScreen as _H

    PLACEHOLDER_TITLES_SHOWN = _H.PLACEHOLDER_TITLES_SHOWN
    _published_placeholders_block = _H._published_placeholders_block
    _future_lastmod_block = _H._future_lastmod_block

    def __init__(self):
        self.app = FakeApp()
        self.notices = []

    def notify(self, message, **kw):
        self.notices.append((message, kw.get("severity", "information")))


def test_a_future_lastmod_blocks_the_sync_and_names_the_post(tmp_path):
    _write(tmp_path, "en/a.md", "2099-01-02T03:04:00")
    screen = FakeScreen()
    assert screen._future_lastmod_block(tmp_path)
    message, severity = screen.notices[-1]
    assert severity == "error"
    assert message.splitlines()[1:] == ["  a (2099-01-02 03:04)", "These block the push."]


def test_the_block_shows_five_posts_and_counts_the_rest(tmp_path):
    for n in range(7):
        _write(tmp_path, f"p{n}.md", "2099-01-01")
    screen = FakeScreen()
    assert screen._future_lastmod_block(tmp_path)
    lines = screen.notices[-1][0].splitlines()
    assert lines[1:6] == [f"  p{n} (2099-01-01 00:00)" for n in range(5)]
    assert lines[-1] == "These and 2 more block the push."


def test_past_dates_do_not_block(tmp_path):
    _write(tmp_path, "a.md", "2020-01-01")
    screen = FakeScreen()
    assert not screen._future_lastmod_block(tmp_path)
    assert screen.notices == []


def test_the_sync_stops_before_any_file_is_edited(tmp_path, monkeypatch):
    from hugin.tui import review

    _write(tmp_path, "a.md", "2099-01-01")
    touched = []
    monkeypatch.setattr(review.images, "posts_with_typora_keys", lambda c: touched.append(c) or [])
    monkeypatch.setattr(review, "find_site_root", lambda d: tmp_path)
    screen = FakeScreen()
    screen._state, screen.directory = review.STATE_BROWSING, tmp_path
    review.HuginScreen.action_git_sync(screen)
    assert "in the future" in screen.notices[-1][0]
    assert touched == [] and screen.app.screens == []


# --- stamping -----------------------------------------------------------------------------


def test_stamp_replaces_only_the_lastmod_line():
    from hugin.writer import stamp_lastmod

    when = datetime(2026, 11, 27, 10, 0)
    text = "---\ntitle: 'T'\nlastMod: 2099-01-01\ntags: [a]\n---\nlastmod: in the body\n"
    assert stamp_lastmod(text, when) == (
        "---\ntitle: 'T'\nlastMod: 2026-11-27T10:00:00\ntags: [a]\n---\nlastmod: in the body\n")


def test_stamp_adds_a_missing_lastmod_and_skips_text_without_front_matter():
    from hugin.writer import stamp_lastmod

    when = datetime(2026, 11, 27, 10, 0)
    assert stamp_lastmod("---\ntitle: T\n---\nBody\n", when) == "---\ntitle: T\nlastmod: 2026-11-27T10:00:00\n---\nBody\n"
    assert stamp_lastmod("Just text\n", when) == "Just text\n"
    assert stamp_lastmod("---\nunclosed\n", when) == "---\nunclosed\n"
