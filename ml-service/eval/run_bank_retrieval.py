"""Evaluate frozen public IDs against a private corpus without printing source text."""
import argparse
from datetime import date
import json
from pathlib import Path

from insight_agent.materials.store import load_snapshot
from insight_agent.tools.bank import BankIndex, BankSearchArgs


def evaluate_cases(index, cases):
    results = []
    for case in cases:
        values = dict(case.get("search_args", {}))
        values.update(query=case["query"], top_k=3)
        found = index.search(BankSearchArgs(**values), as_of=date.fromisoformat(case["as_of"]))
        ids = [result.fragment.fragment_id for result in found]
        expected = case["expected_fragment_ids"]
        passed = bool(set(ids) & set(expected)) if expected else not ids
        results.append({"query": case["query"], "as_of": case["as_of"], "top3": ids,
                        "expected_fragment_ids": expected, "passed": passed,
                        "kind": "positive" if expected else "negative"})
    return results


def passes_release(old_hits, extended_hits, extended_total, refusals, negative_total):
    return old_hits == 10 and extended_total == 12 and extended_hits >= 10 and negative_total == refusals == 6


def read_cases(path):
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def evaluate_suites(index, old, extended, negatives):
    for name, cases, count, positive in (("old", old, 10, True), ("extended", extended, 12, True),
                                         ("negatives", negatives, 6, False)):
        if len(cases) != count:
            raise ValueError(f"{name} suite requires {count} cases")
        if any(bool(case["expected_fragment_ids"]) != positive for case in cases):
            raise ValueError(f"{name} suite must contain {'positive' if positive else 'negative'} cases")
    return {name: evaluate_cases(index, cases) for name, cases in
            (("old", old), ("extended", extended), ("negatives", negatives))}


def main():
    folder = Path(__file__).parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--gold", type=Path, default=folder / "bank-retrieval-gold.jsonl")
    parser.add_argument("--extended", type=Path, default=folder / "bank-retrieval-extended.jsonl")
    parser.add_argument("--negatives", type=Path, default=folder / "bank-retrieval-negatives.jsonl")
    args = parser.parse_args()
    snapshot = load_snapshot(args.store)
    index = BankIndex(snapshot.bank_fragments)
    suites = evaluate_suites(index, read_cases(args.gold), read_cases(args.extended), read_cases(args.negatives))
    counts = {name: sum(row["passed"] for row in rows) for name, rows in suites.items()}
    totals = {name: len(rows) for name, rows in suites.items()}
    passed = totals["old"] == 10 and passes_release(counts["old"], counts["extended"], totals["extended"],
                                                   counts["negatives"], totals["negatives"])
    result = {"revision": snapshot.revision, "passed": passed, "counts": counts,
              "totals": totals, "results": suites}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
