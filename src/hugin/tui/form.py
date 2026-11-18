"""Form helpers: field labels rendered inside a container's top border.

``titled()`` wraps a control (Input, Select, TextArea) in a ``Field``
container that owns the round border and the title. The inner control
loses its own border, so the border sits on the screen background and
only the interior is shaded — the same look ``Select`` has natively.
"""

from __future__ import annotations

from textual.containers import Vertical
from textual.widget import Widget


class Field(Vertical):
    """Bordered container showing a form label as its border title."""

    DEFAULT_CSS = """
    Field {
        layout: horizontal;
        width: 1fr;
        height: auto;
        border: round $border-blurred;
        border-title-align: left;
        border-title-color: $text-muted;
    }
    Field:focus-within {
        border: round $border;
        border-title-color: $accent;
        border-title-style: bold;
    }
    Field > Input, Field > Input:focus, Field > Input.-invalid,
    Field > TextArea, Field > TextArea:focus {
        border: none;
        width: 1fr;
    }
    Field > Input, Field > Input:focus {
        height: 1;
        padding: 0 1;
    }
    Field > Select {
        width: 1fr;
    }
    /* Read-only text keeps the one-line height of an Input. */
    Field > Static {
        width: 1fr;
        padding: 0 1;
    }
    /* Inline icon buttons (e.g. date picker) sit inside the border.
       !important: Button's own CSS uses higher-specificity variant
       selectors (Button.-style-default, .-active !important). */
    Field > Button, Field > Button:hover, Field > Button:focus,
    Field > Button.-active {
        width: auto;
        min-width: 3 !important;
        height: 1 !important;
        border: none !important;
        background: $surface !important;
        color: $text-muted !important;
        text-style: none !important;
    }
    Field > Button:hover, Field > Button:focus {
        color: $accent !important;
    }
    Field > Select > SelectCurrent,
    Field > Select:focus > SelectCurrent,
    Field > Select.-expanded > SelectCurrent {
        border: none;
        padding: 0 1;
    }
    """


def titled(title: str, *widgets: Widget, classes: str | None = None) -> Field:
    """Wrap *widgets* (laid out side by side) in a ``Field`` showing
    *title* in its top border. *classes* go on the ``Field``, for screen
    rules such as a field that grows to fill the screen."""
    field = Field(*widgets, classes=classes)
    field.border_title = title
    return field
