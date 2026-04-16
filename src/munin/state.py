"""Per-session in-memory state for Munin."""


class SessionState:
    """Tracks incoming/outgoing results per post during a session."""

    def __init__(self) -> None:
        self._incoming: dict[str, list[dict]] = {}
        self._outgoing: dict[str, list[dict]] = {}

    def set_incoming(self, post_path: str, results: list[dict]) -> None:
        self._incoming[post_path] = results

    def get_incoming(self, post_path: str) -> list[dict] | None:
        return self._incoming.get(post_path)

    def set_outgoing(self, post_path: str, suggestions: list[dict]) -> None:
        self._outgoing[post_path] = suggestions

    def get_outgoing(self, post_path: str) -> list[dict] | None:
        return self._outgoing.get(post_path)

    def clear_outgoing(self, post_path: str) -> None:
        self._outgoing.pop(post_path, None)
