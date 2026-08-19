"""
park_factors.py
Factor de carreras por estadio: cuanto favorece (>1.0) o castiga (<1.0) la
anotacion respecto al promedio de la liga, por altura, dimensiones del
jardin, y viento predominante del parque. Son valores publicados y
razonablemente estables temporada a temporada (no cambian partido a
partido como el clima del dia).

Se aplica como multiplicador sobre la proyeccion de carreras de AMBOS
equipos cuando juegan en ese estadio (afecta tanto al bateo local como al
visitante -- el parque no distingue de que equipo es la pelota).

Valores aproximados basados en park factors publicos de temporadas
recientes (Baseball-Reference / FanGraphs). Si un equipo no esta en la
tabla (cambio de estadio, expansion, etc.) se usa 1.0 (neutral).
"""

PARK_FACTORS = {
    "Colorado Rockies": 1.18,          # Coors Field -- altura, el mas ofensivo con margen
    "Cincinnati Reds": 1.08,           # Great American Ball Park -- jardines cortos
    "Boston Red Sox": 1.06,            # Fenway Park -- el Green Monster ayuda a bateadores
    "Texas Rangers": 1.05,             # Globe Life Field
    "Baltimore Orioles": 1.04,
    "Philadelphia Phillies": 1.04,
    "Chicago Cubs": 1.03,              # Wrigley Field -- MUY dependiente del viento del dia
    "Toronto Blue Jays": 1.02,
    "Minnesota Twins": 1.01,
    "Arizona Diamondbacks": 1.01,
    "Los Angeles Angels": 1.00,
    "Atlanta Braves": 1.00,
    "Milwaukee Brewers": 1.00,
    "Chicago White Sox": 0.99,
    "Washington Nationals": 0.99,
    "New York Yankees": 0.99,
    "Houston Astros": 0.98,
    "Los Angeles Dodgers": 0.98,
    "Kansas City Royals": 0.98,
    "St. Louis Cardinals": 0.97,
    "Detroit Tigers": 0.97,
    "Cleveland Guardians": 0.96,
    "New York Mets": 0.96,
    "Tampa Bay Rays": 0.96,
    "Athletics": 0.95,
    "Seattle Mariners": 0.94,
    "San Diego Padres": 0.94,
    "Pittsburgh Pirates": 0.93,
    "Miami Marlins": 0.92,
    "San Francisco Giants": 0.90,      # Oracle Park -- el mas pitcher-friendly
}

NEUTRAL_FACTOR = 1.0


def get_park_factor(home_team_name):
    """Regresa el factor de parque del equipo local. Si no lo tenemos en la
    tabla, regresa 1.0 (neutral) en vez de fallar -- mejor un ajuste nulo
    que tronar el calculo de proyeccion."""
    return PARK_FACTORS.get(home_team_name, NEUTRAL_FACTOR)
