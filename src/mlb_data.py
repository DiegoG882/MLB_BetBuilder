"""
mlb_data.py
Todo lo que jala datos "de verdad" del juego: calendario, pitchers probables,
stats de equipo, stats de pitcher/bateador, forma reciente y resultados finales.

Usa la MLB Stats API oficial (statsapi.mlb.com) - es publica y gratuita, no
necesita API key. La documentacion no es oficial pero es muy usada por la
comunidad de analytics de beisbol.
"""

import time

import requests
from datetime import datetime, timedelta

BASE_URL = "https://statsapi.mlb.com/api/v1"
SPORT_ID = 1  # MLB

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 1.5


def _get(path, params=None):
    """Wrapper con manejo de errores y reintentos (backoff simple) para no
    tumbar todo el script si un endpoint falla temporalmente (timeout de
    red, hiccup del lado de MLB, etc.). Si tras varios intentos sigue
    fallando, regresa {} y quien llama debe tratarlo como "sin datos"."""
    url = f"{BASE_URL}{path}"
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params or {}, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            last_error = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    print(f"[mlb_data] WARNING: fallo {url} tras {MAX_RETRIES} intentos -> {last_error}")
    return {}


def get_schedule(date_str):
    """Regresa la lista de juegos programados para una fecha YYYY-MM-DD,
    incluyendo pitchers probables cuando ya estan anunciados."""
    data = _get(
        "/schedule",
        {
            "sportId": SPORT_ID,
            "date": date_str,
            "hydrate": "probablePitcher,team,linescore",
        },
    )
    games = []
    for date_block in data.get("dates", []):
        for g in date_block.get("games", []):
            if g.get("status", {}).get("abstractGameState") == "Final":
                continue
            home = g["teams"]["home"]
            away = g["teams"]["away"]
            games.append(
                {
                    "game_pk": g["gamePk"],
                    "date": date_str,
                    "start_time_utc": g.get("gameDate"),
                    "home_team_id": home["team"]["id"],
                    "home_team_name": home["team"]["name"],
                    "away_team_id": away["team"]["id"],
                    "away_team_name": away["team"]["name"],
                    "home_pitcher": _extract_pitcher(home),
                    "away_pitcher": _extract_pitcher(away),
                }
            )
    return games


def _extract_pitcher(team_block):
    p = team_block.get("probablePitcher")
    if not p:
        return None
    return {"id": p["id"], "name": p["fullName"]}


def get_team_season_stats(team_id, season):
    """Runs por juego a favor y en contra en la temporada, mas el ERA de
    equipo completo (abridores + bullpen), que usamos como proxy de "que tan
    bueno es el pitcheo completo del rival" mas alla del abridor del dia."""
    hitting = _get(
        f"/teams/{team_id}/stats",
        {"stats": "season", "group": "hitting", "season": season},
    )
    pitching = _get(
        f"/teams/{team_id}/stats",
        {"stats": "season", "group": "pitching", "season": season},
    )
    games = _first_stat(hitting, "gamesPlayed", default=None)
    runs_scored_total = _first_stat(hitting, "runs", default=None)
    runs_allowed_total = _first_stat(pitching, "runs", default=None)

    rs_pg = _safe_div(runs_scored_total, games)
    ra_pg = _safe_div(runs_allowed_total, games)
    team_era = _first_stat(pitching, "era", default=None)

    return {
        "runs_scored_per_game": rs_pg,
        "runs_allowed_per_game": ra_pg,
        "team_era": float(team_era) if team_era else None,
        "games_played": games,
    }


def get_team_recent_form(team_id, as_of_date, n_games=10):
    """Promedio de carreras anotadas/permitidas en los ultimos n_games."""
    end = datetime.strptime(as_of_date, "%Y-%m-%d")
    start = end - timedelta(days=30)
    data = _get(
        "/schedule",
        {
            "sportId": SPORT_ID,
            "teamId": team_id,
            "startDate": start.strftime("%Y-%m-%d"),
            "endDate": (end - timedelta(days=1)).strftime("%Y-%m-%d"),
            "hydrate": "linescore",
        },
    )
    finals = []
    for date_block in data.get("dates", []):
        for g in date_block.get("games", []):
            if g.get("status", {}).get("abstractGameState") != "Final":
                continue
            home = g["teams"]["home"]
            away = g["teams"]["away"]
            is_home = home["team"]["id"] == team_id
            runs_for = home.get("score") if is_home else away.get("score")
            runs_against = away.get("score") if is_home else home.get("score")
            if runs_for is None or runs_against is None:
                continue
            finals.append((g["gameDate"], runs_for, runs_against))

    finals.sort(key=lambda x: x[0], reverse=True)
    last_n = finals[:n_games]
    if not last_n:
        return {"recent_runs_for_avg": None, "recent_runs_against_avg": None, "sample": 0}

    rf = sum(x[1] for x in last_n) / len(last_n)
    ra = sum(x[2] for x in last_n) / len(last_n)
    return {"recent_runs_for_avg": rf, "recent_runs_against_avg": ra, "sample": len(last_n)}


def get_pitcher_stats(pitcher_id, season):
    """ERA, WHIP, K/9 de temporada + ultimas 3 aperturas."""
    if pitcher_id is None:
        return None
    season_data = _get(
        f"/people/{pitcher_id}/stats",
        {"stats": "season", "group": "pitching", "season": season},
    )
    era = _first_stat(season_data, "era", default=None)
    whip = _first_stat(season_data, "whip", default=None)
    k9 = _first_stat(season_data, "strikeoutsPer9Inn", default=None)
    ip = _first_stat(season_data, "inningsPitched", default=None)

    log_data = _get(
        f"/people/{pitcher_id}/stats",
        {"stats": "gameLog", "group": "pitching", "season": season},
    )
    last_starts = []
    for split in _splits(log_data)[:3]:
        stat = split.get("stat", {})
        last_starts.append(
            {
                "date": split.get("date"),
                "ip": stat.get("inningsPitched"),
                "er": stat.get("earnedRuns"),
                "k": stat.get("strikeOuts"),
                "bb": stat.get("baseOnBalls"),
            }
        )

    return {
        "id": pitcher_id,
        "era": float(era) if era else None,
        "whip": float(whip) if whip else None,
        "k_per_9": float(k9) if k9 else None,
        "innings_pitched_season": ip,
        "last_3_starts": last_starts,
    }


def get_final_games_range(start_date, end_date):
    """Trae TODOS los juegos terminados de la liga completa en un rango de
    fechas (YYYY-MM-DD, inclusive), en una sola llamada -- se usa para armar
    el cache historico de referencia (ver historical_cache.py) en vez de
    pedir el calendario equipo por equipo, que serian ~30 llamadas por dia."""
    data = _get(
        "/schedule",
        {"sportId": SPORT_ID, "startDate": start_date, "endDate": end_date, "hydrate": "linescore"},
    )
    games = []
    for date_block in data.get("dates", []):
        game_date = date_block.get("date")
        for g in date_block.get("games", []):
            if g.get("status", {}).get("abstractGameState") != "Final":
                continue
            home = g.get("teams", {}).get("home", {})
            away = g.get("teams", {}).get("away", {})
            home_runs = home.get("score")
            away_runs = away.get("score")
            home_id = home.get("team", {}).get("id")
            away_id = away.get("team", {}).get("id")
            if None in (home_runs, away_runs, home_id, away_id):
                continue
            games.append(
                {
                    "game_pk": g.get("gamePk"),
                    "date": game_date,
                    "home_team_id": home_id,
                    "away_team_id": away_id,
                    "home_runs": home_runs,
                    "away_runs": away_runs,
                }
            )
    return games


def get_final_score(game_pk):
    """Para settlement: resultado final ya jugado.

    OJO: usamos el endpoint de /schedule filtrado por gamePk (no
    /game/{id}/linescore a secas) porque el linescore por si solo regresa
    el marcador aunque el juego siga EN VIVO -- no distingue entre un
    partido a la mitad y uno terminado. Usar un marcador parcial como si
    fuera el resultado final settleaba picks mal y corrompia la
    calibracion (fue justo lo que paso: picks se marcaron ganados/perdidos
    con el juego todavia en curso). Aqui SI revisamos que
    abstractGameState sea 'Final' antes de regresar algo."""
    data = _get("/schedule", {"gamePk": game_pk, "hydrate": "linescore"})
    for date_block in data.get("dates", []):
        for g in date_block.get("games", []):
            if g.get("gamePk") != game_pk:
                continue
            if g.get("status", {}).get("abstractGameState") != "Final":
                return None  # todavia en curso, pospuesto, suspendido, etc.
            home = g.get("teams", {}).get("home", {})
            away = g.get("teams", {}).get("away", {})
            home_runs = home.get("score")
            away_runs = away.get("score")
            if home_runs is None or away_runs is None:
                return None
            return {"home_runs": home_runs, "away_runs": away_runs}
    return None


# ---------- helpers ----------

def _first_stat(payload, key, default=None):
    try:
        stats = payload.get("stats", [])
        splits = stats[0].get("splits", [])
        return splits[0].get("stat", {}).get(key, default)
    except (IndexError, KeyError, AttributeError):
        return default


def _splits(payload):
    try:
        return payload.get("stats", [])[0].get("splits", [])
    except (IndexError, KeyError, AttributeError):
        return []


def _safe_div(a, b):
    try:
        a = float(a)
        b = float(b)
        if b == 0:
            return None
        return a / b
    except (TypeError, ValueError):
        return None
