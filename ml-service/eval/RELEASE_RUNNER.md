# Explicit release acceptance runner

Run from the repository root with the ML environment activated:

```sh
PYTHONPATH=ml-service/src python ml-service/eval/run_release_acceptance.py \
  --manifest .local/release-preflight/release-holdout-frozen.json \
  --store .local/materials \
  --profiles ml-service/eval/release-profiles-2026-10-26.json \
  --out .local/release-results
```

The default is **dry-run**: validate the frozen manifest, exact published revision
and catalog token, event/source identities, profile-file hash, pair references,
and existing journals. It never constructs a provider client, reads credentials
or an env file, writes output, or fetches sources. It validates every manifest
pair even when a subset is selected.

Add `--live` to execute. Credentials come from `LLM_API_KEY` / `LLM_BASE_URL`
(or `API_KEY` / `BASE_URL`), or from an explicit `--env-file` containing those
names. Environment values take precedence. There are no credential arguments;
do not put keys in command lines. Repeat `--case-id CASE` to select a bounded
subset of the frozen pairs.

The runner calls production `build_agent`, `agent.run`, and `answer_question`
with one shared client and the existing budget/deadline guards. Every request
pins the frozen revision and semantic event hash. Additional open-source
fetching is disabled so execution uses the immutable corpus. One agent instance
retains the production analysis cache across selected pairs.

Default model for all steps is `google/gemma-4-26b-a4b-it`; both `--model` and
optional `--fallback-model` pass the production model allowlist. Defaults are
60 seconds per HTTP request, 120 seconds per agent/QA run, 40,000 tokens per run,
2,000,000 tokens per day, 3 refinement iterations, and a 100 RUB **estimated**
budget in this output directory's persistent production ledger. Configure these
with `--timeout-seconds`, `--run-deadline-seconds`, `--run-token-budget`,
`--daily-token-budget`, `--max-iterations`, and `--budget-rub`. The 1,800-second
`--deadline-seconds` session limit is checked before starting each case; it does
not cancel an in-flight HTTP request. Production deadlines are likewise checked
between model steps. The HTTP timeout bounds each request, not the entire case.

The default local price assumptions are 0.5 RUB / 1,000 input tokens and 1.5 RUB /
1,000 output tokens. Override with `--price-input-rub-per-1k` and
`--price-output-rub-per-1k`. These are estimates for local guards, not verified
tariffs or actual gateway charges. Raw gateway `usage` (including `cost`) is
preserved separately, with currency `unknown` unless the gateway explicitly
supplies `usage.currency`. This runner makes no `/key/info` calls and never
attributes a shared-key counter delta to this evaluation.

The caller's output directory must be private (0700) and contain no symlinks;
new output uses a private umask. It contains full requests, model prompts/responses, results, QA, production
traces, retry/error journals, provenance hashes, and code/prompt/schema versions.
Keep it outside Git and restrict access. Stdout is one JSON metadata record;
article text, generated text, and credentials are not printed. Transport hooks
observe the existing production client's requests/responses; they add no calls
or retries. HTTP 400 JSON-mode fallback attempts are counted separately.

Each `cases/CASE/` directory is claimed exclusively before any inference for
that case. `started.json` and append-only attempt journals survive interruption;
`result.json` is written atomically. An unchanged completed case is skipped
without creating a client or reading credentials. A completed case with different
inputs, or any incomplete journal, is refused before inference. Review incomplete
journals manually; the runner never automatically repeats potentially billed
work. Preserve the original directory when a deliberate replacement evaluation
is authorized, and give that evaluation a new output directory.

Exit codes: 0 means validation/execution finished, 1 means a completed case had
an execution error (including terminal QA provider/budget/corpus errors), and 2 means input/configuration/journal validation failed or
the session deadline prevented another case. Execution stops after its first
case error. A saved case remains completed even if the pipeline returned failed,
so resuming cannot silently rebill it. Completed results with execution errors
must be inspected; a later completed-only resume reports only skips.

Every case starts with `manual_verdict: NOT_REVIEWED`. Schema validation and the
agent's self-check are execution evidence, not semantic acceptance. The frozen
rubric must be scored separately; the runner never awards a PASS.

Offline tests use real synthetic snapshot publication and the production agent
with a fake ChatClient / HTTP MockTransport. They require no corpus, article,
credential, or provider access:

```sh
PYTHONPATH=ml-service/src python -m pytest -q ml-service/tests/test_release_acceptance_runner.py
```
