"""Ways a browser flow can stop. None of them is retried blindly."""
from __future__ import annotations


class NeedsHuman(Exception):
    """The agent cannot finish safely (layout not understood, step cap reached, unexpected page): hand over."""


class PortalRejected(Exception):
    """The portal's own validation refused the input; the messages are passed on verbatim."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class NavigationTimeout(Exception):
    """A click that should load a new page did not finish in time. The click may still have taken effect."""
