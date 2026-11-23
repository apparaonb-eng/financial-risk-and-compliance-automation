import unittest
from datetime import datetime, timedelta

from compliance_engine import ComplianceEngine, Config, Transaction

BASE = datetime(2026, 11, 20, 9, 0)


def txn(i, cust="C1", hours=0, amount=100.0, cp="Shop", country="US"):
    return Transaction(f"T{i}", cust, BASE + timedelta(hours=hours), amount, "USD", cp, country)


def rules_of(alerts):
    return sorted(a.rule for a in alerts)


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.engine = ComplianceEngine(Config())

    def test_clean_customer_has_no_alerts(self):
        self.assertEqual(self.engine.screen([txn(1), txn(2, hours=5)]), [])

    def test_large_transaction(self):
        alerts = self.engine.screen([txn(1, amount=10_000)])
        self.assertEqual(rules_of(alerts), ["LARGE_TRANSACTION"])

    def test_just_below_threshold_not_large(self):
        self.assertEqual(self.engine.screen([txn(1, amount=9_999.99)]), [])

    def test_structuring(self):
        txns = [txn(1, hours=0, amount=9500), txn(2, hours=10, amount=9200), txn(3, hours=20, amount=9800)]
        alerts = self.engine.screen(txns)
        self.assertEqual(rules_of(alerts), ["STRUCTURING"])
        self.assertEqual(len(alerts[0].txn_ids), 3)

    def test_structuring_outside_window(self):
        txns = [txn(1, hours=0, amount=9500), txn(2, hours=60, amount=9200), txn(3, hours=120, amount=9800)]
        self.assertEqual(self.engine.screen(txns), [])

    def test_velocity(self):
        txns = [txn(i, hours=i * 0.5) for i in range(10)]
        self.assertEqual(rules_of(self.engine.screen(txns)), ["HIGH_VELOCITY"])

    def test_high_risk_country(self):
        alerts = self.engine.screen([txn(1, country="KP")])
        self.assertEqual(rules_of(alerts), ["HIGH_RISK_COUNTRY"])

    def test_sanctions_case_insensitive(self):
        alerts = self.engine.screen([txn(1, cp="  IVAN Petrov ")])
        self.assertEqual(rules_of(alerts), ["SANCTIONS_HIT"])

    def test_rules_are_per_customer(self):
        # Three near-threshold deposits spread across different customers must not be structuring
        txns = [txn(1, cust="A", amount=9500), txn(2, cust="B", amount=9500, hours=1), txn(3, cust="C", amount=9500, hours=2)]
        self.assertEqual(self.engine.screen(txns), [])


class ScoringTests(unittest.TestCase):
    def test_scoring_and_bands(self):
        engine = ComplianceEngine()
        txns = [
            txn(1, cust="BAD", amount=25000, country="IR"),   # LARGE (15) + HIGH_RISK_COUNTRY (30) = 45
            txn(2, cust="BAD", cp="northern star trading"),   # SANCTIONS (30) -> 75
            txn(3, cust="OK"),
        ]
        alerts = engine.screen(txns)
        risks = {r.customer_id: r for r in engine.score_customers(alerts, [t.customer_id for t in txns])}
        self.assertEqual(risks["BAD"].score, 75)
        self.assertEqual(risks["BAD"].band, "HIGH")
        self.assertEqual(risks["OK"].score, 0)
        self.assertEqual(risks["OK"].band, "LOW")

    def test_score_capped_at_100(self):
        engine = ComplianceEngine()
        txns = [txn(i, country="IR", amount=50_000, hours=i) for i in range(10)]
        risks = engine.score_customers(engine.screen(txns), ["C1"])
        self.assertEqual(risks[0].score, 100)


if __name__ == "__main__":
    unittest.main()
