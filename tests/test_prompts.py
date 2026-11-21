"""Prompt registry, per-site overrides and the prompts settings screen."""

import asyncio

from textual.app import App
from textual.widgets import Select, TextArea

import hugin.project as project
from hugin import prompts
from hugin.llm import build_suggest_prompt
from hugin.project import ProjectConfig, load_project, save_project, settings_path
from hugin.tui.project_settings import PromptsScreen


def _blog(monkeypatch, tmp_path, config=None):
    monkeypatch.setattr(project, "PROJECTS_DIR", tmp_path / "projects")
    blog = tmp_path / "blog"
    blog.mkdir()
    save_project(blog, config or ProjectConfig())
    return blog


def test_every_default_template_uses_exactly_its_placeholders():
    for key, spec in prompts.PROMPTS.items():
        if spec.fields is None:
            continue
        assert prompts.check(key, spec.default) == (None, []), key
        spec.default.format(**{f: "x" for f in spec.fields})


def test_an_override_is_sent_instead_of_the_default():
    prompts.activate({"links.suggest": "Topics for {title}.{existing}{content}"})
    assert build_suggest_prompt("Cats", "", []) == "Topics for Cats."


def test_overrides_equal_to_the_default_or_unknown_are_dropped():
    default = prompts.PROMPTS["tags.system"].default
    assert prompts.only_changed({"tags.system": default, "nope": "x", "tags.user": " "}) == {}


def test_a_broken_override_falls_back_to_the_default():
    prompts.activate({"writer.request": "Ask: {question}"})
    assert prompts.render("writer.request", request="R") == "Request:\n\nR"


def test_check_reports_unknown_placeholders_bad_braces_and_left_out_ones():
    error, _ = prompts.check("writer.request", "{request} {question}")
    assert "{question}" in error and "{request}" in error
    error, _ = prompts.check("links.anchor_user", 'Return [{"target_url": ...}] {body}')
    assert "doubled" in error
    assert prompts.check("writer.request", "Do it.") == (None, ["request"])
    assert prompts.check("tags.system", "Any {brace} goes") == (None, [])


def test_overrides_round_trip_through_the_settings_file(monkeypatch, tmp_path):
    text = "Line one with 'quotes', \"double\" and a \\ backslash\nLine two: {pool} {content}"
    config = ProjectConfig(prompts={
        "tags.user": text,
        "tags.system": prompts.PROMPTS["tags.system"].default,  # not an override
        "writer.request": "Ends with a quote '{request}'",
    })
    blog = _blog(monkeypatch, tmp_path, config)
    written = settings_path(blog).read_text()
    assert "[prompts]" in written and '"tags.system"' not in written
    assert "'''\nLine one" in written  # multi-line text stays readable in a diff

    prompts.activate({})
    loaded = load_project(blog)
    assert loaded.prompts == {"tags.user": text, "writer.request": "Ends with a quote '{request}'"}
    assert prompts.text("tags.user") == text  # loading the site activates its prompts


def test_a_site_without_overrides_writes_no_prompts_section(monkeypatch, tmp_path):
    blog = _blog(monkeypatch, tmp_path)
    assert "[prompts]" not in settings_path(blog).read_text()


def _edit(blog, config, edit):
    async def go():
        app = App()
        async with app.run_test(size=(140, 50)) as pilot:
            app.push_screen(PromptsScreen(config, blog))
            await pilot.pause()
            await edit(app.screen, pilot)
            await pilot.pause()

    asyncio.run(go())


async def _pick(screen, pilot, key):
    screen.query_one("#select-prompt", Select).value = key
    await pilot.pause()


def test_screen_keeps_edits_across_prompts_and_saves_them_together(monkeypatch, tmp_path):
    blog = _blog(monkeypatch, tmp_path)
    config = load_project(blog)

    async def edit(screen, pilot):
        await _pick(screen, pilot, "tags.system")
        screen.query_one("#input-prompt-text", TextArea).load_text("Tag it.")
        await _pick(screen, pilot, "writer.request")
        screen.query_one("#input-prompt-text", TextArea).load_text("Do this:\n\n{request}")
        await _pick(screen, pilot, "tags.system")
        assert screen.query_one("#input-prompt-text", TextArea).text == "Tag it."
        screen.query_one("#btn-save-prompts").press()

    _edit(blog, config, edit)
    assert load_project(blog).prompts == {"tags.system": "Tag it.", "writer.request": "Do this:\n\n{request}"}


def test_screen_refuses_an_unknown_placeholder(monkeypatch, tmp_path):
    blog = _blog(monkeypatch, tmp_path)
    config = load_project(blog)

    async def edit(screen, pilot):
        await _pick(screen, pilot, "writer.request")
        screen.query_one("#input-prompt-text", TextArea).load_text("{question}")
        screen.query_one("#btn-save-prompts").press()
        await pilot.pause()
        assert isinstance(pilot.app.screen, PromptsScreen)  # still open, nothing saved

    _edit(blog, config, edit)
    assert load_project(blog).prompts == {}


def test_restore_default_drops_the_override(monkeypatch, tmp_path):
    blog = _blog(monkeypatch, tmp_path, ProjectConfig(prompts={"tags.system": "Tag it."}))
    config = load_project(blog)

    async def edit(screen, pilot):
        await _pick(screen, pilot, "tags.system")
        assert screen.query_one("#input-prompt-text", TextArea).text == "Tag it."
        screen.query_one("#btn-restore-prompt").press()
        await pilot.pause()
        screen.query_one("#btn-save-prompts").press()

    _edit(blog, config, edit)
    assert load_project(blog).prompts == {}
    assert prompts.text("tags.system") == prompts.PROMPTS["tags.system"].default
