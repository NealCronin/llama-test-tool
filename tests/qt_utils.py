"""Shared Qt event-loop helpers for GUI tests."""
from __future__ import annotations

import time

from PySide6.QtWidgets import QApplication


def _spin_until(predicate, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise TimeoutError("event loop did not reach the expected state")
        QApplication.processEvents()
        time.sleep(0.01)
