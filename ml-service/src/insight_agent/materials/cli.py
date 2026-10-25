"""Corpus preparation is explicit and transactional; serving pins a revision at startup."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import sys

import httpx

from insight_agent.materials.bank_corpus import prepare_bank
from insight_agent.materials.dedup import link_source, merge_news
from insight_agent.materials.models import CorpusSnapshot
from insight_agent.materials.news import CbrRssSource, read_saved_news
from insight_agent.materials.ocr import TesseractOCR
from insight_agent.materials.store import load_snapshot, publish_snapshot


@contextmanager
def corpus_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.writer.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _counts(snapshot):
    return {'revision': snapshot.revision, 'documents': len(snapshot.documents), 'pages': len(snapshot.pages),
            'fragments': len(snapshot.bank_fragments),
            'verified_fragments': sum(f.review_status == 'verified' for f in snapshot.bank_fragments),
            'news': len(snapshot.news), 'source_versions': len(snapshot.source_versions)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('bank', 'news', 'link', 'check'):
        sub = commands.add_parser(name)
        sub.add_argument('--store', type=Path, required=True)
        if name == 'bank':
            sub.add_argument('--pdf-dir', type=Path, required=True)
            sub.add_argument('--manifest', type=Path, required=True)
            sub.add_argument('--review-file', type=Path)
            sub.add_argument('--text-overrides', type=Path)
        elif name == 'news':
            source = sub.add_mutually_exclusive_group(required=True)
            source.add_argument('--source', choices=['cbr'])
            source.add_argument('--saved', type=Path)
            sub.add_argument('--limit', type=int, default=10)
        elif name == 'link':
            sub.add_argument('--event-id', required=True)
            sub.add_argument('--source-id', required=True)
    args = parser.parse_args(argv)
    try:
        with corpus_lock(args.store):
            current = load_snapshot(args.store) if (args.store / 'current.json').exists() else CorpusSnapshot()
            if args.command == 'check':
                if not current.revision:
                    raise ValueError('No published snapshot')
                print(json.dumps(_counts(current), ensure_ascii=False))
                return 0
            if args.command == 'bank':
                candidate = prepare_bank(args.pdf_dir, args.manifest, args.store, TesseractOCR(),
                                         args.review_file, text_overrides=args.text_overrides)
                if candidate.report.errors:
                    print(json.dumps(candidate.report.model_dump(), ensure_ascii=False), file=sys.stderr)
                    return 1
                eligible = [f for f in candidate.bank_fragments if f.review_status == 'verified' and f.doc_date
                            and f.statement_type in ('reported_fact', 'bank_assessment', 'forecast')
                            and (f.statement_type != 'forecast' or f.valid_until)]
                if not eligible:
                    print('Review required: see staging/bank-candidates.jsonl; current snapshot retained', file=sys.stderr)
                    return 2
                candidate.news, candidate.source_versions = current.news, current.source_versions
            else:
                if args.command == 'news':
                    if args.saved:
                        incoming = read_saved_news(args.saved)
                    else:
                        with httpx.Client(headers={'User-Agent': 'BusinessInsightsMaterials/1.0'}) as client:
                            incoming = CbrRssSource(client).fetch(args.limit)
                    if not incoming:
                        print('No usable source records; current snapshot retained', file=sys.stderr)
                        return 2
                    result = merge_news(current.news, current.source_versions, incoming, observed_at=datetime.now(timezone.utc))
                else:
                    result = link_source(current.news, current.source_versions, args.event_id, args.source_id)
                candidate = current.model_copy(deep=True)
                candidate.news, candidate.source_versions = result.events, result.versions
            candidate.report.documents = len(candidate.documents)
            candidate.report.pages = len(candidate.pages)
            candidate.report.fragments = len(candidate.bank_fragments)
            candidate.report.news = len(candidate.news)
            revision = publish_snapshot(args.store, candidate)
            candidate.revision = revision
            print(json.dumps(_counts(candidate), ensure_ascii=False))
            return 0
    except (OSError, ValueError, RuntimeError, httpx.HTTPError) as exc:
        # Do not print URLs/response bodies/credentials supplied by external services.
        print(f'Material import failed: {type(exc).__name__}: {str(exc)[:300]}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
