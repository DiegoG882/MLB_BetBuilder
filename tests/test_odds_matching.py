"""
Tests para odds_data.match_game_odds: confirma que cuando los mismos dos
equipos tienen mas de un evento en la respuesta de The Odds API (serie de
varios juegos entre ellos), se elige el que empieza mas cerca de la hora
real del juego -- y no el primero que aparezca en la lista.

Este era el bug real: sin filtrar por hora, se podia agarrar la cuota de
OTRO juego de la serie, lo que generaba "edges" absurdos (30-70%) contra la
proyeccion del dia.
"""

import unittest

from src import odds_data


def _event(home, away, commence_time):
    return {"home_team": home, "away_team": away, "commence_time": commence_time, "bookmakers": []}


class TestMatchGameOdds(unittest.TestCase):
    def test_single_candidate_no_time_needed(self):
        events = [_event("Cleveland Guardians", "New York Mets", "2026-08-04T22:40:00Z")]
        result = odds_data.match_game_odds(events, "Cleveland Guardians", "New York Mets")
        self.assertIsNotNone(result)

    def test_picks_closest_game_in_a_series(self):
        # serie de 3 juegos entre los mismos equipos en dias distintos
        events = [
            _event("Cleveland Guardians", "New York Mets", "2026-08-03T22:40:00Z"),
            _event("Cleveland Guardians", "New York Mets", "2026-08-04T22:40:00Z"),
            _event("Cleveland Guardians", "New York Mets", "2026-08-05T22:40:00Z"),
        ]
        # el juego real de hoy empieza a esta hora
        result = odds_data.match_game_odds(
            events, "Cleveland Guardians", "New York Mets",
            start_time_utc="2026-08-04T22:35:00Z",
        )
        self.assertEqual(result["commence_time"], "2026-08-04T22:40:00Z")

    def test_no_candidate_close_enough_returns_none(self):
        events = [_event("Cleveland Guardians", "New York Mets", "2026-08-10T22:40:00Z")]
        result = odds_data.match_game_odds(
            events, "Cleveland Guardians", "New York Mets",
            start_time_utc="2026-08-04T22:35:00Z",
        )
        self.assertIsNone(result)

    def test_no_match_by_name_returns_none(self):
        events = [_event("Boston Red Sox", "Chicago White Sox", "2026-08-04T22:40:00Z")]
        result = odds_data.match_game_odds(events, "Cleveland Guardians", "New York Mets")
        self.assertIsNone(result)

    def test_missing_start_time_falls_back_to_first(self):
        events = [
            _event("Cleveland Guardians", "New York Mets", "2026-08-03T22:40:00Z"),
            _event("Cleveland Guardians", "New York Mets", "2026-08-05T22:40:00Z"),
        ]
        result = odds_data.match_game_odds(events, "Cleveland Guardians", "New York Mets")
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
