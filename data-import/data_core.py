"""CSV -> validated typed records and import quality report. Python 3.11+, standard library only."""
from __future__ import annotations

import csv
import hashlib
import re
from collections import Counter
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

VERSION = "0.1.0"
COMPANY = "df_Uid_data_hack.csv"
LINK = "df_Fid_Uid_link_hack.csv"
PAYMENT = "df_Uid_payment_hack.csv"
COMPANY_COLUMNS = (
    "SYNTHETIC_U ORG_TYPE OKFS_NAME COMPANY_AGE_YEARS_GRADATION ECO_MARK_LABEL "
    "MAINOKVED_CODE MAINOKVED_NAME NEGATIVEMARK AMOUNT_WORKERS_FNS_GRADATION "
    "AMOUNT_WORKERS_SPARK_GRADATION CL_TYPE CL_PRODUCT_RKO_MARK CL_PRODUCT_GAR_MARK "
    "CL_PRODUCT_LOA_MARK CL_PRODUCT_LEA_MARK CL_PRODUCT_DEP_MARK CL_PRODUCT_ZPP_MARK "
    "FTS_FLAG FTS_COUNTRY_NAME FACTORING_MARK REVENUE_COMPANY GROUP_ID CITY REGION"
).split()
EXPECTED = {COMPANY: COMPANY_COLUMNS, LINK: "SYNTHETIC_F CITY_NAME RC_NAME SYNTHETIC_U".split(),
            PAYMENT: "BENEFICIARY PAYER PAY_AMT PAY_CNT".split()}
PRODUCTS = {"rko": "CL_PRODUCT_RKO_MARK", "guarantees": "CL_PRODUCT_GAR_MARK",
            "loans": "CL_PRODUCT_LOA_MARK", "leasing": "CL_PRODUCT_LEA_MARK",
            "deposits": "CL_PRODUCT_DEP_MARK", "payroll": "CL_PRODUCT_ZPP_MARK",
            "factoring": "FACTORING_MARK"}
FLAGS = {"NEGATIVEMARK", "FTS_FLAG", *PRODUCTS.values()}


def text(value):
    if value is None:
        return None
    return value.strip() or None

def identifier(value, prefix):
    value = text(value)
    return value if value and re.fullmatch(prefix + r"_[0-9]+", value) else None

def number(value, *, integer=False):
    """Exact, finite and nonnegative. Invalid input is never converted to zero."""
    value = text(value)
    if value is None:
        return None
    try:
        result = Decimal(value)
    except InvalidOperation:
        return None
    if not result.is_finite() or result < 0:
        return None
    if integer:
        return int(result) if result == result.to_integral_value() and result < 2**63 else None
    return result

def flag(value):
    n = number(value)
    return bool(n) if n in (Decimal(0), Decimal(1)) else None

def countries(value):
    """No geopolitical alias merging, country-code inference or trade direction."""
    return sorted({part.strip() for part in value.split(",") if part.strip()}) if text(value) else None

def money(value):
    return format(value, "f") if value is not None else None

def read_csv(data_dir, filename):
    with (Path(data_dir) / filename).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter=";")
        header = reader.fieldnames or []
        if len(set(header)) != len(header) or set(EXPECTED[filename]) - set(header):
            raise ValueError(f"{filename}: missing/duplicate header columns; expected {EXPECTED[filename]}")
        for row_number, row in enumerate(reader, 1):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"{filename}, data row {row_number}: inconsistent CSV column count")
            yield row_number, row

def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def normalize_company(row_number, raw):
    result = {col.lower(): text(raw[col]) for col in COMPANY_COLUMNS}
    issues = []
    for col in FLAGS:
        result[col.lower()] = flag(raw[col])
        if text(raw[col]) and result[col.lower()] is None:
            issues.append(f"invalid:{col}")
    result["synthetic_u"] = identifier(raw["SYNTHETIC_U"], "U")
    result["revenue_company"] = number(raw["REVENUE_COMPANY"])
    status = "observed" if result["revenue_company"] is not None else "missing"
    if text(raw["REVENUE_COMPANY"]) and result["revenue_company"] is None:
        status = "invalid"
    if result["revenue_company"] == Decimal(1000):
        result["revenue_company"], status = None, "placeholder"
    if status != "observed":
        issues.append(f"revenue:{status}")
    if result["cl_type"] not in {"CLIENT", "PROSPECT", "CLOSE_CLIENT", None}:
        issues.append("unknown:CL_TYPE")
        result["cl_type"] = None
    result.update(source_row=row_number, revenue_status=status, fts_countries=countries(raw["FTS_COUNTRY_NAME"]))
    if result["fts_countries"] and result["fts_flag"] is False:
        issues.append("countries_present_without_fts_flag")
    result["quality_issues"] = sorted(issues)
    return result


class Dataset:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.files = {name: {"sha256": file_hash(self.data_dir / name)} for name in EXPECTED}
        signature = "\n".join(f"{name}:{info['sha256']}" for name, info in sorted(self.files.items()))
        self.dataset_id = hashlib.sha256(signature.encode()).hexdigest()
        company_rows = list(read_csv(self.data_dir, COMPANY))
        self.files[COMPANY]["rows"] = len(company_rows)
        self.company_counts = Counter(identifier(r["SYNTHETIC_U"], "U") for _, r in company_rows)
        self.ambiguous_ids = {uid for uid, n in self.company_counts.items() if uid and n > 1}
        self.companies = {}
        for row_number, raw in company_rows:
            company = normalize_company(row_number, raw)
            uid = company["synthetic_u"]
            if uid and uid not in self.ambiguous_ids:
                self.companies[uid] = company
        self.registry = {uid for uid in self.company_counts if uid}
        self.links = list(self.link_rows())
        self.files[LINK]["rows"] = sum(1 for _ in read_csv(self.data_dir, LINK))
        for link in self.links:
            self.registry.add(link["synthetic_u"])
        self.pair_counts = Counter()
        for _, row in read_csv(self.data_dir, PAYMENT):
            payer, beneficiary = identifier(row["PAYER"], "U"), identifier(row["BENEFICIARY"], "U")
            for uid in {payer, beneficiary} - {None}:
                self.registry.add(uid)
            if payer and beneficiary:
                self.pair_counts[(payer, beneficiary)] += 1
        self.files[PAYMENT]["rows"] = sum(self.pair_counts.values())
        self.report = {}

    def verify_hashes(self):
        for name, info in self.files.items():
            if file_hash(self.data_dir / name) != info["sha256"]:
                raise ValueError(f"Source changed during processing: {name}")

    def link_rows(self):
        for source_row, row in read_csv(self.data_dir, LINK):
            uid, fid = identifier(row["SYNTHETIC_U"], "U"), identifier(row["SYNTHETIC_F"], "F")
            if uid and fid:
                yield {"source_row": source_row, "synthetic_f": fid, "synthetic_u": uid,
                       "city_name": text(row["CITY_NAME"]), "rc_name": text(row["RC_NAME"])}

    def payment_rows(self):
        for source_row, row in read_csv(self.data_dir, PAYMENT):
            payer, beneficiary = identifier(row["PAYER"], "U"), identifier(row["BENEFICIARY"], "U")
            amount, count = number(row["PAY_AMT"]), number(row["PAY_CNT"], integer=True)
            issues = []
            if not payer or not beneficiary:
                issues.append("missing_or_invalid_company_id")
            if amount is None or count is None:
                issues.append("missing_or_invalid_payment_number")
            if payer in self.ambiguous_ids or beneficiary in self.ambiguous_ids:
                issues.append("ambiguous_company_id")
            if payer and beneficiary and self.pair_counts[(payer, beneficiary)] > 1:
                issues.append("repeated_payment_pair")
            yield {"row_id": source_row, "payer": payer, "beneficiary": beneficiary, "pay_amt": amount,
                   "pay_cnt": count, "ml_eligible": not issues, "quality_issues": issues}

    def build_report(self):
        issues = Counter()
        row_count, eligible_count, exact_duplicates = 0, 0, 0
        repeated_records = set()
        raw_total, raw_self = Decimal(0), Decimal(0)
        raw_operations, raw_self_operations = 0, 0
        with localcontext() as ctx:
            ctx.prec = 80
            for row in self.payment_rows():
                row_count += 1
                payer, beneficiary, amount, count = (row[k] for k in ("payer", "beneficiary", "pay_amt", "pay_cnt"))
                issues.update(row["quality_issues"])
                if "repeated_payment_pair" in row["quality_issues"]:
                    signature = (payer, beneficiary, amount, count)
                    exact_duplicates += signature in repeated_records
                    repeated_records.add(signature)
                if amount is not None:
                    raw_total += amount
                    if payer and payer == beneficiary:
                        raw_self += amount
                if count is not None:
                    raw_operations += count
                    if payer and payer == beneficiary:
                        raw_self_operations += count
                if not row["ml_eligible"]:
                    continue
                eligible_count += 1
        self.files[PAYMENT]["rows"] = row_count
        self.report = {
            "dataset_sha256": self.dataset_id, "files": self.files,
            "company_profiles": len(self.companies), "company_registry": len(self.registry),
            "company_invalid_id_rows": self.company_counts.get(None, 0),
            "ambiguous_company_ids": sorted(self.ambiguous_ids),
            "ambiguous_company_rows": sum(self.company_counts[uid] for uid in self.ambiguous_ids),
            "links_typed": len(self.links), "links_invalid": self.files[LINK]["rows"] - len(self.links),
            "payment_rows": row_count, "payment_rows_ml_eligible": eligible_count,
            "payment_rows_excluded": row_count - eligible_count, "payment_issue_counts": dict(issues),
            "payment_exact_numeric_duplicates_in_repeated_pairs": exact_duplicates,
            "raw_payment_amount_rub": money(raw_total), "raw_self_amount_rub": money(raw_self),
            "raw_payment_operations": raw_operations, "raw_self_operations": raw_self_operations,
            "fts_distinct_nonempty_strings": len({c["fts_country_name"] for c in self.companies.values() if c["fts_country_name"]}),
            "fts_distinct_tokens": len({v for c in self.companies.values() for v in c["fts_countries"] or []}),
            "revenue_status_counts": dict(Counter(c["revenue_status"] for c in self.companies.values())),
            "verification": {"full_csv_processing": True, "postgresql_execution": False},
        }
        return self.report
