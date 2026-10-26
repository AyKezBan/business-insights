# Bank review and retrieval acceptance

The 2026-10-26 review covers all 64 physical pages in the nine-PDF input manifest.
`bank-page-review.json` records page dispositions and separate fragment witnesses.
A reviewed page never approves all its fragments. The ledger contains no source
text, PDF bytes, or rendered images; those remain in the private local workspace.

There are 72 verified fragments across seven dated documents. At 2026-10-26,
61 remain eligible under the 180-day window, explicit expiry and supersession
rules, covering nine claim-level topics. Historical facts retain their reporting
period; eligibility does not turn their figures into present-day observations.
The 2021 steel report stays historical, and the gold digest stays undated.
One additional forecast is explicitly rejected because its publication horizon
cannot be established. Its economic reporting month is not a release deadline.

All original eleven decisions were individually rechecked. The original ten gold
queries are byte-for-byte unchanged. Three independent reviewers froze four new
positives each before retrieval observation. `bank-gold-freeze.json` pins those
datasets and six near-negatives. The release evaluator requires 10/10 old hits,
at least 10/12 extension hits in top three, and six refusals without false positives.

## Rebuild in a private directory

From `ml-service`, with the project environment active:

```sh
PYTHONPATH=src python eval/rebuild_bank_review.py \
  --source /absolute/path/to/prior/private/corpus \
  --destination /absolute/path/to/new/private/corpus
PYTHONPATH=src python eval/run_bank_retrieval.py \
  --store /absolute/path/to/new/private/corpus
PYTHONPATH=src pytest -q
```

The destination must not exist. Rebuilding reuses the immutable OCR from snapshot
`169bef4e77e6219fa145de73cb3fc4b79144babca7f009ac1dfa3d38dd39316c`.
Every page and verified fragment must match the recorded extraction revision,
text hash, image hash and character offsets. Reviews are frozen before applying
them. Original files, snapshots and the source pointer remain unchanged.
Any corrected page must receive a new extraction version and a new visual review;
partial OCR correction notes from this review are not approved page replacements.

The local handoff contains image paths, image-byte integrity checks and 72
`EvidenceSpan` records. Bank spans use fragment-relative Unicode offsets
`[0, len(fragment.text))`; PDF page offsets are separate fields. Consumers must
still enforce the request's dates, expiry, supersession and source role.

## Retrieval limits

BM25 remains the ranking algorithm. Conservative text constraints distinguish
tax/refinery rates, misconduct, gold and ratings from incidental common words;
OFZ issue identifiers and Russian day ranges must agree with the evidence.
Weekly forecasts cannot fall back to annual projections. Normalization of
half-year notation and private investors is symmetric between indexed text and
queries. These rules are regression coverage, not a general proof of semantic
faithfulness. Runtime evidence-span and consumer checks remain separate gates.
