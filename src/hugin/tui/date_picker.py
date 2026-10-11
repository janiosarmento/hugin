"""Date and time picker for frontmatter dates: a month grid, and the time kept as is."""

import calendar
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
        width: 34;
        height: auto;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #picker-month {
        text-style: bold;
        content-align: center middle;
        width: 100%;
    }

    #picker-grid {
        height: auto;
        margin: 1 0;
    }

    #picker-time {
        height: auto;
    }

    #picker-time Input {
        width: 6;
    }

    #picker-buttons {
        height: auto;
        margin-top: 1;
    }

    #picker-buttons Button {
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
        self._hour = start.hour
        self._minute = start.minute

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-modal"):
            yield Static("", id="picker-month")
            yield Static("", id="picker-grid")
            with Horizontal(id="picker-time"):
                yield Label("Time ")
                yield Input(f"{self._hour:02d}", id="picker-hour", type="integer", max_length=2)
                yield Label(":")
                yield Input(f"{self._minute:02d}", id="picker-minute", type="integer", max_length=2)
            yield Static("Arrows move the day, PgUp/PgDn the month, Enter uses it, Esc cancels.", id="picker-hint")
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
        try:
            hour = int(self.query_one("#picker-hour", Input).value)
            minute = int(self.query_one("#picker-minute", Input).value)
            chosen = datetime.combine(self._day, time(hour, minute))
        except ValueError:
            self.notify("Hour must be 0-23 and minute 0-59.", severity="warning")
            return
        self.dismiss(chosen)

    def action_cancel(self) -> None:
        self.dismiss(None)
