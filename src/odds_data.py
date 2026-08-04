"""
odds_data.py
Trae las cuotas reales de las casas de apuestas via The Odds API
(https://the-odds-api.com/) - gratis hasta 500 requests/mes.

Con esto podemos comparar la probabilidad que calcula nuestro modelo contra
la probabilidad implicita "justa" (sin vig) de la cuota del mercado, y asi
detectar "valor" (edge) real, no solo una proyeccion en el aire.
"""

import time

import requests

BASE_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"

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


def match_game_odds(odds_events, home_team_name, away_team_name):
    """Empareja un juego de la MLB Stats API con su evento en The Odds API
    por nombre de equipo (los nombres suelen coincidir, pero por si acaso
    hacemos match parcial)."""
    for event in odds_events:
        h = event.get("home_team", "")
        a = event.get("away_team", "")
        if _names_match(h, home_team_name) and _names_match(a, away_team_name):
            return event
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
