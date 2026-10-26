"""Run manually defined gold IDs against a local private corpus; no source text output."""
import argparse
from datetime import date
import json
from pathlib import Path

from insight_agent.materials.store import load_snapshot
from insight_agent.tools.bank import BankIndex,BankSearchArgs


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--store',type=Path,required=True)
    parser.add_argument('--gold',type=Path,default=Path(__file__).with_name('bank-retrieval-gold.jsonl'))
    args=parser.parse_args()
    snapshot=load_snapshot(args.store)
    index=BankIndex(snapshot.bank_fragments)
    results=[]
    for line in args.gold.read_text().splitlines():
        case=json.loads(line)
        found=index.search(BankSearchArgs(query=case['query'],top_k=3),as_of=date.fromisoformat(case['as_of']))
        ids=[r.fragment.fragment_id for r in found]
        results.append({'query':case['query'],'top3':ids,'hit':bool(set(ids)&set(case['expected_fragment_ids']))})
    no_coverage={q:not index.search(BankSearchArgs(query=q),as_of=date(2026,10,24))
        for q in ['квантовая криптография','орбитальные спутники','верификация биометрии']}
    result={'revision':snapshot.revision,'hits':sum(r['hit'] for r in results),'total':len(results),
        'no_coverage':no_coverage,'results':results}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['hits']>=8 and all(no_coverage.values()) else 1


if __name__=='__main__':
    raise SystemExit(main())
