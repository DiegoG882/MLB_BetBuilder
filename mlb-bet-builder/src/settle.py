"""
settle.py
Antes de generar los picks del dia, revisa los picks 'pending' de dias
anteriores cuyo juego ya termino, determina si ganaron o perdieron, y
actualiza calibration.json. Esto es lo que hace que el sistema "aprenda":
manana el modelo ya sabe que tan bien calibrado estuvo hoy.
"""

from . import mlb_data
from . import model as model_module
from . import storage


def settle_pending_picks(history, calibration):
    updated_count = 0
    for entry in history:
        if entry["status"] != "pending":
            continue

        final_score = mlb_data.get_final_score(entry["game_pk"])
        if not final_score:
            continue  # el juego todavia no termina o no hay datos, lo dejamos pending

        won = _did_pick_win(entry, final_score)
        if won is None:
            entry["status"] = "no_data"
            continue

        entry["status"] = "win" if won else "loss"
        entry["result"] = final_score
        storage.update_calibration(calibration, entry["market_type"], entry["model_prob"], won)
        updated_count += 1

    return updated_count


def _did_pick_win(entry, final_score):
    market = entry["market_type"]
    extra = entry.get("extra", {})
    home_runs = final_score["home_runs"]
    away_runs = final_score["away_runs"]
    total_runs = home_runs + away_runs

    if market == "moneyline":
        picked_side = extra.get("side")  # 'home' o 'away'
        if picked_side == "home":
            return home_runs > away_runs
        elif picked_side == "away":
            return away_runs > home_runs
        return None

    if market in ("total_over", "total_under"):
        line = extra.get("line")
        if line is None:
            return None
        if market == "total_over":
            return total_runs > line
        else:
            return total_runs < line

    return None
