#!/usr/bin/env python3
"""Thread-safe console log for the web UI."""
from __future__ import annotations

import datetime as dt
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List


class ConsoleLog:
    def __init__(self, max_lines: int = 50000) -> None:
        self._lines: Deque[Dict[str, Any]] = deque(maxlen=max_lines)
        self._lock = threading.Lock()
        self._seq = 0
        self.verbose = True

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()
            self._seq = 0

    def log(self, message: str, level: str = "INFO", verbose_only: bool = False) -> None:
        if verbose_only and not self.verbose:
            return
        ts = dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        with self._lock:
            self._seq += 1
            self._lines.append(
                {"id": self._seq, "ts": ts, "level": level.upper(), "msg": str(message), "epoch": time.time()}
            )

    def info(self, msg: str, verbose_only: bool = False) -> None:
        self.log(msg, "INFO", verbose_only)

    def warn(self, msg: str, verbose_only: bool = False) -> None:
        self.log(msg, "WARN", verbose_only)

    def error(self, msg: str, verbose_only: bool = False) -> None:
        self.log(msg, "ERROR", verbose_only)

    def ok(self, msg: str, verbose_only: bool = False) -> None:
        self.log(msg, "OK", verbose_only)

    def debug(self, msg: str) -> None:
        self.log(msg, "DEBUG", verbose_only=True)

    def since(self, after_id: int = 0) -> List[Dict[str, Any]]:
        with self._lock:
            return [ln for ln in self._lines if ln["id"] > after_id]


CONSOLE = ConsoleLog()
