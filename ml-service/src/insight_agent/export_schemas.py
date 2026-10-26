import json
import sys
from pathlib import Path

from insight_agent.schemas import Answer, InsightRequest, Insight, QuestionRequest, TraceEvent
from insight_agent.schemas import BankFragment, NewsPage
from insight_agent.materials.models import CatalogToken, CorpusSnapshot
from insight_agent.materials.sources import FetchBatch, SourceSpec
from insight_agent.materials.api import CatalogResponse, MaterialEvent

CONTRACTS = {
    "news-page": NewsPage,
    "bank-fragment": BankFragment,
    "materials": CorpusSnapshot,
    "catalog-token": CatalogToken,
    "material-event": MaterialEvent,
    "catalog-response": CatalogResponse,
    "source-spec": SourceSpec,
    "fetch-batch": FetchBatch,
    "insight-request": InsightRequest,
    "insight": Insight,
    "question-request": QuestionRequest,
    "answer": Answer,
    "trace-event": TraceEvent,
}


def main(out_dir: str) -> None:
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    for name, model in CONTRACTS.items():
        schema = model.model_json_schema()
        (target / f"{name}.schema.json").write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"{name}.schema.json")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "../contracts/ml")
