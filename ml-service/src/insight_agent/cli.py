import argparse
import sys
from pathlib import Path

from insight_agent.api import build_agent
from insight_agent.config import get_settings
from insight_agent.schemas import InsightRequest


def main() -> None:
    parser = argparse.ArgumentParser(description="Персонализированный инсайт по событию")
    parser.add_argument("request", type=Path, help="JSON по схеме contracts/ml/insight-request.schema.json")
    args = parser.parse_args()
    request = InsightRequest.model_validate_json(args.request.read_text("utf-8"))
    insight = build_agent(get_settings()).run(request)
    sys.stdout.write(insight.model_dump_json(indent=2) + "\n")
    print(f"run_id={insight.run_id} status={insight.status.value}", file=sys.stderr)


if __name__ == "__main__":
    main()
