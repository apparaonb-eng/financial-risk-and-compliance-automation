"""
Run the Financial Risk and Compliance Automation engine.

Usage:
    python main.py                      # generate sample data, screen it, write reports
    python main.py transactions.csv     # screen your own CSV file

CSV columns: txn_id,customer_id,timestamp,amount,currency,counterparty,counterparty_country
(timestamp in ISO format, e.g. 2026-11-20T14:30:00)
"""

import csv
import sys
from datetime import datetime, timedelta

from compliance_engine import (
    ComplianceEngine, export_alerts_csv, export_risk_json, load_transactions,
)

FIELDS = ["txn_id", "customer_id", "timestamp", "amount", "currency",
          "counterparty", "counterparty_country"]


def write_sample_data(path: str) -> None:
    base = datetime(2026, 11, 20, 9, 0)
    rows, n = [], 0

    def add(cust, hours, amount, cp, country):
        nonlocal n
        n += 1
        rows.append([f"T{n:04d}", cust, (base + timedelta(hours=hours)).isoformat(),
                     amount, "USD", cp, country])

    # C001: normal customer
    add("C001", 0, 120.50, "Grocery Mart", "US")
    add("C001", 30, 560.00, "Electric Utility", "US")
    # C002: structuring (three deposits just under 10,000)
    add("C002", 1, 9500, "Cash Deposit", "US")
    add("C002", 10, 9200, "Cash Deposit", "US")
    add("C002", 20, 9800, "Cash Deposit", "US")
    # C003: large transaction + high-risk country
    add("C003", 2, 25000, "Gulf Trading Co", "IR")
    # C004: sanctions hit
    add("C004", 5, 4000, "Northern Star Trading", "AE")
    # C005: high velocity
    for i in range(10):
        add("C005", i * 1.5, 75, "Crypto Exchange X", "SG")

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        w.writerows(rows)


def main() -> None:
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        path = "sample_transactions.csv"
        write_sample_data(path)
        print(f"Generated sample data: {path}")

    transactions = load_transactions(path)
    engine = ComplianceEngine()
    alerts = engine.screen(transactions)
    risks = engine.score_customers(alerts, (t.customer_id for t in transactions))

    print(f"\nScreened {len(transactions)} transactions -> {len(alerts)} alerts\n")
    for a in alerts:
        print(f"[{a.severity:<6}] {a.rule:<18} {a.customer_id}  {a.message}")

    print("\nCustomer risk ranking:")
    for r in risks:
        rules = ", ".join(r.rules_triggered) or "-"
        print(f"  {r.customer_id}  score={r.score:<3} band={r.band:<6} rules: {rules}")

    export_alerts_csv(alerts, "alerts_report.csv")
    export_risk_json(risks, "customer_risk.json")
    print("\nReports written: alerts_report.csv, customer_risk.json")


if __name__ == "__main__":
    main()
