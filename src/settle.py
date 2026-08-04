"""
settle.py
Antes de generar los picks del dia, revisa los picks 'pending' de dias
anteriores cuyo juego ya termino, determina si ganaron o perdieron, y
actualiza calibration.json. Esto es lo que hace que el sistema "aprenda":
manana el modelo ya sabe que tan bien calibrado estuvo hoy.

Tambien limpia picks que se quedaron 'pending' demasiado tiempo (juego
suspendido/pospuesto, o la API de resultados nunca respondio) para que no
se acumulen para siempre esperando un resultado que quiza nunca llega.
"""

from datetime import datetime

from . import mlb_data
from . import storage

STALE_PENDING_DAYS = storage.STALE_PENDING_DAYS


def settle_pending_picks(history, calibration, today_str=None):
    updated_count = 0
    expired_count = 0
    today = datetime.strptime(today_str, "%Y-%m-%d") if today_str else datetime.now()

    for entry in history:
        if entry["status"] != "pending":
            continue

        final_score = mlb_data.get_final_score(entry["game_pk"])
        if not final_score:
            if _is_stale(entry, today):
                entry["status"] = "no_data"
                expired_count += 1
            continue  # el juego todavia no termina o no hay datos, lo dejamos pending

        won = _did_pick_win(entry, final_score)
        if won is None:
            entry["status"] = "no_data"
            continue

        entry["status"] = "win" if won else "loss"
        entry["result"] = final_score
        storage.update_calibration(calibration, entry["market_type"], entry["model_prob"], won)
        updated_count += 1

    if expired_count:
        print(f"[settle] {expired_count} pick(s) marcados 'no_data' por quedarse pending mas de {STALE_PENDING_DAYS} dias.")

    return updated_count


def _is_stale(entry, today):
    try:
        pick_date = datetime.strptime(entry["date"], "%Y-%m-%d")
    except (KeyError, ValueError):
        return False
    return (today - pick_date).days > STALE_PENDING_DAYS


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
