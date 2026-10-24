"""Atomic CSV import into the dedicated bi_data schema. DSN is read from environment."""
import argparse
import os
from pathlib import Path

from data_core import COMPANY, COMPANY_COLUMNS, LINK, PAYMENT, VERSION, Dataset, read_csv

SCHEMA = Path(__file__).parent / "sql" / "schema.sql"
TABLES = ["payment_snapshot", "person_company_link", "company_profile", "person_ref", "company_ref",
          "raw_company", "raw_link", "raw_payment", "dataset_metadata"]


def import_dataset(data_dir, dsn, *, replace=False):
    # Lazy dependency: CSV preparation and its tests run without psycopg.
    import psycopg
    from psycopg import sql
    from psycopg.types.json import Jsonb

    dataset = Dataset(data_dir)
    dataset.build_report()
    dataset.verify_hashes()
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            # Same transaction: serialize loaders; block service reads during replacement.
            cursor.execute("SELECT pg_advisory_xact_lock(684901236)")
            cursor.execute(SCHEMA.read_text(encoding="utf-8"), prepare=False)
            cursor.execute("SELECT schema_version, dataset_sha256 FROM bi_data.dataset_metadata WHERE singleton")
            current = cursor.fetchone()
            if current:
                if current[0] != VERSION:
                    raise ValueError("Incompatible bi_data schema version; a reviewed migration is required")
                if current[1] == dataset.dataset_id:
                    return {"status": "unchanged", "dataset_sha256": dataset.dataset_id}
                if not replace:
                    raise ValueError("Different dataset already loaded; use --replace-current to replace only bi_data")
            elif not replace:
                # Protect partial/manual data without matching metadata.
                for table in TABLES[:-1]:
                    cursor.execute(sql.SQL("SELECT EXISTS (SELECT 1 FROM bi_data.{})").format(sql.Identifier(table)))
                    if cursor.fetchone()[0]:
                        raise ValueError("bi_data contains rows without metadata; use a clean DB or explicit --replace-current")
            if replace:
                cursor.execute(sql.SQL("TRUNCATE {} ").format(sql.SQL(", ").join(
                    sql.Identifier("bi_data", table) for table in TABLES)))

            def copy_records(table, columns, rows):
                statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
                    sql.Identifier("bi_data", table), sql.SQL(",").join(map(sql.Identifier, columns)))
                with cursor.copy(statement) as copy:
                    for row in rows:
                        copy.write_row(row)

            for filename, table in [(COMPANY, "raw_company"), (LINK, "raw_link"), (PAYMENT, "raw_payment")]:
                copy_records(table, ["source_row", "payload"],
                             ((n, Jsonb(row)) for n, row in read_csv(data_dir, filename)))

            copy_records("company_ref", ["synthetic_u", "profile_status"], (
                (uid, "ambiguous" if uid in dataset.ambiguous_ids else
                 "available" if uid in dataset.companies else "external") for uid in sorted(dataset.registry)))
            company_columns = [col.lower() for col in COMPANY_COLUMNS] + [
                "source_row", "revenue_status", "fts_countries", "quality_issues"]
            copy_records("company_profile", company_columns,
                         ([c[col] for col in company_columns] for c in dataset.companies.values()))
            copy_records("person_ref", ["synthetic_f"], ((fid,) for fid in sorted({r["synthetic_f"] for r in dataset.links})))
            link_columns = ["source_row", "synthetic_f", "synthetic_u", "city_name", "rc_name"]
            copy_records("person_company_link", link_columns, ([r[col] for col in link_columns] for r in dataset.links))
            payment_columns = ["row_id", "payer", "beneficiary", "pay_amt", "pay_cnt", "ml_eligible", "quality_issues"]
            copy_records("payment_snapshot", payment_columns,
                         ([r[col] for col in payment_columns] for r in dataset.payment_rows()))
            dataset.verify_hashes()  # Fail/rollback if CSV changed while COPY was running.
            cursor.execute("""INSERT INTO bi_data.dataset_metadata
                (singleton, schema_version, dataset_sha256, source_manifest, payment_period_description)
                VALUES (TRUE, %s, %s, %s, %s)""", (
                    VERSION, dataset.dataset_id, Jsonb(dataset.report),
                    "С 1 января; год начала и дата окончания не установлены"))
            for table in TABLES:
                cursor.execute(sql.SQL("ANALYZE {}").format(sql.Identifier("bi_data", table)))
    return {"status": "loaded", "dataset_sha256": dataset.dataset_id,
            "rows": {name: info["rows"] for name, info in dataset.files.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--replace-current", action="store_true", help="Replace current bi_data dataset atomically; no history")
    args = parser.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        parser.error("Set DATABASE_URL; its value is never printed by this script")
    result = import_dataset(args.data_dir, dsn, replace=args.replace_current)
    print(result)


if __name__ == "__main__":
    main()
