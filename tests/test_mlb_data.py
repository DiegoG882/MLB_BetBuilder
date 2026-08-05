"""
Tests para mlb_data.get_final_score: confirma que SOLO regresa marcador
cuando el juego ya termino (abstractGameState == 'Final'), y no cuando
esta en vivo, pospuesto o no encontrado. Este es el bug real que causo que
el sistema settleara picks con marcadores de juegos a la mitad y corrompiera
la calibracion -- ver el comentario en mlb_data.py para el detalle.
"""

import unittest
from unittest.mock import patch

from src import mlb_data


def _schedule_response(game_pk, state, home_score=None, away_score=None):
    return {
        "dates": [
            {
                "games": [
                    {
                        "gamePk": game_pk,
                        "status": {"abstractGameState": state},
                        "teams": {
                            "home": {"score": home_score},
                            "away": {"score": away_score},
                        },
                    }
                ]
            }
        ]
    }


class TestGetFinalScore(unittest.TestCase):
    @patch("src.mlb_data._get")
    def test_final_game_returns_score(self, mock_get):
        mock_get.return_value = _schedule_response(123, "Final", home_score=5, away_score=3)
        result = mlb_data.get_final_score(123)
        self.assertEqual(result, {"home_runs": 5, "away_runs": 3})

    @patch("src.mlb_data._get")
    def test_in_progress_game_returns_none(self, mock_get):
        # este es el caso que causaba el bug: el juego va a la mitad y ya
        # tiene carreras, pero NO es el resultado final
        mock_get.return_value = _schedule_response(123, "Live", home_score=2, away_score=1)
        result = mlb_data.get_final_score(123)
        self.assertIsNone(result)

    @patch("src.mlb_data._get")
    def test_preview_game_returns_none(self, mock_get):
        mock_get.return_value = _schedule_response(123, "Preview")
        result = mlb_data.get_final_score(123)
        self.assertIsNone(result)

    @patch("src.mlb_data._get")
    def test_game_not_found_returns_none(self, mock_get):
        mock_get.return_value = {"dates": []}
        result = mlb_data.get_final_score(999)
        self.assertIsNone(result)

    @patch("src.mlb_data._get")
    def test_empty_response_returns_none(self, mock_get):
        mock_get.return_value = {}
        result = mlb_data.get_final_score(123)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
