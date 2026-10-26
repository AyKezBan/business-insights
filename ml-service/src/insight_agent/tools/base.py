from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from insight_agent.trace import Tracer


class ToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    fn: Callable[[BaseModel], Any]
    summarize: Callable[[Any], dict]


class ToolRegistry:
    def __init__(self, tools: list[Tool], tracer: Tracer, max_calls: int):
        self._tools = {t.name: t for t in tools}
        self._tracer = tracer
        self._max_calls = max_calls
        self.calls = 0

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def describe(self) -> str:
        return "\n".join(
            f"- {t.name}: {t.description} Аргументы: {list(t.args.model_fields)}" for t in self._tools.values()
        )

    def call(self, name: str, args: dict, *, step: str) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            self._tracer.emit("tool_call", step, {"tool": name, "rejected": "unknown_tool"})
            raise ToolError(f"Инструмент {name!r} не разрешён")
        if self.calls >= self._max_calls:
            self._tracer.emit("tool_call", step, {"tool": name, "rejected": "call_limit"})
            raise ToolError("Превышен лимит вызовов инструментов")
        try:
            parsed = tool.args.model_validate(args)
        except ValidationError as exc:
            self._tracer.emit("tool_call", step, {"tool": name, "rejected": "bad_args", "error": str(exc)[:500]})
            raise ToolError(f"Неверные аргументы {name}: {exc}") from exc
        self.calls += 1
        result = tool.fn(parsed)
        self._tracer.emit("tool_call", step, {"tool": name, "args": parsed.model_dump(), "result": tool.summarize(result)})
        return result
