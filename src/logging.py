"""Logging utility. Writes to stdout and to a log file with timestamps."""
from __future__ import annotations

import atexit
from datetime import datetime
from pathlib import Path
from typing import IO, Optional

_log_file: Optional[IO[str]] = None


def setup_log(log_path: Path) -> None:
    """Open log_path for writing (overwrite). No-op if already set up."""
    global _log_file
    if _log_file is not None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _log_file = open(log_path, "w", encoding="utf-8", buffering=1)
    atexit.register(_log_file.close)


def log(msg: str = "", end: str = "\n", flush: bool = False) -> None:
    """Print to stdout and append to the log file with a timestamp."""
    print(msg, end=end, flush=flush)
    if _log_file is not None:
        ts = datetime.now().strftime("%H:%M:%S")
        _log_file.write(f"[{ts}] {msg}{end}")
