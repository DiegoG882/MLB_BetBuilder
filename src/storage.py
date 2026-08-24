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

import csv
import json
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
HISTORY_PATH = os.path.join(DATA_DIR, "picks_history.json")
CALIBRATION_PATH = os.path.join(DATA_DIR, "calibration.json")
BANKROLL_PATH = os.path.join(DATA_DIR, "bankroll.json")
PICKS_CSV_PATH = os.path.join(DATA_DIR, "picks_history.csv")
SUMMARY_CSV_PATH = os.path.join(DATA_DIR, "performance_summary.csv")

CSV_FIELDS = [
    "date", "matchup", "market_type", "selection", "model_prob",
    "implied_prob", "edge", "risk", "model_version", "status", "result",
    "projected_total", "actual_total", "stake_pct", "stake_amount",
]

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
            "matchup": pick.get("matchup", ""),
            "market_type": pick["market_type"],
            "selection": pick["selection"],
            "model_prob": pick["model_prob"],
            "implied_prob": pick["implied_prob"],
            "risk": pick["risk"],
            "model_version": pick.get("model_version", "sin_version"),
            "extra": pick["extra"],
            "stake_pct": pick.get("stake_pct") or None,
            "stake_amount": pick.get("stake_amount"),
            "status": "pending",   # pending | win | loss | push | no_data
            "result": None,
        }
    )


def upsert_pick(history, pick, game_pk, date_str):
    """Como record_pick, pero idempotente: si ya existe un pick para el
    mismo dia + juego + tipo de mercado, lo ACTUALIZA en vez de duplicarlo.
    Esto es lo que permite correr el workflow manualmente mas de una vez el
    mismo dia (para pruebas, por ejemplo) sin ensuciar el historial.

    Si el pick existente YA se resolvio (win/loss/no_data), no lo tocamos --
    nunca queremos pisar un resultado ya conocido con una repeticion que
    todavia dice pending."""
    key = (date_str, game_pk, pick["market_type"])
    for entry in history:
        if (entry.get("date"), entry.get("game_pk"), entry.get("market_type")) == key:
            if entry.get("status") != "pending":
                return  # ya resuelto, no lo tocamos
            entry.update({
                "matchup": pick.get("matchup", ""),
                "selection": pick["selection"],
                "model_prob": pick["model_prob"],
                "implied_prob": pick["implied_prob"],
                "risk": pick["risk"],
                "model_version": pick.get("model_version", "sin_version"),
                "extra": pick["extra"],
                "stake_pct": pick.get("stake_pct") or None,
                "stake_amount": pick.get("stake_amount"),
            })
            return
    record_pick(history, pick, game_pk, date_str)


def dedupe_history(history):
    """Colapsa entradas duplicadas (mismo dia + juego + tipo de mercado --
    pasa cuando el workflow se corrio manualmente mas de una vez el mismo
    dia, antes de que existiera upsert_pick). Para cada grupo de
    duplicados: si alguna version ya esta resuelta (win/loss/no_data), esa
    gana sobre cualquier pending; si todas siguen pending, gana la mas
    reciente en la lista. Regresa (historial_limpio, cuantos_se_quitaron)."""
    best_by_key = {}
    order = []
    for entry in history:
        key = (entry.get("date"), entry.get("game_pk"), entry.get("market_type"))
        if key not in best_by_key:
            best_by_key[key] = entry
            order.append(key)
            continue
        current = best_by_key[key]
        if current.get("status") == "pending":
            best_by_key[key] = entry  # resuelto gana sobre pending, o mas reciente entre dos pending
        # si el actual ya estaba resuelto, se queda -- no lo pisamos

    deduped = [best_by_key[k] for k in order]
    removed = len(history) - len(deduped)
    return deduped, removed

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


# ---------- Exportacion a CSV (para verlo en Excel/Sheets, o directo en GitHub) ----------

def export_history_csv(history):
    """Escribe picks_history.json como CSV plano: una fila por pick, con su
    resultado. Se regenera completo cada corrida (el archivo es chico, no
    importa el costo). Se puede abrir en Excel/Google Sheets, o verlo
    directo como tabla en la pagina de GitHub sin descargar nada."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PICKS_CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for entry in history:
            model_prob = entry.get("model_prob")
            implied_prob = entry.get("implied_prob")
            edge = None
            if model_prob is not None and implied_prob is not None:
                edge = round(model_prob - implied_prob, 4)

            result = entry.get("result") or {}
            result_str = f"{result.get('away_runs')}-{result.get('home_runs')}" if result else ""
            actual_total = None
            if result:
                home_r = result.get("home_runs")
                away_r = result.get("away_runs")
                if home_r is not None and away_r is not None:
                    actual_total = home_r + away_r

            # solo aplica a picks de totales (moneyline no proyecta un total)
            projected_total = (entry.get("extra") or {}).get("projected_total")

            writer.writerow({
                "date": entry.get("date"),
                "matchup": entry.get("matchup", ""),
                "market_type": entry.get("market_type"),
                "selection": entry.get("selection"),
                "model_prob": model_prob,
                "implied_prob": implied_prob,
                "edge": edge,
                "risk": entry.get("risk"),
                "model_version": entry.get("model_version", "sin_version"),
                "status": entry.get("status"),
                "result": result_str,
                "projected_total": projected_total,
                "actual_total": actual_total,
                "stake_pct": entry.get("stake_pct"),
                "stake_amount": entry.get("stake_amount"),
            })

def export_performance_summary_csv(history):
    """Resumen de aciertos por VERSION del modelo + tipo de mercado, mas un
    renglon TOTAL por version. Desglosarlo por version es lo que permite
    comparar "v4 contra lo que hubo antes" sin que un cambio nuevo quede
    escondido dentro del promedio general de todo el historial."""
    stats = {}
    for entry in history:
        status = entry.get("status")
        if status not in ("win", "loss"):
            continue  # solo contamos picks ya resueltos, no pending/no_data
        version = entry.get("model_version") or "sin_version"
        market = entry.get("market_type", "desconocido")
        bucket = stats.setdefault((version, market), {"wins": 0, "losses": 0})
        bucket["wins" if status == "win" else "losses"] += 1

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SUMMARY_CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["model_version", "market_type", "wins", "losses", "total", "win_rate_pct"])

        totals_by_version = {}
        for (version, market), s in sorted(stats.items()):
            total = s["wins"] + s["losses"]
            win_rate = round(100 * s["wins"] / total, 1) if total else 0.0
            writer.writerow([version, market, s["wins"], s["losses"], total, win_rate])
            tv = totals_by_version.setdefault(version, {"wins": 0, "losses": 0})
            tv["wins"] += s["wins"]
            tv["losses"] += s["losses"]

        for version, tv in sorted(totals_by_version.items()):
            total = tv["wins"] + tv["losses"]
            win_rate = round(100 * tv["wins"] / total, 1) if total else 0.0
            writer.writerow([version, "TOTAL", tv["wins"], tv["losses"], total, win_rate])

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
