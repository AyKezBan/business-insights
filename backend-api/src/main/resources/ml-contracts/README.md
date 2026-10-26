# Backend ML contracts

The authoritative schemas are generated from the current Python models into `contracts/ml`.
Every `.schema.json` resource here is a byte-for-byte copy of its counterpart there.
Regenerate with `python -m insight_agent.export_schemas contracts/ml` from the repository
root with `PYTHONPATH=ml-service/src`, then update the resource copies together.

`source-commit.txt` and `MlContracts.CONTRACT_COMMIT` record the historical ML integration
baseline. The active combined adapter is identified by `MlContracts.CONTRACT_VERSION`;
the baseline SHA does not claim that these extended schemas are unchanged since it.

`catalog-response` uses `catalog_token/events/next_cursor` at `/v1/materials/events`.
`news-page` uses `corpus_revision/items/next_offset` at `/v1/materials/news`.
Feed refresh uses the trusted catalog service, preserving full-token CAS and frozen inputs.
