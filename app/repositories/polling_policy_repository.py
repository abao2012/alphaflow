import json
from pathlib import Path
from typing import Any


DEFAULT_POLLING_POLICY: dict[str, Any] = {
    "default_interval_minutes": 15,
    "intensive_interval_minutes": 5,
    "intensive_windows": [
        {"name": "open_discovery", "start": "09:30", "end": "10:30"},
        {"name": "afternoon_rotation", "start": "13:00", "end": "14:00"},
    ],
    "quiet_windows": [
        {"name": "lunch_break", "start": "11:30", "end": "13:00"},
        {"name": "after_hours", "start": "15:05", "end": "09:20"},
    ],
    "push_gates": {
        "immediate": [
            "emerging suggestion is probe",
            "market phase changes",
            "mainline enters top 5",
            "mainline score changes by at least 2.0",
            "new critical risk alert appears",
        ],
        "summary_only": [
            "emerging suggestion is watch",
            "small score drift below 2.0",
            "repeated avoid_chase warning for the same mainline on the same day",
        ],
        "dedupe": [
            "send avoid_chase at most once per mainline per day",
            "send the same ticker signal at most once per trading session",
        ],
    },
}


class PollingPolicyRepository:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(DEFAULT_POLLING_POLICY, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return json.loads(self.path.read_text(encoding="utf-8"))
