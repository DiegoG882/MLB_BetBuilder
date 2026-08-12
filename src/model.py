"""
model.py
El "cerebro": proyecta carreras esperadas por equipo, calcula probabilidad de
cada pick con una distribucion de Poisson, compara contra la cuota real del
mercado cuando esta disponible, aplica la calibracion aprendida de dias
anteriores (ver storage.py / settle.py), y sugiere cuanto apostar (Kelly
fraccionado) segun tu bankroll.
"""

import math

# Ventaja de jugar en casa: en MLB los equipos locales anotan en promedio
# ~4-5% mas carreras que de visitante (factor de cancha, rutina, sin viaje,
# ultimo turno al bate). La codificamos como un bono fijo de carreras en vez
# de un %, para que sea facil de entender y ajustar.
HOME_FIELD_RUN_BONUS = 0.15

# Cuanto pesa el ERA del abridor probable vs. el ERA de todo el staff
# (abridores + bullpen) del equipo rival al proyectar carreras en contra.
# Un abridor tira ~5-6 entradas; el resto del juego lo cubre el bullpen, que
# puede ser mucho mejor o peor que el abridor. Sin datos de bullpen
# separados (no los trae la API sin llamadas extra), usamos el ERA de
# equipo completo como proxy de "que tan bueno es el bullpen en promedio".
STARTER_WEIGHT_IN_OPPONENT_FACTOR = 0.65
TEAM_STAFF_WEIGHT_IN_OPPONENT_FACTOR = 1 - STARTER_WEIGHT_IN_OPPONENT_FACTOR


def poisson_pmf(k, lam):
    if lam <= 0:
        return 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def poisson_over_prob(lam, line):
    """P(runs > line) para una linea tipo 8.5 con distribucion Poisson(lam).
    Como las lineas de MLB casi siempre terminan en .5, no hay empate que
    manejar: sumamos P(k) para k > line."""
    threshold = math.floor(line) + 1
    cumulative = sum(poisson_pmf(k, lam) for k in range(0, threshold))
    return max(0.0, min(1.0, 1 - cumulative))


def project_team_runs(
    season_stats,
    recent_form,
    opp_pitcher_stats,
    league_avg_era=4.20,
    is_home=False,
    opp_team_era=None,
):
    """Proyeccion simple de carreras esperadas para un equipo en un juego:
    60% peso a temporada completa, 40% a forma reciente (mas sensible a
    rachas), ajustado por que tan bueno/malo es el pitcheo rival (abridor +
    bullpen del equipo contrario) vs. el promedio de la liga, mas un bono
    fijo si el equipo juega de local.

    Es un modelo deliberadamente simple y transparente (no una caja negra)
    para que puedas ver exactamente por que salio cada numero y ajustarlo
    vos mismo si queres afinarlo."""
    season_rs = season_stats.get("runs_scored_per_game")
    recent_rs = recent_form.get("recent_runs_for_avg")

    if season_rs is None and recent_rs is None:
        return None

    base = _weighted_avg(season_rs, 0.6, recent_rs, 0.4)
    if base is None:
        base = season_rs or recent_rs

    starter_era = None
    if opp_pitcher_stats:
        starter_era = opp_pitcher_stats.get("era")

    effective_opp_era = _weighted_avg(
        starter_era, STARTER_WEIGHT_IN_OPPONENT_FACTOR,
        opp_team_era, TEAM_STAFF_WEIGHT_IN_OPPONENT_FACTOR,
    )

    if effective_opp_era:
        # si el pitcheo rival (abridor + bullpen) tiene ERA mejor que el
        # promedio de liga, baja la proyeccion de carreras; si es peor, la
        # sube. Factor acotado para no que un solo dato dispare numeros
        # irreales con poca muestra.
        factor = effective_opp_era / league_avg_era
        factor = max(0.7, min(1.4, factor))
        base = base * factor

    if is_home:
        base += HOME_FIELD_RUN_BONUS

    return round(base, 2)


def _weighted_avg(a, wa, b, wb):
    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a
    return a * wa + b * wb


def risk_label(edge, sample_size):
    """Semaforo de riesgo. 'edge' = diferencia entre probabilidad del modelo
    y probabilidad implicita del mercado sin vig (si hay cuota). Sin cuota,
    usamos solo que tan lejos de 50% esta la probabilidad del modelo, con
    penalidad si el tamano de muestra (juegos recientes disponibles) es
    chico."""
    confidence = abs(edge)
    if sample_size is not None and sample_size < 5:
        confidence *= 0.6  # poca muestra = menos confianza aunque el numero se vea bonito

    if confidence >= 0.12:
        return "🟢 Riesgo bajo"
    elif confidence >= 0.06:
        return "🟡 Riesgo medio"
    else:
        return "🔴 Riesgo alto"


def build_pick(market_type, selection, model_prob, implied_prob, sample_size, extra=None):
    edge = None
    if implied_prob is not None:
        edge = model_prob - implied_prob
    else:
        edge = model_prob - 0.5

    return {
        "market_type": market_type,       # 'moneyline' | 'total_over' | 'total_under'
        "selection": selection,           # texto legible: "Yankees ML", "Over 8.5"
        "model_prob": round(model_prob, 4),
        "implied_prob": round(implied_prob, 4) if implied_prob is not None else None,
        "edge": round(edge, 4),
        "risk": risk_label(edge, sample_size),
        "extra": extra or {},
    }


def apply_calibration(model_prob, market_type, calibration):
    """Ajusta la probabilidad cruda del modelo usando la correccion aprendida
    de dias anteriores para ese tipo de mercado y ese rango (bucket) de
    probabilidad. Ver storage.py para como se actualiza calibration.json."""
    bucket = _bucket_for(model_prob)
    key = f"{market_type}:{bucket}"
    entry = calibration.get(key)
    if not entry or entry.get("n", 0) < 5:
        # sin suficiente historial todavia para ese bucket, no ajustamos
        return model_prob

    predicted_avg = entry["predicted_sum"] / entry["n"]
    actual_rate = entry["hits"] / entry["n"]
    correction = actual_rate - predicted_avg
    adjusted = model_prob + correction
    return max(0.01, min(0.99, adjusted))


def _bucket_for(prob):
    """Agrupa en buckets de 10% (50-60, 60-70, ... 90-100) para tener
    suficiente muestra por bucket antes de confiar en el ajuste."""
    pct = int(prob * 100)
    lower = (pct // 10) * 10
    return f"{lower}-{lower+10}"


# ---------- Bankroll / Kelly Criterion ----------

# Kelly "puro" apuesta el % matematicamente optimo, pero asume que tu
# probabilidad estimada es exacta -- en la practica nunca lo es, y Kelly
# puro te puede llevar a apuestas enormes con variancia brutal si te
# equivocas un poco. Un cuarto de Kelly (25%) es el estandar de la industria
# para bajar esa variancia a cambio de crecer un poco mas lento.
DEFAULT_KELLY_FRACTION = 0.25

# Tope duro: sin importar que tan grande salga Kelly, nunca sugerir apostar
# mas de este % del bankroll en un solo pick. Proteccion contra errores del
# modelo o de la cuota. Bajado de 5% a 2%: con 4 picks/dia al 5% cada uno, la
# suma podia llegar a 20% del bankroll en un solo dia -- demasiado para
# aguantar una racha mala. Configurable con la variable de entorno
# MAX_STAKE_PCT_OF_BANKROLL si despues de varias semanas de calibracion
# confirmada quieres mas agresividad.
MAX_STAKE_PCT_OF_BANKROLL = 0.02

# Si el edge (diferencia entre la probabilidad del modelo y la del mercado
# sin vig) es mayor a este umbral, casi siempre es senal de un dato mal
# calibrado (pitcher recien traspasado, cuota mal emparejada al juego, etc.)
# y no una oportunidad real -- es muy raro que el mercado se equivoque por
# tanto. Los picks que pasan este umbral quedan fuera de la seleccion de
# "picks fuertes" con monto sugerido (se siguen mostrando en el reporte
# completo, pero sin dinero automatico encima). Configurable con la variable
# de entorno EDGE_SANITY_CAP.
EDGE_SANITY_CAP = 0.15

def american_to_decimal_odds(price):
    """Convierte cuota americana a cuota decimal (ej. -150 -> 1.667,
    +150 -> 2.5). Cuota decimal es la que usa la formula de Kelly."""
    if price is None:
        return None
    if price > 0:
        return 1 + (price / 100)
    return 1 + (100 / -price)


def kelly_fraction(model_prob, price, fraction=DEFAULT_KELLY_FRACTION, max_stake_pct=MAX_STAKE_PCT_OF_BANKROLL):
    """Fraccion del bankroll a apostar segun el criterio de Kelly, aplicando
    un multiplicador fraccional (Kelly a 1/4 por default) y un tope maximo
    por pick. Regresa 0.0 si no hay edge (no conviene apostar) o si falta la
    cuota."""
    decimal_odds = american_to_decimal_odds(price)
    if decimal_odds is None or decimal_odds <= 1:
        return 0.0

    b = decimal_odds - 1  # ganancia neta por unidad apostada
    p = model_prob
    q = 1 - p

    full_kelly = (b * p - q) / b
    if full_kelly <= 0:
        return 0.0  # el modelo no ve edge suficiente para que Kelly recomiende apostar

    stake_pct = full_kelly * fraction
   return round(min(stake_pct, max_stake_pct), 4)


def suggested_stake(model_prob, price, bankroll, fraction=DEFAULT_KELLY_FRACTION):
    """Regresa (stake_pct, stake_amount) redondeado a 2 decimales. Si no hay
    bankroll configurado o no hay cuota, regresa (0.0, None) para que el
    caller sepa que no se puede sugerir un monto."""
    if not bankroll or bankroll <= 0 or price is None:
        return 0.0, None
    pct = kelly_fraction(model_prob, price, fraction)
    if pct <= 0:
        return 0.0, None
    amount = round(bankroll * pct, 2)
    return pct, amount
