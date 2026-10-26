#!/usr/bin/env python3
"""Generate labelled synthetic smoke inputs in a dedicated, disposable directory."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

KIND = "business-insights-synthetic-smoke"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    output = args.output_dir.resolve()
    marker = output / "synthetic-smoke.json"
    if output.exists() and any(output.iterdir()):
        if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")).get("kind") != KIND:
            parser.error("Refusing to overwrite a nonempty directory that is not a generated synthetic smoke dataset")
    output.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"kind": KIND, "version": 1, "person_id": "F_1", "company_id": "U_1",
                                  "warning": "SYNTHETIC TEST DATA ONLY. Never import into a shared or live database."}, indent=2) + "\n", encoding="utf-8")

    # Reuse the importer tests' CSV formats and edge cases; never read source-data/.
    import_root = args.repo_root / "data-import"
    sys.path.insert(0, str(import_root))
    spec = importlib.util.spec_from_file_location("smoke_import_fixtures", import_root / "tests" / "fixtures.py")
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    fixtures.write_fixture(output / "csv")

    source = args.repo_root / "ml-service" / "tests" / "fixtures" / "request.json"
    event = json.loads(source.read_text(encoding="utf-8"))["event"]
    backend_event = {"eventId": event["event_id"], "title": "[SYNTHETIC SMOKE] " + event["title"],
                     "text": event["text"], "publishedAt": event["published_at"],
                     "source": {"sourceId": event["source"]["source_id"], "title": "[SYNTHETIC SMOKE] " + event["source"]["title"],
                                "date": event["source"]["date"], "url": event["source"]["url"]},
                     "relatedSources": [], "topics": event["topics"]}
    (output / "backend-event.json").write_text(json.dumps(backend_event, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"kind": KIND, "csv_dir": str(output / "csv"), "event": str(output / "backend-event.json")}, indent=2))


if __name__ == "__main__":
    main()
