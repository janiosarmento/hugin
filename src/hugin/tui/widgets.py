"""Textual widgets with Hugin's own key bindings."""

from textual.binding import Binding
from textual.widgets import TextArea as _TextArea


class TextArea(_TextArea):
    """TextArea where Ctrl+A selects all the text.

    Textual binds select-all to F7 only, which macOS terminals often keep for
    a media key; Ctrl+A is what people press.
    """

    BINDINGS = [Binding("ctrl+a", "select_all", "Select all", show=False)]
