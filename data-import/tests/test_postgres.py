"""Integration test: requires explicit BI_TEST_DATABASE_URL for a disposable DB.

Replaces only the business_insights schema's contents. Does not run against DATABASE_URL.
"""
import os
import tempfile
import unittest
from unittest.mock import patch

from data_core import COMPANY, COMPANY_COLUMNS, Dataset
from tests.fixtures import write_fixture


@unittest.skipUnless(os.environ.get("BI_TEST_DATABASE_URL"), "No disposable PostgreSQL test DSN supplied")
class PostgresTests(unittest.TestCase):
    def test_import_rerun_and_rollback(self):
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row
        from load_postgres import SCHEMA_VERSION, TABLES, import_dataset
        dsn = os.environ["BI_TEST_DATABASE_URL"]
        with tempfile.TemporaryDirectory() as temp:
            root = write_fixture(temp)
            loaded = import_dataset(root, dsn, replace=True)
            self.assertEqual(loaded["status"], "loaded")
            self.assertEqual(import_dataset(root, dsn)["status"], "unchanged")
            dataset = Dataset(root)
            dataset.build_report()
            with psycopg.connect(dsn) as conn:
                tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'business_insights'").fetchall()
                self.assertEqual({row[0] for row in tables}, set(TABLES))
                self.assertEqual(conn.execute("SELECT schema_version FROM business_insights.dataset_metadata").fetchone()[0],
                                 SCHEMA_VERSION)
                registry = dict(conn.execute("SELECT synthetic_u, profile_status FROM business_insights.company_ref").fetchall())
                self.assertEqual(registry, {uid: "ambiguous" if uid in dataset.ambiguous_ids else
                                           "available" if uid in dataset.companies else "external"
                                           for uid in dataset.registry})
                with conn.cursor(row_factory=dict_row) as cursor:
                    columns = [col.lower() for col in COMPANY_COLUMNS] + [
                        "source_row", "revenue_status", "fts_countries", "quality_issues"]
                    cursor.execute(sql.SQL("SELECT {} FROM business_insights.company_profile ORDER BY synthetic_u").format(
                        sql.SQL(", ").join(map(sql.Identifier, columns))))
                    self.assertEqual(cursor.fetchall(), sorted(dataset.companies.values(), key=lambda c: c["synthetic_u"]))
                    cursor.execute("SELECT * FROM business_insights.person_company_link ORDER BY source_row")
                    self.assertEqual(cursor.fetchall(), dataset.links)
                    cursor.execute("SELECT * FROM business_insights.payment_snapshot ORDER BY row_id")
                    self.assertEqual(cursor.fetchall(), list(dataset.payment_rows()))
                self.assertEqual(conn.execute("SELECT synthetic_f FROM business_insights.person_ref ORDER BY synthetic_f").fetchall(),
                                 [(fid,) for fid in sorted({link["synthetic_f"] for link in dataset.links})])
                self.assertEqual(conn.execute("SELECT source_manifest FROM business_insights.dataset_metadata").fetchone()[0], dataset.report)
                before = conn.execute("SELECT dataset_sha256 FROM business_insights.dataset_metadata").fetchone()[0]
                self.assertEqual(conn.execute("SELECT count(*) FROM business_insights.payment_snapshot").fetchone()[0], 8)
            # Change the input fingerprint, then force COPY to violate its PK.
            path = root / COMPANY
            path.write_text(path.read_text(encoding="utf-8-sig").replace("123456789012345.67", "123456789012345.68"), encoding="utf-8-sig")
            with self.assertRaises(ValueError):
                import_dataset(root, dsn)
            original = Dataset.payment_rows
            def corrupt(self):
                first = None
                for row in original(self):
                    first = first or row
                    yield row
                yield first
            with patch.object(Dataset, "payment_rows", corrupt):
                with self.assertRaises(psycopg.errors.UniqueViolation):
                    import_dataset(root, dsn, replace=True)
            with psycopg.connect(dsn) as conn:
                self.assertEqual(conn.execute("SELECT dataset_sha256 FROM business_insights.dataset_metadata").fetchone()[0], before)
                self.assertEqual(conn.execute("SELECT count(*) FROM business_insights.payment_snapshot").fetchone()[0], 8)


if __name__ == "__main__":
    unittest.main()
