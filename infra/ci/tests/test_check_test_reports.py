import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_test_reports import verify_reports


class ReportTests(unittest.TestCase):
    def report(self, xml):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'report.xml'
        path.write_text(xml, encoding='utf-8')
        return path

    def test_counts_pytest_and_surefire_cases(self):
        pytest = self.report('<testsuites><testsuite><testcase classname="python" name="one"/></testsuite></testsuites>')
        java = self.report('<testsuite><testcase classname="example.PostgresTest" name="two"/></testsuite>')
        self.assertEqual(verify_reports([pytest, java], ['example.PostgresTest']), 2)

    def test_rejects_skipped_database_case(self):
        path = self.report('<testsuite><testcase classname="PostgresTest" name="db"><skipped message="No DSN"/></testcase></testsuite>')
        with self.assertRaisesRegex(ValueError, 'skipped'):
            verify_reports([path], [])

    def test_rejects_failed_or_errored_case(self):
        for outcome in ('failure', 'error'):
            with self.subTest(outcome=outcome):
                path = self.report(f'<testsuite><testcase name="test"><{outcome}/></testcase></testsuite>')
                with self.assertRaisesRegex(ValueError, outcome):
                    verify_reports([path], [])

    def test_rejects_empty_and_missing_reports(self):
        for paths in ([], [self.report('<testsuite tests="0"/>')]):
            with self.subTest(paths=paths), self.assertRaisesRegex(ValueError, 'No test cases'):
                verify_reports(paths, [])

    def test_rejects_missing_required_database_class(self):
        path = self.report('<testsuite><testcase classname="UnitTest" name="one"/></testsuite>')
        with self.assertRaisesRegex(ValueError, 'PostgresTest'):
            verify_reports([path], ['PostgresTest'])


if __name__ == '__main__':
    unittest.main()
