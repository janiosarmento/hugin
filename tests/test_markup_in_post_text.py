"""Post text shown in the review panel is data, not Textual markup.

A draft's image prompts ([img]…[/img]) next to a link anchor used to crash the
outgoing-links list with a MarkupError.
"""

import asyncio
from unittest.mock import MagicMock

import frontmatter
from textual.app import App
from textual.widgets import Checkbox, Static

from hugin.config import EmbeddingsConfig, FrontmatterConfig, HuginConfig, LinksConfig
from hugin.engines import Engine
from hugin.scanner import Post
from hugin.tui.review import HuginScreen

BODY = (
    "[img]Illustration: a café garden at dawn. Aspect ratio: 16:9[/img]\n\n"
    "Water the orchids [weekly] and keep them in bright light, away from [/b] drafts.\n\n"
    "[img]Illustration: a résumé of watering tools[/img] ends here \\"
)
META = {"title": "Orchids", "draft": True, "description": "Care [/img] guide", "tags": ["[/i]plants"]}


def _host(tmp_path):
    path = tmp_path / "orchids.md"
    path.write_text(frontmatter.dumps(frontmatter.Post(BODY, **META)) + "\n")
    post = Post(path, META, BODY, True, tags=list(META["tags"]), mtime=path.stat().st_mtime)
    site = MagicMock()
    site.post_url.return_value = "/orchids/"
    site.warnings = []
    index = MagicMock()
    index.has_no_outgoing.return_value = False
    index.get_post_url.return_value = "/orchids/"
    index.get_link_keywords.return_value = ""
    index.link_keywords_for_path.return_value = ""
    index._cache = {"posts": {"x": {"url": "/light/", "title": "Bright [/img] light"}}}

    class Host(App):
        def on_mount(self):
            self.push_screen(HuginScreen(
                posts=[post], all_posts=[post],
                engine=Engine("t", "http://localhost/v1", "m", 30, None),
                pool={}, state={}, directory=tmp_path,
                config=HuginConfig(LinksConfig(), EmbeddingsConfig(), FrontmatterConfig()),
                site=site, index=index,
            ))

    return Host(), post


def _shown(screen) -> str:
    container = screen.query_one("#suggested-tags-container")
    parts = [str(w.render()) for w in container.query(Static)]
    parts += [cb.label.plain for cb in container.query(Checkbox)]
    return "\n".join(parts)


def test_review_panel_shows_bracketed_post_text_as_is(tmp_path):
    seen = {}

    async def go():
        app, post = _host(tmp_path)
        async with app.run_test(size=(186, 90)) as pilot:
            await pilot.pause()
            screen = app.screen

            screen._show_outgoing([{"anchor_text": "bright light", "target_url": "/light/"}])
            await pilot.pause()
            seen["outgoing"] = _shown(screen)

            screen._show_existing_links([{"anchor_text": "[weekly]", "url": "/w/"}], post)
            await pilot.pause()
            seen["links"] = _shown(screen)

            screen._display_tags(["[/img]soil"])
            await pilot.pause()
            seen["tags"] = _shown(screen)

            screen._display_summary("Short [/img] summary")
            await pilot.pause()
            seen["summary"] = _shown(screen)

    asyncio.run(go())
    assert "[/img]" in seen["outgoing"] and "[/b] drafts" in seen["outgoing"]
    assert "Bright [/img] light" in seen["outgoing"]
    assert "[weekly]" in seen["links"]
    assert "[/img]soil" in seen["tags"] and "[/i]plants" in seen["tags"]
    assert "Care [/img] guide" in seen["summary"]
