# Offline fixture CI

`.github/workflows/offline-ci.yml` runs on pushes, pull requests, and manual dispatch.
It installs public dependencies, then uses synthetic fixtures and local test servers:

- ML: the full `pytest` suite and a fresh, byte-for-byte export comparison with every `contracts/ml/*.schema.json` file.
- Data import: unit tests and the real PostgreSQL import/rerun/rollback test.
- Java: `mvn -B -ntp -f backend-api/pom.xml -DfailIfNoTests=true clean test`, including real PostgreSQL persistence, catalog, and queued-input tests.
- Frontend: Python tests and every `tests/test_*.cjs` Node suite, including catalog, context and evidence-span checks.

The PostgreSQL 17 service belongs only to that CI job. The importer uses `ci_import`;
Java receives the loopback `/postgres` administration URL and creates/drops its own
generated test databases. These are disposable test credentials, without repository secrets.
No private corpus, customer CSV files, PDF files, source ingestion commands, or paid model
calls are required. Dependency installation uses the network; “offline” describes the test inputs.
This workflow supplies fixture checks; live release acceptance remains a separate explicit run.

`check_test_reports.py` checks pytest and Maven JUnit XML reports. It rejects missing/empty
results, failures, errors, and skipped cases; required database test classes must appear.
Run its regression tests from the repository root with:

```sh
python -m unittest discover -s infra/ci/tests -v
```
