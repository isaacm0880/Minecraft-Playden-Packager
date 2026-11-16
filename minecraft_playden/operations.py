"""Cooperative cancellation shared by builds, transfers and GUI workers."""
from threading import Event


class OperationCancelled(Exception):
    """An operation stopped before its commit point; never a successful result."""


class CancellationToken:
    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self.cancelled:
            raise OperationCancelled('Operation cancelled. No success was reported.')

    def wait(self, seconds: float) -> None:
        self._event.wait(seconds)
        self.check()
