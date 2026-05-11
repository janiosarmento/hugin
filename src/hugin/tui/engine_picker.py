"""Engine and model selection modal."""

import httpx

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Label, Static

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
        self._viewing = "engines"  # "engines" or "models"
        self._selected_engine: Engine | None = None
        self._available_models: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="engine-modal"):
            yield Label("Select engine", id="modal-title")
            yield DataTable(id="picker-table", cursor_type="row", zebra_stripes=True)
            yield Static("Enter to select, Escape to cancel", id="picker-hint")
        yield Footer()

    def on_mount(self) -> None:
        self._show_engines()

    def _show_engines(self) -> None:
        self._viewing = "engines"
        self.query_one("#modal-title", Label).update("Select engine")
        self.query_one("#picker-hint", Static).update("Enter to select, Escape to cancel")

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

        table = self.query_one("#picker-table", DataTable)
        table.clear(columns=True)
        table.add_column("", key="active", width=2)
        table.add_column("Model", key="model")

        for i, model_id in enumerate(models):
            active = " ●" if model_id == engine.model else ""
            table.add_row(active, model_id, key=f"model-{i}")

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

    @work(exclusive=True)
    async def _fetch_models(self, engine: Engine) -> None:
        self.query_one("#picker-hint", Static).update("Loading models...")

        try:
            headers = {}
            if engine.api_key:
                headers["Authorization"] = f"Bearer {engine.api_key}"

            async with httpx.AsyncClient(timeout=engine.timeout) as client:
                response = await client.get(
                    f"{engine.url}/models",
                    headers=headers,
                )
                if response.status_code in (401, 403, 404, 405):
                    # Endpoint blocked or not supported — use configured model
                    self.dismiss(engine)
                    return
                response.raise_for_status()

            data = response.json()
            models = [
                m["id"] for m in data.get("data", [])
                if "embed" not in m["id"].lower()
            ]
            models.sort()

            if not models:
                # API doesn't list models, use the configured one
                self.dismiss(engine)
                return

            self._show_models(models)

        except Exception as e:
            # Can't list models — just use the engine with its default model
            self.notify(f"Can't list models: {e}", severity="warning")
            self.dismiss(engine)

    def action_cancel(self) -> None:
        if self._viewing == "models":
            self._show_engines()
        else:
            self.dismiss(None)
