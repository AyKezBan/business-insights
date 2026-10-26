JSON Schema generated from ML protocol commit `e330ae5b7751a81f6deab0446149a4666e8ef7c4`.

These resources are bundled in the JAR and validate requests/responses before typed decoding.
Regenerate from `insight_agent.export_schemas` using the intended worktree's PYTHONPATH;
copy the five ML exchange schemas plus catalog-token, material-event and catalog-response. Keep the source commit in
`source-commit.txt` and `MlContracts.CONTRACT_COMMIT` in sync. Runtime/semantic schema
changes require another export; do not hand-edit generated JSON.

Claims and impacts carry optional `evidence_spans` (legacy default `[]`). Each span
identifies an exact source body by opaque source ID and SHA-256, with nonempty
`[start,end)` offsets measured in Unicode code points. Java and the UI preserve these
offsets without slicing or translating the already rendered ML claim text.
