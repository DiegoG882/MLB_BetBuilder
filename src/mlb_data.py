"""
mlb_data.py
Todo lo que jala datos "de verdad" del juego: calendario, pitchers probables,
stats de equipo, stats de pitcher/bateador, forma reciente y resultados finales.

Usa la MLB Stats API oficial (statsapi.mlb.com) - es publica y gratuita, no
necesita API key. La documentacion no es oficial pero es muy usada por la
comunidad de analytics de beisbol.
"""

import requests
from datetime import datetime, timedelta

BASE_URL = "https://statsapi.mlb.com/api/v1"
SPORT_ID = 1  # MLB


def _get(path, params=None):
    """Wrapper simple con manejo de errores para no tumbar todo el script
    si un endpoint falla (ej: un pitcher sin stats todavia en la temporada)."""
    url = f"{BASE_URL}{path}"
    try:
        resp = requests.get(url, params=params or {}, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"[mlb_data] WARNING: fallo {url} -> {e}")
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
    """Runs por juego a favor y en contra en la temporada."""
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


def get_final_score(game_pk):
    """Para settlement: resultado final ya jugado."""
    data = _get(f"/game/{game_pk}/linescore")
    if not data:
        return None
    home_runs = data.get("teams", {}).get("home", {}).get("runs")
    away_runs = data.get("teams", {}).get("away", {}).get("runs")
    if home_runs is None or away_runs is None:
        return None
    return {"home_runs": home_runs, "away_runs": away_runs}


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
