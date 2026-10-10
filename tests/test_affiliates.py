import asyncio

import pytest
from textual.app import App
from textual.widgets import DataTable, Input

import hugin.affiliates as aff
import hugin.tui.affiliates_screen as screen


@pytest.fixture
def aff_file(tmp_path, monkeypatch):
    path = tmp_path / "affiliates.toml"
    path.write_text(
        "# my notes\n"
        '"litter box" = "https://amzn.to/aaa"\n'
        'scratcher = "https://amzn.to/bbb"\n'
    )
    monkeypatch.setattr(aff, "AFFILIATES_PATH", path)
    monkeypatch.setattr(aff, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(screen, "AFFILIATES_PATH", path)
    return path


def test_save_add_update_rename_remove_keep_comments(aff_file):
    aff.save_affiliate("food", "https://amzn.to/ccc")
    aff.save_affiliate("scratcher", "https://amzn.to/new")
    aff.save_affiliate("tower", "https://amzn.to/new", old_keyword="scratcher")
    assert aff.load_affiliates() == {
        "litter box": "https://amzn.to/aaa",
        "food": "https://amzn.to/ccc",
        "tower": "https://amzn.to/new",
    }
    aff.remove_affiliate("food")
    assert "food" not in aff.load_affiliates()
    assert aff_file.read_text().startswith("# my notes\n")


def test_accented_keywords_round_trip(aff_file):
    aff.save_affiliate("ração para gatos", "https://amzn.to/zzz")
    assert aff.load_affiliates()["ração para gatos"] == "https://amzn.to/zzz"


def test_creates_missing_file_when_saving(tmp_path, monkeypatch):
    path = tmp_path / "sub" / "affiliates.toml"
    monkeypatch.setattr(aff, "AFFILIATES_PATH", path)
    monkeypatch.setattr(aff, "CONFIG_DIR", path.parent)
    aff.save_affiliate("toy", "https://amzn.to/t")
    assert aff.load_affiliates() == {"toy": "https://amzn.to/t"}


@pytest.mark.parametrize("keyword,url,editing,ok", [
    ("", "https://x.co", None, False),
    ("a", "amzn.to/x", None, False),
    ("a", "https://x.co/a b", None, False),
    ("LITTER BOX", "https://x.co", None, False),   # duplicate, case-insensitive
    ("litter box", "https://x.co", "litter box", True),  # editing itself
    ("new", "https://x.co", None, True),
])
def test_validate(keyword, url, editing, ok):
    existing = {"litter box": "https://amzn.to/aaa"}
    assert (aff.validate_affiliate(keyword, url, existing, editing) is None) == ok


def test_screen_add_edit_remove(aff_file):
    async def go():
        app = App()
        async with app.run_test(size=(120, 40)) as pilot:
            app.push_screen(screen.AffiliatesScreen())
            await pilot.pause()
            table = app.screen.query_one("#af-table", DataTable)
            assert table.row_count == 2

            await pilot.press("a")
            await pilot.pause()
            await pilot.press(*"food", "enter", *"https://amzn.to/ccc", "enter")
            await pilot.pause()
            assert table.row_count == 3
            assert table.cursor_row == 0  # sorted: "food" comes first, cursor lands on it

            await pilot.press("e")  # edit "food": change the URL
            await pilot.pause()
            url = app.screen.query_one("#input-url", Input)
            url.value = "https://amzn.to/ddd"
            await pilot.press("enter", "enter")  # keyword field -> URL field -> save
            await pilot.pause()
            assert aff.load_affiliates()["food"] == "https://amzn.to/ddd"

            await pilot.press("delete")
            await pilot.pause()
            await pilot.click("#btn-remove")
            await pilot.pause()
            assert "food" not in aff.load_affiliates()
            assert table.row_count == 2

    asyncio.run(go())


def test_screen_rejects_invalid_and_cancel_keeps_file(aff_file):
    before = aff_file.read_text()

    async def go():
        app = App()
        async with app.run_test(size=(120, 40)) as pilot:
            app.push_screen(screen.AffiliatesScreen())
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            await pilot.press(*"x", "enter", *"not-a-url", "enter")
            await pilot.pause()
            assert isinstance(app.screen, screen.AffiliateEditScreen)  # still open
            await pilot.press("escape")
            await pilot.pause()

    asyncio.run(go())
    assert aff_file.read_text() == before


def test_screen_lists_alphabetically_ignoring_case_and_accents(aff_file):
    aff.save_affiliate("água", "https://amzn.to/1")
    aff.save_affiliate("Zebra", "https://amzn.to/2")
    aff.save_affiliate("bowl", "https://amzn.to/3")

    async def go():
        app = App()
        async with app.run_test(size=(120, 40)) as pilot:
            app.push_screen(screen.AffiliatesScreen())
            await pilot.pause()
            table = app.screen.query_one("#af-table", DataTable)
            names = [table.get_row_at(i)[0] for i in range(table.row_count)]
            assert names == ["água", "bowl", "litter box", "scratcher", "Zebra"]
            # actions follow the displayed order, not the file order
            table.move_cursor(row=4)
            await pilot.press("c")
            await pilot.pause()

    asyncio.run(go())
