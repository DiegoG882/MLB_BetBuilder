"""
Tests para settle.py: determinar si un pick gano/perdio dado un resultado
final, y la expiracion de picks que se quedaron 'pending' demasiado tiempo.
"""

import unittest
from datetime import datetime, timedelta

from src import settle


class TestDidPickWin(unittest.TestCase):
    def test_moneyline_home_win(self):
        entry = {"market_type": "moneyline", "extra": {"side": "home"}}
        result = {"home_runs": 5, "away_runs": 3}
        self.assertTrue(settle._did_pick_win(entry, result))

    def test_moneyline_home_loss(self):
        entry = {"market_type": "moneyline", "extra": {"side": "home"}}
        result = {"home_runs": 2, "away_runs": 3}
        self.assertFalse(settle._did_pick_win(entry, result))

    def test_moneyline_away_win(self):
        entry = {"market_type": "moneyline", "extra": {"side": "away"}}
        result = {"home_runs": 2, "away_runs": 3}
        self.assertTrue(settle._did_pick_win(entry, result))

    def test_total_over_win(self):
        entry = {"market_type": "total_over", "extra": {"line": 8.5}}
        result = {"home_runs": 5, "away_runs": 4}  # total 9
        self.assertTrue(settle._did_pick_win(entry, result))

    def test_total_under_win(self):
        entry = {"market_type": "total_under", "extra": {"line": 8.5}}
        result = {"home_runs": 3, "away_runs": 4}  # total 7
        self.assertTrue(settle._did_pick_win(entry, result))

    def test_missing_line_returns_none(self):
        entry = {"market_type": "total_over", "extra": {}}
        result = {"home_runs": 5, "away_runs": 4}
        self.assertIsNone(settle._did_pick_win(entry, result))


class TestStaleExpiry(unittest.TestCase):
    def test_recent_pick_not_stale(self):
        today = datetime(2026, 8, 10)
        entry = {"date": "2026-08-09"}
        self.assertFalse(settle._is_stale(entry, today))

    def test_old_pick_is_stale(self):
        today = datetime(2026, 8, 10)
        entry = {"date": "2026-08-01"}
        self.assertTrue(settle._is_stale(entry, today))

    def test_exactly_at_threshold_not_stale(self):
        today = datetime(2026, 8, 10)
        entry = {"date": (today - timedelta(days=settle.STALE_PENDING_DAYS)).strftime("%Y-%m-%d")}
        self.assertFalse(settle._is_stale(entry, today))


if __name__ == "__main__":
    unittest.main()
