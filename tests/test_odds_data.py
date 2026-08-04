"""
Tests para odds_data.py: conversion de cuotas americanas a probabilidad
implicita, y el de-vig de mercados de dos resultados.
"""

import unittest

from src import odds_data


class TestImpliedProb(unittest.TestCase):
    def test_negative_price(self):
        self.assertAlmostEqual(odds_data.american_to_implied_prob(-150), 150 / 250)

    def test_positive_price(self):
        self.assertAlmostEqual(odds_data.american_to_implied_prob(150), 100 / 250)

    def test_none_price(self):
        self.assertIsNone(odds_data.american_to_implied_prob(None))


class TestRemoveVig(unittest.TestCase):
    def test_devig_sums_to_one(self):
        # -110 / -110 es un mercado clasico con ~4.5% de vig
        prob_a = odds_data.american_to_implied_prob(-110)
        prob_b = odds_data.american_to_implied_prob(-110)
        fair_a, fair_b = odds_data.remove_vig_two_way(prob_a, prob_b)
        self.assertAlmostEqual(fair_a + fair_b, 1.0, places=6)
        self.assertAlmostEqual(fair_a, 0.5, places=6)

    def test_devig_preserves_relative_edge(self):
        prob_a = odds_data.american_to_implied_prob(-200)  # favorito
        prob_b = odds_data.american_to_implied_prob(170)   # underdog
        fair_a, fair_b = odds_data.remove_vig_two_way(prob_a, prob_b)
        self.assertGreater(fair_a, fair_b)
        self.assertAlmostEqual(fair_a + fair_b, 1.0, places=6)

    def test_missing_data_returns_none(self):
        fair_a, fair_b = odds_data.remove_vig_two_way(None, 0.5)
        self.assertIsNone(fair_a)
        self.assertIsNone(fair_b)


if __name__ == "__main__":
    unittest.main()
