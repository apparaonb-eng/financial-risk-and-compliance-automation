"""
Financial Risk and Compliance Automation
----------------------------------------
A simple rule-based engine that:

  1. Screens transactions against AML / compliance rules
       - LARGE_TRANSACTION   : single transaction at/above a reporting threshold
       - STRUCTURING         : several just-below-threshold transactions in a short window
       - HIGH_VELOCITY       : too many transactions in a short window
       - HIGH_RISK_COUNTRY   : counterparty in a high-risk jurisdiction
       - SANCTIONS_HIT       : counterparty name matches a sanctions watchlist
  2. Raises alerts with a severity and an explanation.
  3. Scores each customer's overall risk (0-100) and assigns a risk band.
  4. Exports alerts and customer risk summaries as CSV / JSON.

All thresholds live in `Config` so compliance teams can tune them without touching the rules.
"""

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Set

SEVERITY_WEIGHT = {"LOW": 5, "MEDIUM": 15, "HIGH": 30}


# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
@dataclass
class Config:
    large_txn_threshold: float = 10_000.0
    structuring_floor: float = 8_000.0      # "just below" range = [floor, threshold)
    structuring_min_count: int = 3
    structuring_window_hours: int = 48
    velocity_max_count: int = 8
    velocity_window_hours: int = 24
    high_risk_countries: Set[str] = field(default_factory=lambda: {"IR", "KP", "SY", "MM"})
    sanctions_watchlist: Set[str] = field(default_factory=lambda: {
        "blackrock shell ltd", "northern star trading", "ivan petrov",
    })


# ----------------------------------------------------------------------------
# Data classes
# ----------------------------------------------------------------------------
@dataclass
class Transaction:
    txn_id: str
    customer_id: str
    timestamp: datetime
    amount: float
    currency: str
    counterparty: str
    counterparty_country: str

    @staticmethod
    def from_row(row: Dict[str, str]) -> "Transaction":
        return Transaction(
            txn_id=row["txn_id"],
            customer_id=row["customer_id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            amount=float(row["amount"]),
            currency=row.get("currency", "USD"),
            counterparty=row["counterparty"],
            counterparty_country=row["counterparty_country"].upper(),
        )


@dataclass
class Alert:
    rule: str
    severity: str
    customer_id: str
    txn_ids: List[str]
    message: str


@dataclass
class CustomerRisk:
    customer_id: str
    score: int
    band: str
    alert_count: int
    rules_triggered: List[str]


# ----------------------------------------------------------------------------
# Rules  (each rule: (transactions of ONE customer sorted by time, config) -> alerts)
# ----------------------------------------------------------------------------
def rule_large_transaction(txns: List[Transaction], cfg: Config) -> List[Alert]:
    return [
        Alert("LARGE_TRANSACTION", "MEDIUM", t.customer_id, [t.txn_id],
              f"Transaction of {t.amount:,.2f} {t.currency} meets the reporting threshold "
              f"of {cfg.large_txn_threshold:,.2f}")
        for t in txns if t.amount >= cfg.large_txn_threshold
    ]


def rule_structuring(txns: List[Transaction], cfg: Config) -> List[Alert]:
    near = [t for t in txns if cfg.structuring_floor <= t.amount < cfg.large_txn_threshold]
    window = timedelta(hours=cfg.structuring_window_hours)
    alerts, flagged = [], set()
    for i, start in enumerate(near):
        group = [t for t in near[i:] if t.timestamp - start.timestamp <= window]
        ids = tuple(t.txn_id for t in group)
        if len(group) >= cfg.structuring_min_count and not flagged.intersection(ids):
            flagged.update(ids)
            total = sum(t.amount for t in group)
            alerts.append(Alert(
                "STRUCTURING", "HIGH", start.customer_id, list(ids),
                f"{len(group)} transactions just below the reporting threshold within "
                f"{cfg.structuring_window_hours}h totalling {total:,.2f}"))
    return alerts


def rule_velocity(txns: List[Transaction], cfg: Config) -> List[Alert]:
    window = timedelta(hours=cfg.velocity_window_hours)
    alerts, flagged = [], set()
    for i, start in enumerate(txns):
        group = [t for t in txns[i:] if t.timestamp - start.timestamp <= window]
        ids = tuple(t.txn_id for t in group)
        if len(group) > cfg.velocity_max_count and not flagged.intersection(ids):
            flagged.update(ids)
            alerts.append(Alert(
                "HIGH_VELOCITY", "MEDIUM", start.customer_id, list(ids),
                f"{len(group)} transactions within {cfg.velocity_window_hours}h "
                f"(limit {cfg.velocity_max_count})"))
    return alerts


def rule_high_risk_country(txns: List[Transaction], cfg: Config) -> List[Alert]:
    return [
        Alert("HIGH_RISK_COUNTRY", "HIGH", t.customer_id, [t.txn_id],
              f"Counterparty '{t.counterparty}' is in high-risk jurisdiction {t.counterparty_country}")
        for t in txns if t.counterparty_country in cfg.high_risk_countries
    ]


def rule_sanctions(txns: List[Transaction], cfg: Config) -> List[Alert]:
    return [
        Alert("SANCTIONS_HIT", "HIGH", t.customer_id, [t.txn_id],
              f"Counterparty '{t.counterparty}' matches the sanctions watchlist")
        for t in txns if t.counterparty.strip().lower() in cfg.sanctions_watchlist
    ]


DEFAULT_RULES = [
    rule_large_transaction,
    rule_structuring,
    rule_velocity,
    rule_high_risk_country,
    rule_sanctions,
]


# ----------------------------------------------------------------------------
# Engine
# ----------------------------------------------------------------------------
class ComplianceEngine:
    def __init__(self, config: Config = None, rules=None):
        self.config = config or Config()
        self.rules = rules or DEFAULT_RULES

    def screen(self, transactions: Iterable[Transaction]) -> List[Alert]:
        by_customer: Dict[str, List[Transaction]] = defaultdict(list)
        for t in transactions:
            by_customer[t.customer_id].append(t)

        alerts: List[Alert] = []
        for cust_txns in by_customer.values():
            cust_txns.sort(key=lambda t: t.timestamp)
            for rule in self.rules:
                alerts.extend(rule(cust_txns, self.config))
        return alerts

    @staticmethod
    def score_customers(alerts: List[Alert], customer_ids: Iterable[str]) -> List[CustomerRisk]:
        per_cust: Dict[str, List[Alert]] = defaultdict(list)
        for a in alerts:
            per_cust[a.customer_id].append(a)

        results = []
        for cid in sorted(set(customer_ids)):
            cust_alerts = per_cust.get(cid, [])
            score = min(100, sum(SEVERITY_WEIGHT[a.severity] for a in cust_alerts))
            band = "HIGH" if score >= 60 else "MEDIUM" if score >= 25 else "LOW"
            results.append(CustomerRisk(
                cid, score, band, len(cust_alerts),
                sorted({a.rule for a in cust_alerts})))
        return sorted(results, key=lambda r: r.score, reverse=True)


# ----------------------------------------------------------------------------
# I/O helpers
# ----------------------------------------------------------------------------
def load_transactions(path: str) -> List[Transaction]:
    with open(path, newline="", encoding="utf-8") as f:
        return [Transaction.from_row(row) for row in csv.DictReader(f)]


def export_alerts_csv(alerts: List[Alert], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rule", "severity", "customer_id", "txn_ids", "message"])
        for a in alerts:
            w.writerow([a.rule, a.severity, a.customer_id, ";".join(a.txn_ids), a.message])


def export_risk_json(risks: List[CustomerRisk], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in risks], f, indent=2)
