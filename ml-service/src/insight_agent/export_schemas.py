import json
import sys
from pathlib import Path

from insight_agent.schemas import Answer, InsightRequest, Insight, QuestionRequest, TraceEvent
from insight_agent.schemas import BankFragment
from insight_agent.materials.models import CorpusSnapshot

CONTRACTS = {
    "bank-fragment": BankFragment,
    "materials": CorpusSnapshot,
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
