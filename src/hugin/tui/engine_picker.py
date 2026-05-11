"""Engine and model selection modal."""

import httpx

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Label, Static

from hugin.engines import Engine


class EnginePickerScreen(ModalScreen[Engine | None]):
    """Modal to pick an AI engine and model."""

    BINDINGS = [
        ("escape", "cancel", "Back / Cancel"),
    ]

    DEFAULT_CSS = """
    EnginePickerScreen {
        align: center middle;
    }

    #engine-modal {
        width: 90;
        height: auto;
        max-height: 80%;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }

    #modal-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #picker-table {
        height: auto;
        max-height: 20;
    }

    #model-input {
        margin-top: 1;
        margin-bottom: 1;
    }

    #picker-hint {
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, engines: list[Engine], current_id: str, current_model: str = "") -> None:
        super().__init__()
        self.engines = engines
        self.current_id = current_id
        self.current_model = current_model
        self._viewing = "engines"  # "engines", "models", or "manual"
        self._selected_engine: Engine | None = None
        self._available_models: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="engine-modal"):
            yield Label("Select engine", id="modal-title")
            yield DataTable(id="picker-table", cursor_type="row", zebra_stripes=True)
            yield Input(placeholder="model name", id="model-input")
            yield Static("Enter to select, Escape to cancel", id="picker-hint")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#model-input", Input).display = False
        self._show_engines()

    def _show_engines(self) -> None:
        self._viewing = "engines"
        self.query_one("#modal-title", Label).update("Select engine")
        self.query_one("#picker-hint", Static).update("Enter to select, Escape to cancel")
        self.query_one("#picker-table", DataTable).display = True
        self.query_one("#model-input", Input).display = False

        table = self.query_one("#picker-table", DataTable)
        table.clear(columns=True)
        table.add_column("", key="active", width=2)
        table.add_column("Engine", key="engine")
        table.add_column("Model", key="model")
        table.add_column("Status", key="status")

        for i, engine in enumerate(self.engines):
            active = " ●" if engine.id == self.current_id else ""
            model = self.current_model if engine.id == self.current_id and self.current_model else engine.model
            status = "ready" if engine.available else "no key"
            table.add_row(active, engine.id, model, status, key=f"eng-{i}")

        table.focus()

    def _show_models(self, models: list[str]) -> None:
        self._viewing = "models"
        self._available_models = models
        engine = self._selected_engine

        self.query_one("#modal-title", Label).update(
            f"Select model for {engine.id}"
        )
        self.query_one("#picker-hint", Static).update(
            "Enter to select, Escape to go back"
        )
        self.query_one("#picker-table", DataTable).display = True
        self.query_one("#model-input", Input).display = False

        table = self.query_one("#picker-table", DataTable)
        table.clear(columns=True)
        table.add_column("", key="active", width=2)
        table.add_column("Model", key="model")

        for i, model_id in enumerate(models):
            active = " ●" if model_id == engine.model else ""
            table.add_row(active, model_id, key=f"model-{i}")

        table.focus()

    def _show_manual_input(self, reason: str) -> None:
        """Show a text input so the user can type a model name manually."""
        self._viewing = "manual"
        engine = self._selected_engine

        self.query_one("#modal-title", Label).update(
            f"Model for {engine.id}"
        )
        self.query_one("#picker-hint", Static).update(
            f"{reason}  Enter to confirm, Escape to go back"
        )
        self.query_one("#picker-table", DataTable).display = False

        inp = self.query_one("#model-input", Input)
        inp.value = engine.model or ""
        inp.display = True
        inp.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        index = event.cursor_row
        if index is None:
            return

        if self._viewing == "engines":
            if 0 <= index < len(self.engines):
                engine = self.engines[index]
                if not engine.available:
                    self.notify(
                        f"No API key. Set {engine.id.upper()}_API_KEY",
                        severity="warning",
                    )
                    return
                self._selected_engine = engine
                self._fetch_models(engine)

        elif self._viewing == "models":
            if 0 <= index < len(self._available_models):
                self._selected_engine.model = self._available_models[index]
                self.dismiss(self._selected_engine)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._viewing != "manual":
            return
        model = event.value.strip()
        if not model:
            self.notify("Enter a model name.", severity="warning")
            return
        self._selected_engine.model = model
        self.dismiss(self._selected_engine)

    @work(exclusive=True)
    async def _fetch_models(self, engine: Engine) -> None:
        self.query_one("#picker-hint", Static).update("Loading models...")

        try:
            headers = {"User-Agent": "hugin/0.1"}
            if engine.api_key:
                headers["Authorization"] = f"Bearer {engine.api_key}"

            async with httpx.AsyncClient(timeout=engine.timeout) as client:
                url = f"{engine.url}/models"
                response = await client.get(url, headers=headers)
                if response.status_code in (403, 405):
                    # Some servers block GET on /models — fall back to POST
                    response = await client.post(url, headers=headers)
                if response.status_code == 401:
                    self._show_manual_input("Auth error — type model name:")
                    return
                if response.status_code in (403, 404):
                    self._show_manual_input("Can't list models — type model name:")
                    return
                response.raise_for_status()

            data = response.json()
            models = [
                m["id"] for m in data.get("data", [])
                if "embed" not in m["id"].lower()
            ]
            models.sort()

            if not models:
                self._show_manual_input("No models returned — type model name:")
                return

            self._show_models(models)

        except Exception as e:
            self._show_manual_input(f"Error: {e} — type model name:")

    def action_cancel(self) -> None:
        if self._viewing in ("models", "manual"):
            self._show_engines()
        else:
            self.dismiss(None)
