"""Packaging progress events shared by CLI-independent frontends."""
from dataclasses import dataclass
from collections.abc import Callable


@dataclass(frozen=True)
class Progress:
    stage: str
    message: str
    completed: int = 0
    total: int | None = None
    current_file: str = ''
    bytes_copied: int | None = None
    bytes_total: int | None = None


ProgressCallback = Callable[[Progress], None]
