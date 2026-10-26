import csv
from pathlib import Path
from data_core import COMPANY, COMPANY_COLUMNS, EXPECTED, LINK, PAYMENT


def write_fixture(root):
    root = Path(root)
    root.mkdir(exist_ok=True, parents=True)
    def company(uid, revenue="1000", fts="1", country="КИТАЙ, ОАЭ, КИТАЙ"):
        c = dict.fromkeys(COMPANY_COLUMNS, "")
        c.update(SYNTHETIC_U=uid, REVENUE_COMPANY=revenue, CL_TYPE="CLIENT", MAINOKVED_CODE="01.10",
                 FTS_FLAG=fts, FTS_COUNTRY_NAME=country, CL_PRODUCT_LOA_MARK="1.0", GROUP_ID="g1")
        return c
    rows = {
        COMPANY: [company("U_1"), company("U_2", "123456789012345.67"),
                  company("U_9", "1"), company("U_9", "2"), company("U_3", "0", "0", "")],
        LINK: [dict(zip(EXPECTED[LINK], values)) for values in [
            ("F_1", "Москва", "Регион1", "U_1"), ("F_1", "Казань", "Регион2", "U_2"),
            ("F_2", "", "", "U_99"), ("F_1", "", "", "U_9"), ("bad", "", "", "U_1")]],
        PAYMENT: [dict(zip(EXPECTED[PAYMENT], values)) for values in [
            ("U_99", "U_1", "0.1", "3"), ("U_1", "U_2", "0.2", "4"),
            ("U_1", "U_1", "100", "5"), ("U_1", "U_9", "999", "1"),
            ("U_2", "U_99", "7", "1"), ("U_2", "U_99", "7", "1"),
            ("U_1", "U_98", "bad", "1"), ("U_2", "U_97", "0", "2")]],
    }
    for name, data in rows.items():
        with (root / name).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=EXPECTED[name], delimiter=";")
            writer.writeheader()
            writer.writerows(data)
    return root
