from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path


class EventLog:
    def __init__(self, log_dir: str | Path, clock=time.time) -> None:
        d = Path(log_dir)
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.fromtimestamp(clock()).strftime("%Y%m%d-%H%M%S")
        self.path = d / f"session-{stamp}.jsonl"
        self._f = self.path.open("a", encoding="utf-8")

    def write(self, msg: dict) -> None:
        if self._f.closed:
            return
        self._f.write(json.dumps(msg, separators=(",", ":")) + "\n")
        self._f.flush()

    def close(self) -> None:
        if not self._f.closed:
            self._f.close()
