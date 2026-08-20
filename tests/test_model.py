"""
Tests para las funciones puras de model.py: Poisson, calibracion, home field
advantage, y sizing de bankroll (Kelly fraccionado). Corre con:
    pytest
o, si no tienes pytest instalado:
    python -m unittest discover
"""

import math
import unittest

from src import model


class TestPoisson(unittest.TestCase):
    def test_over_prob_high_line_is_low(self):
        # con lam=4 carreras esperadas, es poco probable pasar de 12.5
        prob = model.poisson_over_prob(4.0, 12.5)
        self.assertLess(prob, 0.05)

    def test_over_prob_low_line_is_high(self):
        prob = model.poisson_over_prob(4.0, 0.5)
        self.assertGreater(prob, 0.9)

    def test_over_under_complement_to_one(self):
        lam, line = 4.5, 8.5
        over = model.poisson_over_prob(lam, line)
        # under = P(runs < line) = P(runs <= floor(line)) porque la linea es .5
        under = sum(model.poisson_pmf(k, lam) for k in range(0, math.floor(line) + 1))
        self.assertAlmostEqual(over + under, 1.0, places=6)


class TestProjectTeamRuns(unittest.TestCase):
    def setUp(self):
        self.season = {"runs_scored_per_game": 4.5}
        self.recent = {"recent_runs_for_avg": 5.0}
        self.avg_pitcher = {"era": 4.20}  # exactamente el promedio de liga

    def test_home_field_bonus_applied(self):
        away_proj = model.project_team_runs(self.season, self.recent, self.avg_pitcher, is_home=False)
        home_proj = model.project_team_runs(self.season, self.recent, self.avg_pitcher, is_home=True)
        self.assertAlmostEqual(home_proj - away_proj, model.HOME_FIELD_RUN_BONUS, places=2)

    def test_no_data_returns_none(self):
        result = model.project_team_runs({}, {}, None)
        self.assertIsNone(result)

    def test_tough_pitcher_lowers_projection(self):
        tough_pitcher = {"era": 2.0}  # mucho mejor que el promedio de liga
        baseline = model.project_team_runs(self.season, self.recent, self.avg_pitcher)
        vs_tough = model.project_team_runs(self.season, self.recent, tough_pitcher)
        self.assertLess(vs_tough, baseline)

    def test_bullpen_proxy_blends_with_starter(self):
        # abridor promedio de liga pero bullpen/staff completo mucho peor
        # que el promedio -> la proyeccion deberia subir vs. usar solo el
        # abridor.
        only_starter = model.project_team_runs(self.season, self.recent, self.avg_pitcher, opp_team_era=None)
        with_bad_staff = model.project_team_runs(
            self.season, self.recent, self.avg_pitcher, opp_team_era=6.0
        )
        self.assertGreater(with_bad_staff, only_starter)


class TestCalibration(unittest.TestCase):
    def test_no_adjustment_without_enough_samples(self):
        calibration = {"moneyline:60-70": {"n": 2, "hits": 2, "predicted_sum": 1.3}}
        adjusted = model.apply_calibration(0.65, "moneyline", calibration)
        self.assertEqual(adjusted, 0.65)

    def test_overconfident_model_gets_corrected_down(self):
        # el modelo predijo en promedio 75% en ese bucket pero solo acerto 60%
        calibration = {
            "moneyline:70-80": {"n": 10, "hits": 6, "predicted_sum": 7.5}
        }
        adjusted = model.apply_calibration(0.75, "moneyline", calibration)
        self.assertLess(adjusted, 0.75)

    def test_adjusted_prob_stays_in_bounds(self):
        calibration = {
            "moneyline:90-100": {"n": 10, "hits": 0, "predicted_sum": 9.5}
        }
        adjusted = model.apply_calibration(0.95, "moneyline", calibration)
        self.assertGreaterEqual(adjusted, 0.01)
        self.assertLessEqual(adjusted, 0.99)


class TestRiskLabel(unittest.TestCase):
    def test_moderate_edge_is_low_risk(self):
        self.assertEqual(model.risk_label(0.13, 10), "🟢 Riesgo bajo")

    def test_edge_above_sanity_cap_is_suspicious(self):
        # 546 picks reales mostraron que "Riesgo bajo" sin techo (edge >=12%
        # sin limite) acertaba MENOS que "Riesgo medio" -- porque mezclaba
        # ventaja real con edges gigantes que casi siempre son dato mal
        # calibrado. Por eso edge > EDGE_SANITY_CAP ya no es "bajo riesgo".
        self.assertEqual(model.risk_label(0.20, 10), "⚠️ Edge sospechoso")

    def test_small_edge_is_high_risk(self):
        self.assertEqual(model.risk_label(0.02, 10), "🔴 Riesgo alto")

    def test_small_sample_downgrades_confidence(self):
        # mismo edge, pero poca muestra -> penalizado, puede bajar de
        # categoria de riesgo
        high_sample = model.risk_label(0.10, 10)
        low_sample = model.risk_label(0.10, 2)
        order = ["🔴 Riesgo alto", "🟡 Riesgo medio", "🟢 Riesgo bajo"]
        self.assertLessEqual(order.index(low_sample), order.index(high_sample))

class TestKellySizing(unittest.TestCase):
    def test_decimal_odds_conversion(self):
        self.assertAlmostEqual(model.american_to_decimal_odds(150), 2.5)
        self.assertAlmostEqual(model.american_to_decimal_odds(-150), 1 + 100 / 150)

    def test_no_edge_no_stake(self):
        # probabilidad del modelo igual a la implicita de la cuota -> sin edge
        # cuota -110 implica ~52.4%
        pct = model.kelly_fraction(0.50, -110)
        self.assertEqual(pct, 0.0)

    def test_positive_edge_gives_positive_stake(self):
        # modelo dice 65% mientras que -110 implica ~52% -> hay edge real
        pct = model.kelly_fraction(0.65, -110)
        self.assertGreater(pct, 0.0)

    def test_stake_never_exceeds_cap(self):
        # edge exagerado a proposito para forzar que el cap entre en accion
        pct = model.kelly_fraction(0.95, 200, fraction=1.0)
        self.assertLessEqual(pct, model.MAX_STAKE_PCT_OF_BANKROLL)

    def test_suggested_stake_without_bankroll(self):
        pct, amount = model.suggested_stake(0.65, -110, bankroll=None)
        self.assertEqual(pct, 0.0)
        self.assertIsNone(amount)

    def test_suggested_stake_with_bankroll(self):
        pct, amount = model.suggested_stake(0.65, -110, bankroll=1000)
        self.assertGreater(pct, 0.0)
        self.assertGreater(amount, 0.0)
        self.assertLessEqual(amount, 1000 * model.MAX_STAKE_PCT_OF_BANKROLL)


if __name__ == "__main__":
    unittest.main()
