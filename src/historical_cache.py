"""
historical_cache.py
Mantiene un archivo local (data/game_history_cache.json) con los partidos
terminados de los ultimos N dias (100 por default) de TODA la liga, para
usarlos como referencia al calcular la "forma reciente" de cada equipo.

Por que un cache en vez de pedir la API cada vez:
  - Antes, get_team_recent_form pedia el calendario de cada equipo por
    separado (~30 llamadas/dia, una por cada equipo que juega hoy).
  - Con el cache, una corrida normal solo pide los dias que le faltan desde
    la ultima vez que corrio (normalmente 1 dia), y el resto sale del
    archivo local -- mucho mas rapido y con menos riesgo de que la API
    truene a medio calculo.
  - Ademas, tener 100 dias de referencia (en vez de 30) hace que la "forma
    reciente" no se quede sin datos en situaciones raras: inicio de
    temporada, equipo que tuvo muchos dias libres/lluvia, etc.

El cache se guarda y se sube al repo igual que picks_history.json y
calibration.json (ver .github/workflows/daily.yml).
"""

from datetime import datetime, timedelta

from . import mlb_data
from . import storage

CACHE_PATH = storage.DATA_DIR + "/game_history_cache.json"
DEFAULT_WINDOW_DAYS = 100

# La API a veces se pone lenta o falla con rangos muy largos de una sola
# vez; pedimos en pedazos de este tamano para que un fallo parcial no tire
# toda la actualizacion del cache.
CHUNK_DAYS = 20


def load_cache():
    return storage.load_json(CACHE_PATH, [])


def save_cache(games):
    storage.save_json(CACHE_PATH, games)


def update_cache(as_of_date_str, window_days=DEFAULT_WINDOW_DAYS):
    """Trae los juegos que le falten al cache para cubrir los ultimos
    `window_days` dias antes de `as_of_date_str`, los fusiona con lo que ya
    habia, poda lo que quedo fuera de la ventana, y guarda. Regresa la
    lista actualizada (para usarla de inmediato en la misma corrida sin
    tener que releer el archivo)."""
    as_of = datetime.strptime(as_of_date_str, "%Y-%m-%d")
    window_start = as_of - timedelta(days=window_days)

    games = load_cache()
    games_by_pk = {g["game_pk"]: g for g in games if "game_pk" in g}

    existing_dates = {g["date"] for g in games if "date" in g}
    latest_cached = max(existing_dates) if existing_dates else None

    if latest_cached is None:
        # cache vacio (primera corrida): traer la ventana completa
        fetch_start = window_start
    else:
        latest_dt = datetime.strptime(latest_cached, "%Y-%m-%d")
        # si el cache ya esta al dia (o mas), solo falta el dia de ayer/hoy
        fetch_start = max(latest_dt + timedelta(days=1), window_start)

    fetch_end = as_of - timedelta(days=1)  # no pedimos "hoy": todavia no termina

    if fetch_start <= fetch_end:
        new_games = _fetch_range_chunked(fetch_start, fetch_end)
        for g in new_games:
            games_by_pk[g["game_pk"]] = g
        print(f"[historical_cache] {len(new_games)} juego(s) nuevo(s) agregados al cache "
              f"({fetch_start.strftime('%Y-%m-%d')} a {fetch_end.strftime('%Y-%m-%d')}).")
    else:
        print("[historical_cache] Cache ya estaba al dia, no se pidio nada nuevo.")

    # podar lo que ya quedo fuera de la ventana de referencia
    window_start_str = window_start.strftime("%Y-%m-%d")
    merged = [g for g in games_by_pk.values() if g.get("date", "") >= window_start_str]
    merged.sort(key=lambda g: g["date"])

    save_cache(merged)
    return merged


def _fetch_range_chunked(start_dt, end_dt):
    all_games = []
    chunk_start = start_dt
    while chunk_start <= end_dt:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), end_dt)
        all_games.extend(
            mlb_data.get_final_games_range(
                chunk_start.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")
            )
        )
        chunk_start = chunk_end + timedelta(days=1)
    return all_games


def get_recent_form(games_cache, team_id, as_of_date_str, n_games=10):
    """Mismo resultado que mlb_data.get_team_recent_form (promedio de
    carreras a favor/en contra de los ultimos n_games), pero leyendo del
    cache local en vez de llamar la API. Si el cache no tiene suficientes
    juegos de ese equipo (recien arrancando el cache, por ejemplo), regresa
    lo que haya -- nunca truena por falta de datos."""
    relevant = [
        g for g in games_cache
        if g["date"] < as_of_date_str and (g["home_team_id"] == team_id or g["away_team_id"] == team_id)
    ]
    relevant.sort(key=lambda g: g["date"], reverse=True)
    last_n = relevant[:n_games]

    if not last_n:
        return {"recent_runs_for_avg": None, "recent_runs_against_avg": None, "sample": 0}

    runs_for = []
    runs_against = []
    for g in last_n:
        if g["home_team_id"] == team_id:
            runs_for.append(g["home_runs"])
            runs_against.append(g["away_runs"])
        else:
            runs_for.append(g["away_runs"])
            runs_against.append(g["home_runs"])

    return {
        "recent_runs_for_avg": sum(runs_for) / len(runs_for),
        "recent_runs_against_avg": sum(runs_against) / len(runs_against),
        "sample": len(last_n),
    }
