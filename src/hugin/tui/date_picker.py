"""Date and time picker for frontmatter dates: a month grid, and the time kept as is."""

import calendar
import re
from datetime import date, datetime, time, timedelta

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

WEEKDAYS = "Su Mo Tu We Th Fr Sa"


def parse_frontmatter_date(text: str) -> datetime | None:
    """A date or datetime as the editor shows it; None when it is not one."""
    try:
        return datetime.fromisoformat(text.strip())
    except ValueError:
        try:
            return datetime.combine(date.fromisoformat(text.strip()), time())
        except ValueError:
            return None


def format_frontmatter_date(value: datetime) -> str:
    """ISO form Hugo reads, with seconds: 2024-01-02T11:00:00."""
    return value.isoformat(timespec="seconds")


def add_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    year, month = divmod(index, 12)
    day = min(value.day, calendar.monthrange(year, month + 1)[1])
    return date(year, month + 1, day)


class DatePickerScreen(ModalScreen[datetime | None]):
    """Pick a day in a month grid, and set the hour and minute.

    The time of the value passed in is kept unless it is changed here, so
    picking a new day does not move a scheduled post's publication time.
    Dismisses with the chosen datetime, or None when cancelled.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("left", "move(-1)", "Day -"),
        Binding("right", "move(1)", "Day +"),
        Binding("up", "move(-7)", "Week -"),
        Binding("down", "move(7)", "Week +"),
        Binding("pageup", "month(-1)", "Month -"),
        Binding("pagedown", "month(1)", "Month +"),
        Binding("enter", "confirm", "Use"),
    ]

    DEFAULT_CSS = """
    DatePickerScreen {
        align: center middle;
    }

    #picker-modal {
        width: 36;
        height: auto;
        border: round $accent;
        background: $surface;
        padding: 0 1;
    }

    #picker-month {
        text-style: bold;
        width: 100%;
        content-align: center middle;
    }

    #picker-grid {
        height: auto;
        margin-bottom: 1;
    }

    #picker-time {
        height: 1;
    }

    #picker-time Label {
        width: auto;
        padding-right: 1;
    }

    #picker-time Input {
        width: 8;
        height: 1;
        border: none;
        padding: 0;
    }

    #picker-buttons {
        height: 1;
        margin-top: 1;
    }

    #picker-buttons Button {
        height: 1;
        min-width: 8;
        border: none;
        margin-right: 1;
    }

    #picker-hint {
        color: $text-muted;
    }
    """

    def __init__(self, value: datetime | None = None) -> None:
        super().__init__()
        start = value or datetime.now()
        self._day = start.date()
        self._time = time(start.hour, start.minute)

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-modal"):
            yield Static("", id="picker-month")
            yield Static("", id="picker-grid")
            with Horizontal(id="picker-time"):
                yield Label("Time")
                yield Input(self._time.strftime("%H:%M"), id="picker-time-input")
            yield Static("←→↑↓ day  PgUp/PgDn month", id="picker-hint")
            with Horizontal(id="picker-buttons"):
                yield Button("Use", id="btn-use", variant="primary")
                yield Button("Cancel", id="btn-cancel-pick")

    def on_mount(self) -> None:
        self._render_month()
        # Textual focuses the first input once the screen is shown; the grid should get the keys
        self.call_after_refresh(self.set_focus, None)

    def _render_month(self) -> None:
        self.query_one("#picker-month", Static).update(self._day.strftime("%B %Y"))
        rows = [WEEKDAYS]
        for week in calendar.Calendar(firstweekday=6).monthdayscalendar(self._day.year, self._day.month):
            cells = []
            for number in week:
                if number == 0:
                    cells.append("  ")
                elif number == self._day.day:
                    cells.append(f"[reverse]{number:2d}[/reverse]")
                else:
                    cells.append(f"{number:2d}")
            rows.append(" ".join(cells))
        self.query_one("#picker-grid", Static).update("\n".join(rows))

    def action_move(self, days: int) -> None:
        self._day += timedelta(days=days)
        self._render_month()

    def action_month(self, months: int) -> None:
        self._day = add_months(self._day, months)
        self._render_month()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_confirm()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-use":
            self.action_confirm()
        else:
            self.action_cancel()

    def action_confirm(self) -> None:
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", self.query_one("#picker-time-input", Input).value)
        try:
            chosen_time = time(int(match.group(1)), int(match.group(2))) if match else None
        except ValueError:
            chosen_time = None
        if chosen_time is None:
            self.notify("Time must be HH:MM, from 00:00 to 23:59.", severity="warning")
            return
        self.dismiss(datetime.combine(self._day, chosen_time))

    def action_cancel(self) -> None:
        self.dismiss(None)
