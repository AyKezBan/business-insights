from typing import Protocol

from insight_agent.materials.models import RawNews


class NewsSource(Protocol):
    def fetch(self, limit: int) -> list[RawNews]: ...
