import json
from datetime import datetime
from pathlib import Path
from typing import Any


class JsonlLogRepository:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def append(self, name: str, payload: dict[str, Any]) -> None:
        target = self.base_dir / f"{name}.jsonl"
        line = json.dumps(
            {"ts": datetime.now().isoformat(), "payload": payload},
            ensure_ascii=False,
        )
        with target.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
