"""Testes offline (sem rede): parsing da Gamma, banco e detecção de alertas.

Rodar:  python -m unittest discover -s tests -v
"""

import sys
import os
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from countries import OTHER_LABEL, detect_country
from database import TS_FORMAT, Database
from alerts import detect_alerts, build_email_html, market_url, parse_recipients
from polymarket_api import PolymarketClient, _unwrap_list
from utils import parse_json_field, alert_hour_utc

# Formato REAL da Gamma API: array no topo, listas stringificadas.
GAMMA_MARKET = {
    "id": "512329",
    "question": "Will the Fed cut rates in September?",
    "conditionId": "0xabc123",
    "slug": "fed-cut-september",
    "outcomes": '["Yes", "No"]',
    "outcomePrices": '["0.6350", "0.3650"]',
    "clobTokenIds": '["111111", "222222"]',
    "volumeNum": 2500000.5,
    "endDate": "2026-09-30T00:00:00Z",
    "events": [{"slug": "fed-decision-september"}],
}


def ts(dt):
    return dt.strftime(TS_FORMAT)


def make_market(market_id, price, category="economy", question=None):
    return {
        "market_id": market_id, "question": question or f"Market {market_id}",
        "category": category, "slug": f"slug-{market_id}", "event_slug": "",
        "outcome": "Yes", "price": price, "token_id": "", "volume": 1000.0,
        "end_date": "",
    }


class TestParsing(unittest.TestCase):
    def test_parse_stringified_json_field(self):
        self.assertEqual(parse_json_field('["Yes", "No"]'), ["Yes", "No"])
        self.assertEqual(parse_json_field(["a"]), ["a"])
        self.assertEqual(parse_json_field(None), [])
        self.assertEqual(parse_json_field("not json"), [])

    def test_unwrap_list_accepts_array_and_wrapped(self):
        self.assertEqual(_unwrap_list([1, 2], "markets"), [1, 2])
        self.assertEqual(_unwrap_list({"markets": [1]}, "markets"), [1])
        self.assertEqual(_unwrap_list({"data": [2]}, "markets"), [2])
        self.assertEqual(_unwrap_list(None, "markets"), [])

    def test_normalize_market_yes_price(self):
        m = PolymarketClient._normalize_market(GAMMA_MARKET, "economy")
        self.assertEqual(m["market_id"], "512329")
        self.assertAlmostEqual(m["price"], 0.635)
        self.assertEqual(m["token_id"], "111111")
        self.assertEqual(m["category"], "economy")
        self.assertEqual(m["event_slug"], "fed-decision-september")

    def test_normalize_market_no_price_returns_none(self):
        raw = dict(GAMMA_MARKET)
        raw["outcomePrices"] = None
        self.assertIsNone(PolymarketClient._normalize_market(raw, "economy"))

    def test_normalize_picks_yes_outcome_index(self):
        raw = dict(GAMMA_MARKET)
        raw["outcomes"] = '["No", "Yes"]'
        raw["outcomePrices"] = '["0.40", "0.60"]'
        m = PolymarketClient._normalize_market(raw, "economy")
        self.assertAlmostEqual(m["price"], 0.60)
        self.assertEqual(m["token_id"], "222222")

    def test_alert_hour_parsing(self):
        self.assertEqual(alert_hour_utc({"alert_run": "23:00"}), 23)
        self.assertEqual(alert_hour_utc({"alert_run": "bogus"}), 23)


class TestCountryDetection(unittest.TestCase):
    def test_us_by_institution_and_leader(self):
        self.assertIn("Estados Unidos", detect_country(
            "Will the Fed cut rates in September?"))
        self.assertIn("Estados Unidos", detect_country(
            "Will Trump sign the bill?"))
        self.assertIn("Estados Unidos", detect_country(
            "Will Oprah win the 2028 Democratic presidential nomination?"))

    def test_case_sensitive_acronym_no_false_positive(self):
        # "us" minúsculo (pronome) não pode virar Estados Unidos
        self.assertEqual(detect_country("Will they let us know the result?"),
                         OTHER_LABEL)

    def test_alt_spellings(self):
        self.assertIn("Ucrânia", detect_country(
            "Will Volodymyr Zelenskyy be the next leader out?"))
        self.assertIn("Turquia", detect_country(
            "Will Recep Tayyip Erdoğan win the Nobel Peace Prize?"))

    def test_priority_specific_before_us(self):
        # conflito citando dois países -> vence o mais específico da lista
        self.assertIn("Irã", detect_country("Will the US strike Iran in 2026?"))

    def test_crypto_and_unknown(self):
        self.assertIn("Cripto", detect_country("Bitcoin above $150k by Dec 31?"))
        self.assertEqual(detect_country("Will it rain tomorrow?"), OTHER_LABEL)


class TestDatabaseAndAlerts(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.now = datetime(2026, 7, 9, 23, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.db.close()

    def test_snapshot_roundtrip_and_history(self):
        self.db.store_snapshot([make_market("m1", 0.50)], ts(self.now))
        self.db.store_snapshot([make_market("m1", 0.55)],
                               ts(self.now + timedelta(hours=6)))
        self.assertEqual(self.db.latest_ts(), ts(self.now + timedelta(hours=6)))
        history = self.db.get_history("m1", days=90)
        self.assertEqual([p for _, p in history], [0.50, 0.55])

    def test_alert_fires_above_threshold_pp(self):
        # ontem 50% -> hoje 54% = +4 pp > 3 pp: alerta
        self.db.store_snapshot([make_market("m1", 0.50), make_market("m2", 0.30)],
                               ts(self.now - timedelta(hours=24)))
        self.db.store_snapshot([make_market("m1", 0.54), make_market("m2", 0.31)],
                               ts(self.now))
        alerts = detect_alerts(self.db, threshold_pp=3.0)
        self.assertIn("economy", alerts)
        self.assertEqual(len(alerts["economy"]), 1)
        alert = alerts["economy"][0]
        self.assertEqual(alert["market_id"], "m1")
        self.assertAlmostEqual(alert["delta_pp"], 4.0)

    def test_exactly_threshold_does_not_fire(self):
        # 50% -> 53% = 3.0 pp; spec: alerta apenas se delta > 3
        self.db.store_snapshot([make_market("m1", 0.50)],
                               ts(self.now - timedelta(hours=24)))
        self.db.store_snapshot([make_market("m1", 0.53)], ts(self.now))
        self.assertEqual(detect_alerts(self.db, threshold_pp=3.0), {})

    def test_reference_snapshot_tolerates_cron_delay(self):
        # rodada de "ontem" às 23:07 (cron atrasado) ainda serve de referência
        self.db.store_snapshot([make_market("m1", 0.50)],
                               ts(self.now - timedelta(hours=23, minutes=53)))
        self.db.store_snapshot([make_market("m1", 0.60)], ts(self.now))
        alerts = detect_alerts(self.db, threshold_pp=3.0)
        self.assertEqual(len(alerts["economy"]), 1)

    def test_no_reference_no_alerts(self):
        # primeiro dia de coleta: sem rodada de ontem, sem alerta (e sem crash)
        self.db.store_snapshot([make_market("m1", 0.50)], ts(self.now))
        self.assertEqual(detect_alerts(self.db, threshold_pp=3.0), {})

    def test_prune_removes_old_snapshots(self):
        old = self.now - timedelta(days=120)
        self.db.store_snapshot([make_market("m_old", 0.5)], ts(old))
        self.db.store_snapshot([make_market("m_new", 0.5)], ts(self.now))
        removed = self.db.prune(days=90)
        self.assertEqual(removed, 1)
        self.assertEqual(self.db.get_history("m_old", days=365), [])

    def test_email_html_contains_alerts(self):
        self.db.store_snapshot([make_market("m1", 0.50)],
                               ts(self.now - timedelta(hours=24)))
        self.db.store_snapshot([make_market("m1", 0.58)], ts(self.now))
        alerts = detect_alerts(self.db, threshold_pp=3.0)
        html = build_email_html(alerts, self.db.latest_ts(), 1,
                                "https://x.streamlit.app")
        self.assertIn("Market m1", html)
        self.assertIn("+8.0 pp", html)
        self.assertIn("ECONOMY", html)
        self.assertIn("x.streamlit.app", html)

    def test_parse_recipients_comma_and_semicolon(self):
        self.assertEqual(
            parse_recipients("a@x.com; b@x.com , c@x.com;"),
            ["a@x.com", "b@x.com", "c@x.com"])
        self.assertEqual(parse_recipients(""), [])
        self.assertEqual(parse_recipients(None), [])

    def test_market_url_prefers_event_slug(self):
        self.assertEqual(market_url({"event_slug": "ev", "slug": "mk"}),
                         "https://polymarket.com/event/ev")
        self.assertEqual(market_url({"event_slug": "", "slug": "mk"}),
                         "https://polymarket.com/market/mk")


if __name__ == "__main__":
    unittest.main()
