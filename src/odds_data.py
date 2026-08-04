"""
odds_data.py
Trae las cuotas reales de las casas de apuestas via The Odds API
(https://the-odds-api.com/) - gratis hasta 500 requests/mes.

Con esto podemos comparar la probabilidad que calcula nuestro modelo contra
la probabilidad implicita de la cuota del mercado, y asi detectar "valor"
(edge) real, no solo una proyeccion en el aire.
"""

import requests

BASE_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"


def get_mlb_odds(api_key, regions="us", markets="h2h,totals"):
    """Regresa la lista cruda de eventos con sus cuotas (moneyline y totales).
    Si falla (key invalida, se acabo la cuota gratis, etc.) regresa lista vacia
    y el resto del sistema sigue funcionando solo con probabilidad del modelo."""
    if not api_key:
        return []
    try:
        resp = requests.get(
            BASE_URL,
            params={
                "apiKey": api_key,
                "regions": regions,
                "markets": markets,
                "oddsFormat": "american",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"[odds_data] WARNING: fallo la consulta de cuotas -> {e}")
        return []


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
    """Convierte cuota americana a probabilidad implicita (0-1), sin quitar
    el vig/comision de la casa (eso es aproximado, no probabilidad 'justa')."""
    if price is None:
        return None
    if price > 0:
        return 100 / (price + 100)
    return -price / (-price + 100)
