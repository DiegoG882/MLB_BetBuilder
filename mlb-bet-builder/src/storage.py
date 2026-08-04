"""
storage.py
Persistencia simple en JSON (sin base de datos, para que sea facil de mover
entre GitHub Actions y la Raspberry el dia de manana).

picks_history.json  -> cada pick que se mando, con su resultado despues de
                        settle.py (pending / win / loss).
calibration.json    -> por market_type + bucket de probabilidad: cuantas
                        veces se predijo ese rango y cuantas realmente
                        acerto. Esto es lo que hace que el modelo "aprenda".
"""

import json
import os

HISTORY_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "picks_history.json")
CALIBRATION_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "calibration.json")


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_history():
    return load_json(HISTORY_PATH, [])


def save_history(history):
    save_json(HISTORY_PATH, history)


def load_calibration():
    return load_json(CALIBRATION_PATH, {})


def save_calibration(calibration):
    save_json(CALIBRATION_PATH, calibration)


def record_pick(history, pick, game_pk, date_str):
    """Agrega un pick nuevo al historial con status pending."""
    history.append(
        {
            "date": date_str,
            "game_pk": game_pk,
            "market_type": pick["market_type"],
            "selection": pick["selection"],
            "model_prob": pick["model_prob"],
            "implied_prob": pick["implied_prob"],
            "risk": pick["risk"],
            "extra": pick["extra"],
            "status": "pending",   # pending | win | loss | push | no_data
            "result": None,
        }
    )


def update_calibration(calibration, market_type, model_prob, won):
    """won = True/False. Acumula por bucket para poder calcular la tasa real
    de acierto vs la probabilidad que predijo el modelo (ver model.py
    apply_calibration)."""
    pct = int(model_prob * 100)
    lower = (pct // 10) * 10
    bucket = f"{lower}-{lower+10}"
    key = f"{market_type}:{bucket}"

    entry = calibration.setdefault(key, {"n": 0, "hits": 0, "predicted_sum": 0.0})
    entry["n"] += 1
    entry["hits"] += 1 if won else 0
    entry["predicted_sum"] += model_prob
