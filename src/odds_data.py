"""
odds_data.py
Trae las cuotas reales de las casas de apuestas via The Odds API
(https://the-odds-api.com/) - gratis hasta 500 requests/mes.

Con esto podemos comparar la probabilidad que calcula nuestro modelo contra
la probabilidad implicita "justa" (sin vig) de la cuota del mercado, y asi
detectar "valor" (edge) real, no solo una proyeccion en el aire.
"""

import time
from datetime import datetime

import requests

BASE_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"

# Si dos juegos entre los mismos equipos (serie de varios partidos, muy
# comun en MLB) tienen cuotas en la misma respuesta de The Odds API, solo
# aceptamos el evento como "el mismo juego" si su hora de inicio esta a
# menos de esto de la hora real del juego. Si no, mejor no usar cuotas
# (None) que usar las de otro partido.
MAX_MATCH_TIME_DIFF_HOURS = 4

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 1.5


def _get_with_retry(url, params, timeout=15):
    """GET con reintentos y backoff exponencial simple. Una consulta de
    cuotas fallida no deberia tumbar el reporte del dia entero -- reintenta
    un par de veces antes de rendirse."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            last_error = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    print(f"[odds_data] WARNING: fallo la consulta de cuotas tras {MAX_RETRIES} intentos -> {last_error}")
    return None


def get_mlb_odds(api_key, regions="us", markets="h2h,totals"):
    """Regresa la lista cruda de eventos con sus cuotas (moneyline y totales).
    Si falla (key invalida, se acabo la cuota gratis, etc.) regresa lista vacia
    y el resto del sistema sigue funcionando solo con probabilidad del modelo."""
    if not api_key:
        return []
    data = _get_with_retry(
        BASE_URL,
        {
            "apiKey": api_key,
            "regions": regions,
            "markets": markets,
            "oddsFormat": "american",
        },
    )
    return data if data is not None else []


def match_game_odds(odds_events, home_team_name, away_team_name, start_time_utc=None):
    """Empareja un juego de la MLB Stats API con su evento en The Odds API
    por nombre de equipo Y por hora de inicio.

    Solo por nombre NO alcanza: si los mismos dos equipos juegan una serie
    de varios partidos (algo normal en MLB, series de 3-4 juegos), The Odds
    API puede traer cuotas de MAS de un juego entre ellos en la misma
    respuesta. Sin filtrar por hora, se podia agarrar el evento equivocado
    (el de otro dia de la serie) -- eso se veia como un "edge" gigante y
    sin sentido contra la proyeccion del dia, cuando en realidad se estaba
    comparando contra el mercado de OTRO juego.

    Si se pasa start_time_utc y hay mas de un evento candidato, nos
    quedamos con el que empiece mas cerca de esa hora; si el mas cercano
    sigue estando a mas de MAX_MATCH_TIME_DIFF_HOURS, mejor regresamos None
    (sin cuotas) que arriesgarnos a comparar contra el juego equivocado."""
    candidates = [
        event for event in odds_events
        if _names_match(event.get("home_team", ""), home_team_name)
        and _names_match(event.get("away_team", ""), away_team_name)
    ]

    if not candidates:
        return None
    if not start_time_utc:
        return candidates[0]

    game_dt = _parse_iso(start_time_utc)
    if game_dt is None:
        return candidates[0]

    def _hours_apart(event):
        event_dt = _parse_iso(event.get("commence_time"))
        if event_dt is None:
            return float("inf")
        return abs((event_dt - game_dt).total_seconds()) / 3600

    best = min(candidates, key=_hours_apart)
    if _hours_apart(best) > MAX_MATCH_TIME_DIFF_HOURS:
        return None
    return best


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _names_match(a, b):
    a, b = a.lower(), b.lower()
    return a in b or b in a


def best_moneyline(event, side):
    """side = 'home' o 'away'. Regresa la mejor cuota americana disponible
    entre las casas de apuestas listadas."""
    if not event:
        return None
    team_name = event["home_team"] if side == "home" else event["away_team"]
    best = None
    for bookmaker in event.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market["key"] != "h2h":
                continue
            for outcome in market.get("outcomes", []):
                if outcome["name"] == team_name:
                    price = outcome["price"]
                    if best is None or price > best:
                        best = price
    return best


def best_total(event):
    """Regresa (linea, cuota_over, cuota_under) mas comun/mejor entre casas."""
    if not event:
        return None
    lines = {}
    for bookmaker in event.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market["key"] != "totals":
                continue
            for outcome in market.get("outcomes", []):
                point = outcome.get("point")
                if point is None:
                    continue
                lines.setdefault(point, {})
                lines[point][outcome["name"]] = outcome["price"]
    if not lines:
        return None
    # usamos la linea que mas casas ofrecen (la "consenso")
    point = max(lines, key=lambda p: len(lines[p]))
    over = lines[point].get("Over")
    under = lines[point].get("Under")
    return {"line": point, "over_price": over, "under_price": under}


def american_to_implied_prob(price):
    """Convierte cuota americana a probabilidad implicita (0-1), SIN quitar
    el vig/comision de la casa. Esta es la probabilidad "cruda" del mercado,
    no la probabilidad "justa" -- para eso usa remove_vig_two_way."""
    if price is None:
        return None
    if price > 0:
        return 100 / (price + 100)
    return -price / (-price + 100)


def remove_vig_two_way(prob_a, prob_b):
    """Quita el vig (comision de la casa) de un mercado de dos resultados
    (ej. Over/Under, o Home/Away). Las casas de apuestas fijan cuotas para
    que la suma de probabilidades implicitas sea > 100% (esa diferencia es
    su ganancia garantizada). Normalizamos dividiendo cada probabilidad
    entre la suma, para que el edge que calculamos despues sea contra la
    probabilidad "justa" del mercado, no contra una inflada a favor de la
    casa.

    Regresa (prob_a_justa, prob_b_justa) que suman 1.0. Si falta algun dato,
    regresa (None, None)."""
    if prob_a is None or prob_b is None:
        return None, None
    total = prob_a + prob_b
    if total <= 0:
        return None, None
    return prob_a / total, prob_b / total
