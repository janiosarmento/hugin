"""Confirm where a blog keeps its images, the first time Hugin moves pasted ones."""

from datetime import datetime
from pathlib import Path

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from hugin import images
from hugin.tui.form import titled


class ImageConventionScreen(ModalScreen["images.Convention | None"]):
    """The detected image folder, editable; dismisses with the confirmed one or None."""

    BINDINGS = [("escape", "cancel", "Cancel"), ("ctrl+s", "save", "Save")]

    DEFAULT_CSS = """
    ImageConventionScreen {
        align: center middle;
    }

    #images-modal {
        width: 90%;
        max-width: 100;
        height: auto;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #images-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #images-evidence, #images-example {
        color: $text-muted;
        margin-bottom: 1;
    }

    #images-error {
        color: $error;
    }

    #images-buttons {
        height: auto;
        margin-top: 1;
    }

    #images-buttons Button {
        margin-right: 1;
    }
    """

    def __init__(self, detection: "images.Detection | None", site_root: Path, pending: int) -> None:
        super().__init__()
        self._detection = detection
        self._site_root = site_root
        self._pending = pending

    def compose(self) -> ComposeResult:
        found = self._detection.convention if self._detection else images.Convention("static/images", "/images")
        with Vertical(id="images-modal"):
            yield Label("Where does this blog keep its images?", id="images-title")
            yield Static(
                (escape(self._detection.evidence) + ". " if self._detection
                 else "No recent post links an image on this site, so this is a guess. ")
                + f"{self._pending} post(s) link images in {escape(str(images.INBOX))}. "
                "Hugin moves them here and rewrites the links; this is saved in "
                "hugin/config.toml and not asked again.",
                id="images-evidence",
            )
            yield titled("Folder, in the site", Input(found.dir, id="images-dir"))
            yield titled("Link to that folder", Input(found.url, id="images-url"))
            yield titled("Date folders (from the post's date)", Select(
                [("No date folders", ""), *((p, p) for p in images.DATE_PATTERNS if p)],
                value=found.date_folders, allow_blank=False, id="images-dates",
            ))
            yield Static("", id="images-example")
            yield Static("", id="images-error")
            with Horizontal(id="images-buttons"):
                yield Button("Save and move (Ctrl+S)", id="btn-images-save", variant="primary")
                yield Button("Not now", id="btn-images-cancel")

    def on_mount(self) -> None:
        self._update_example()
        self.query_one("#btn-images-save", Button).focus()

    def _convention(self) -> images.Convention:
        return images.Convention(
            dir=self.query_one("#images-dir", Input).value.strip().strip("/"),
            url="/" + self.query_one("#images-url", Input).value.strip().strip("/"),
            date_folders=self.query_one("#images-dates", Select).value or "",
        )

    def _problem(self, convention: images.Convention) -> str | None:
        if not convention.dir:
            return "Give the folder."
        folder = (self._site_root / convention.dir).resolve()
        if not folder.is_relative_to(self._site_root.resolve()):
            return "The folder must be inside the site."
        if convention.url == "/":
            return "Give the link prefix, like /images."
        return None

    def _update_example(self) -> None:
        convention = self._convention()
        sub = datetime.now().strftime(images.DATE_PATTERNS.get(convention.date_folders, ""))
        folder, url = convention.folder(sub)
        self.query_one("#images-example", Static).update(
            f"A new post's photo.jpg: {escape(folder)}/photo.jpg, linked as {escape(url)}/photo.jpg. "
            "A post that already has images gets new ones in the same folder."
        )

    def on_input_changed(self, _event: Input.Changed) -> None:
        self._update_example()

    def on_select_changed(self, _event: Select.Changed) -> None:
        self._update_example()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-images-save":
            self.action_save()
        else:
            self.action_cancel()

    def action_save(self) -> None:
        convention = self._convention()
        problem = self._problem(convention)
        if problem:
            self.query_one("#images-error", Static).update(problem)
            return
        self.dismiss(convention)

    def action_cancel(self) -> None:
        self.dismiss(None)
