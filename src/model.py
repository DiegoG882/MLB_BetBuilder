"""
model.py
El "cerebro": proyecta carreras esperadas por equipo, calcula probabilidad de
cada pick con una distribucion de Poisson, compara contra la cuota real del
mercado cuando esta disponible, y aplica la calibracion aprendida de dias
anteriores (ver storage.py / settle.py).
"""

import math


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


def project_team_runs(season_stats, recent_form, opp_pitcher_stats, league_avg_era=4.20):
    """Proyeccion simple de carreras esperadas para un equipo en un juego:
    60% peso a temporada completa, 40% a forma reciente (mas sensible a
    rachas), ajustado por que tan bueno/malo es el pitcher rival vs el
    promedio de la liga.

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

    pitcher_era = None
    if opp_pitcher_stats:
        pitcher_era = opp_pitcher_stats.get("era")

    if pitcher_era:
        # si el pitcher rival tiene ERA mejor que el promedio de liga, baja la
        # proyeccion de carreras; si es peor, la sube. Factor acotado para no
        # que un solo dato dispare numeros irreales con poca muestra.
        factor = pitcher_era / league_avg_era
        factor = max(0.7, min(1.4, factor))
        base = base * factor

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
    y probabilidad implicita del mercado (si hay cuota). Sin cuota, usamos
    solo que tan lejos de 50% esta la probabilidad del modelo, con penalidad
    si el tamano de muestra (juegos recientes disponibles) es chico."""
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
