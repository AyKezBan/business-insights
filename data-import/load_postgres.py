"""Atomic CSV import into the dedicated business_insights schema. DSN is read from environment."""
import argparse
import os
from pathlib import Path

from data_core import COMPANY_COLUMNS, PERIOD_DESCRIPTION, VERSION, Dataset

SQL_DIR = Path(__file__).parent / "sql"
SCHEMA_FILES = tuple(SQL_DIR / name for name in ("01_schemas.sql", "02_tables.sql", "03_indexes.sql"))
SCHEMA_VERSION = VERSION
TABLES = ["payment_snapshot", "person_company_link", "company_profile", "person_ref", "company_ref",
          "dataset_metadata"]


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
            cursor.execute("\n".join(path.read_text(encoding="utf-8") for path in SCHEMA_FILES), prepare=False)
            cursor.execute("SELECT schema_version, dataset_sha256 FROM business_insights.dataset_metadata WHERE singleton")
            current = cursor.fetchone()
            if current:
                # 0.2 -> 0.3 keeps all six tables; reload refreshes quality/provenance.
                if current[0] not in {"0.2.0", SCHEMA_VERSION}:
                    raise ValueError("Incompatible business_insights schema version; a reviewed migration is required")
                if current[0] != SCHEMA_VERSION and not replace:
                    raise ValueError("Use --replace-current to upgrade 0.2.0 and rebuild quality metadata")
                if current[0] == SCHEMA_VERSION and current[1] == dataset.dataset_id:
                    return {"status": "unchanged", "dataset_sha256": dataset.dataset_id}
                if not replace:
                    raise ValueError("Different dataset already loaded; use --replace-current to replace only business_insights")
            elif not replace:
                # Protect partial/manual data without matching metadata.
                for table in TABLES[:-1]:
                    cursor.execute(sql.SQL("SELECT EXISTS (SELECT 1 FROM business_insights.{})").format(sql.Identifier(table)))
                    if cursor.fetchone()[0]:
                        raise ValueError("business_insights contains rows without metadata; use a clean DB or explicit --replace-current")
            if replace:
                cursor.execute(sql.SQL("TRUNCATE {} ").format(sql.SQL(", ").join(
                    sql.Identifier("business_insights", table) for table in TABLES)))

            def copy_records(table, columns, rows):
                statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
                    sql.Identifier("business_insights", table), sql.SQL(",").join(map(sql.Identifier, columns)))
                with cursor.copy(statement) as copy:
                    for row in rows:
                        copy.write_row(row)

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
            cursor.execute("""INSERT INTO business_insights.dataset_metadata
                (singleton, schema_version, dataset_sha256, source_manifest, payment_period_description)
                VALUES (TRUE, %s, %s, %s, %s)""", (
                    SCHEMA_VERSION, dataset.dataset_id, Jsonb(dataset.report),
                    PERIOD_DESCRIPTION))
            for table in TABLES:
                cursor.execute(sql.SQL("ANALYZE {}").format(sql.Identifier("business_insights", table)))
    return {"status": "loaded", "dataset_sha256": dataset.dataset_id,
            "rows": {name: info["rows"] for name, info in dataset.files.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--replace-current", action="store_true", help="Replace current business_insights dataset atomically; no history")
    args = parser.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        parser.error("Set DATABASE_URL; its value is never printed by this script")
    result = import_dataset(args.data_dir, dsn, replace=args.replace_current)
    print(result)


if __name__ == "__main__":
    main()
