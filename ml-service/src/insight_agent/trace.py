import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from insight_agent.schemas import TraceEvent

SECRET_KEYS = {"api_key", "authorization", "token", "password", "llm_api_key"}
MAX_STRING = 2000


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:16]}"


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: "***" if k.lower() in SECRET_KEYS else _redact(v) for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str) and len(value) > MAX_STRING:
        return value[:MAX_STRING] + f"…[+{len(value) - MAX_STRING}]"
    return value


class Tracer:
    def __init__(self, run_id: str, trace_dir: Path | None = None):
        self.run_id = run_id
        self.events: list[TraceEvent] = []
        self._path = trace_dir / f"{run_id}.jsonl" if trace_dir else None
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, type_: str, step: str, data: dict | None = None) -> TraceEvent:
        event = TraceEvent(
            run_id=self.run_id,
            seq=len(self.events) + 1,
            ts=datetime.now(UTC),
            type=type_,
            step=step,
            data=_redact(data or {}),
        )
        self.events.append(event)
        if self._path:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(event.model_dump_json() + "\n")
        return event


def load_trace(trace_dir: Path, run_id: str) -> list[TraceEvent] | None:
    if not run_id.startswith("run_") or not run_id[4:].isalnum():
        return None
    path = trace_dir / f"{run_id}.jsonl"
    if not path.exists():
        return None
    return [TraceEvent.model_validate_json(line) for line in path.read_text("utf-8").splitlines() if line]
