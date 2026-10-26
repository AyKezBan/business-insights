# Material finalization contracts

Approved design: ../../docs/superpowers/specs/2026-10-26-domains-03-04-finalization-design.md
Task plan: ../../docs/superpowers/plans/2026-10-26-domains-03-04-finalization.md

T02 implements SourceSpec/FetchBatch, compatible metadata v2, precision and CatalogToken. T07 implements the runtime catalog and pinned corpus. T08/T09 preserve them through Java storage and operation inputs. This initial baseline does not claim those tasks already implemented.

Only the coordinator edits shared models/schema/registry. Publisher adapter workers consume a committed interface SHA. Corpus full text, raw PDFs, OCR and credentials stay outside Git. Fresh as_of is publication cutoff, not a point-in-time reconstruction guarantee.
