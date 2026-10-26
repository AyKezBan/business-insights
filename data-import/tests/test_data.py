import tempfile
import unittest
from decimal import Decimal

from data_core import Dataset, countries, flag, identifier, number, read_csv, PAYMENT
from tests.fixtures import write_fixture


class ConversionTests(unittest.TestCase):
    def test_exact_numeric_and_unknowns(self):
        self.assertEqual(number("123456789012345.67"), Decimal("123456789012345.67"))
        self.assertEqual(number("9007199254740993", integer=True), 9007199254740993)
        self.assertEqual(number("0"), Decimal(0))
        for value in ["", "NULL", "NaN", "Infinity", "-1", "garbage"]:
            self.assertIsNone(number(value))
        for value in ["1.5", str(2**63)]:
            self.assertIsNone(number(value, integer=True))

    def test_flags_and_country_tokens(self):
        self.assertIs(flag("0.0"), False)
        self.assertIs(flag("1"), True)
        self.assertIsNone(flag("2"))
        self.assertEqual(countries("КИТАЙ, ОАЭ, КИТАЙ"), ["КИТАЙ", "ОАЭ"])
        self.assertIsNone(countries(" "))
        self.assertEqual(identifier(" U_1 ", "U"), "U_1")
        self.assertIsNone(identifier("1", "U"))


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = write_fixture(self.temp.name)
        self.dataset = Dataset(self.root)
        self.dataset.build_report()
        self.payments = list(self.dataset.payment_rows())

    def test_quarantine_and_registry(self):
        self.assertEqual(self.dataset.ambiguous_ids, {"U_9"})
        self.assertNotIn("U_9", self.dataset.companies)
        self.assertIn("U_99", self.dataset.registry)
        self.assertNotIn("U_99", self.dataset.companies)
        self.assertEqual(self.dataset.report["links_invalid"], 1)
        self.assertEqual(self.dataset.report["ambiguous_company_rows"], 2)
        self.assertEqual(len(self.dataset.links), 4)

    def test_payments_self_duplicates_unknowns(self):
        self.assertEqual(self.payments[0]["pay_amt"], Decimal("0.1"))
        self.assertEqual(self.payments[1]["pay_amt"], Decimal("0.2"))
        self.assertEqual(self.payments[2]["payer"], self.payments[2]["beneficiary"])
        self.assertEqual(self.payments[2]["pay_amt"], Decimal("100"))
        self.assertEqual(self.payments[2]["pay_cnt"], 5)
        self.assertFalse(self.payments[3]["ml_eligible"])
        self.assertIn("ambiguous_company_id", self.payments[3]["quality_issues"])
        for payment in self.payments[4:6]:
            self.assertFalse(payment["ml_eligible"])
            self.assertIn("repeated_payment_pair", payment["quality_issues"])
        self.assertIsNone(self.payments[6]["pay_amt"])
        self.assertFalse(self.payments[6]["ml_eligible"])
        self.assertEqual(self.dataset.report["payment_rows_ml_eligible"], 4)
        self.assertEqual(self.dataset.report["payment_rows_excluded"], 4)
        self.assertEqual(self.dataset.report["payment_exact_numeric_duplicates_in_repeated_pairs"], 1)

    def test_missing_is_not_zero(self):
        self.assertEqual(self.dataset.companies["U_3"]["revenue_company"], Decimal("0"))
        self.assertFalse(any("U_3" in (p["payer"], p["beneficiary"]) for p in self.payments))
        company = self.dataset.companies["U_1"]
        self.assertIsNone(company["revenue_company"])
        self.assertEqual(company["revenue_status"], "placeholder")
        self.assertEqual(self.payments[7]["pay_amt"], Decimal("0"))
        self.assertTrue(self.payments[7]["ml_eligible"])
        self.assertEqual(company["mainokved_code"], "01.10")
        self.assertEqual(self.dataset.companies["U_2"]["revenue_company"], Decimal("123456789012345.67"))

    def test_source_change_and_malformed_csv(self):
        path = self.root / PAYMENT
        with path.open("a", encoding="utf-8") as stream:
            stream.write("bad;column;count\n")
        with self.assertRaises(ValueError):
            list(read_csv(self.root, PAYMENT))
        with self.assertRaises(ValueError):
            self.dataset.verify_hashes()


if __name__ == "__main__":
    unittest.main()
