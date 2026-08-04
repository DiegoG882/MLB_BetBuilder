"""
storage.py
Persistencia simple en JSON (sin base de datos, para que sea facil de mover
entre GitHub Actions y la Raspberry el dia de manana).

picks_history.json  -> cada pick que se mando, con su resultado despues de
                        settle.py (pending / win / loss / push / no_data).
calibration.json    -> por market_type + bucket de probabilidad: cuantas
                        veces se predijo ese rango y cuantas realmente
                        acerto. Esto es lo que hace que el modelo "aprenda".
bankroll.json        -> tu bankroll actual, para poder sugerir cuanto
                        apostar por pick (ver model.suggested_stake). Se
                        actualiza a mano (o con el helper set_bankroll.py),
                        el sistema nunca lo cambia solo.
"""

import json
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
HISTORY_PATH = os.path.join(DATA_DIR, "picks_history.json")
CALIBRATION_PATH = os.path.join(DATA_DIR, "calibration.json")
BANKROLL_PATH = os.path.join(DATA_DIR, "bankroll.json")

# Si un pick queda 'pending' mas de este numero de dias (el juego se
# suspendio, se pospuso, o el endpoint de resultado nunca respondio), lo
# marcamos 'no_data' en vez de dejarlo pending para siempre.
STALE_PENDING_DAYS = 3


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


# ---------- Bankroll ----------

def load_bankroll(default_from_env=None):
    """Carga el bankroll actual. Prioridad:
    1. data/bankroll.json (si existe y tiene un valor > 0) -- esto es lo que
       actualizas con `python -m src.set_bankroll <monto>` cuando ganas,
       pierdes o depositas mas.
    2. La variable de entorno BANKROLL_USD (util para el primer setup o para
       correrlo en GitHub Actions via secret, aunque ahi no persiste entre
       corridas a menos que tambien commitees bankroll.json).
    3. None si no hay bankroll configurado en ningun lado (el sistema sigue
       funcionando, simplemente no sugiere montos)."""
    data = load_json(BANKROLL_PATH, {})
    value = data.get("bankroll_usd")
    if value and value > 0:
        return float(value)
    if default_from_env:
        try:
            env_value = float(default_from_env)
            if env_value > 0:
                return env_value
        except (TypeError, ValueError):
            pass
    return None


def save_bankroll(amount):
    save_json(BANKROLL_PATH, {"bankroll_usd": round(float(amount), 2)})


def adjust_bankroll(delta):
    """Suma (o resta, si delta es negativo) al bankroll guardado. Util para
    ir ajustando el bankroll manualmente segun tus resultados reales fuera
    del sistema (depositos, retiros, resultados que no calzan exacto con
    el stake sugerido)."""
    current = load_bankroll() or 0.0
    new_value = max(0.0, current + delta)
    save_bankroll(new_value)
    return new_value
