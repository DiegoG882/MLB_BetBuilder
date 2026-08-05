"""
main.py
Orquestador diario:
  1. Settle: revisa picks pendientes de dias anteriores, actualiza la
     calibracion (el "aprendizaje") y expira picks que quedaron pending
     demasiado tiempo sin resultado.
  2. Trae los juegos de hoy + pitchers probables + stats (incluyendo ERA de
     equipo completo, para el ajuste de bullpen) + ventaja de local.
  3. Trae cuotas reales (si hay ODDS_API_KEY), calcula probabilidad
     implicita del mercado SIN vig (probabilidad "justa") para medir edge
     real.
  4. Genera picks (moneyline + total de carreras) con probabilidad ajustada
     por calibracion y semaforo de riesgo.
  5. Si hay bankroll configurado, sugiere cuanto apostar por pick (Kelly
     fraccionado) y un resumen de exposicion total del dia.
  6. Manda todo a Telegram.
  7. Guarda los picks nuevos en el historial (status pending) para que
     manana se puedan revisar.

Se corre una vez al dia via GitHub Actions (ver .github/workflows/daily.yml)
o via cron si lo corres en tu propia maquina / Raspberry.
"""

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import mlb_data
from src import odds_data
from src import model as model_module
from src import storage
from src import settle
from src import telegram_bot

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
MAX_GAMES = int(os.getenv("MAX_GAMES_PER_DAY", "15"))
TZ = os.getenv("TIMEZONE", "America/Mexico_City")
def _env_float(name, default):
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


KELLY_FRACTION = _env_float("KELLY_FRACTION", model_module.DEFAULT_KELLY_FRACTION)
# % del bankroll total a partir del cual avisamos que la exposicion del dia
# es alta (no bloquea nada, solo te avisa para que decidas tu).
EXPOSURE_WARNING_PCT = _env_float("EXPOSURE_WARNING_PCT", 0.15)

SEASON = datetime.now().year


def main():
    today = datetime.now(ZoneInfo(TZ)).strftime("%Y-%m-%d")
    print(f"=== MLB Bet Builder - {today} ===")

    bankroll = storage.load_bankroll(default_from_env=os.getenv("BANKROLL_USD"))
    if bankroll:
        print(f"Bankroll configurado: ${bankroll:,.2f} (Kelly fraccionado: {KELLY_FRACTION*100:.0f}%)")
    else:
        print("Sin bankroll configurado -- el mensaje no va a incluir montos sugeridos. "
              "Usa 'python -m src.set_bankroll <monto>' para configurarlo.")

    # 1) Aprender de dias anteriores + limpiar picks viejos sin resultado
    history = storage.load_history()
    calibration = storage.load_calibration()
    settled = settle.settle_pending_picks(history, calibration, today_str=today)
    print(f"Picks de dias anteriores resueltos: {settled}")
    storage.save_history(history)
    storage.save_calibration(calibration)

    # 2) Juegos de hoy
    games = mlb_data.get_schedule(today)
    games = games[:MAX_GAMES]
    print(f"Juegos encontrados hoy: {len(games)}")

    if not games:
        telegram_bot.send_message(
            TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
            f"⚾ No hay juegos de MLB programados hoy ({today})."
        )
        return

    # 3) Cuotas reales (opcional)
    odds_events = odds_data.get_mlb_odds(ODDS_API_KEY) if ODDS_API_KEY else []

    message_lines = [f"⚾ <b>Bet Builder MLB - {today}</b>\n"]
    new_picks_for_history = []
    total_stake_amount = 0.0

    for game in games:
        block, picks, stake_total_for_game = _process_game(game, odds_events, calibration, bankroll)
        if block:
            message_lines.append(block)
            total_stake_amount += stake_total_for_game
            for pick in picks:
                new_picks_for_history.append((pick, game["game_pk"]))

    if bankroll:
        message_lines.append(_exposure_summary(bankroll, total_stake_amount))

    message_lines.append(
        "\n⚠️ Esto es un modelo estadistico, no una garantia. "
        "Jugá responsable: fijate un límite antes de apostar y respétalo pase lo que pase."
    )
    full_message = "\n".join(message_lines)

    sent = telegram_bot.send_message(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, full_message)
    print(f"Mensaje enviado a Telegram: {sent}")

    # 7) Guardar historial
    for pick, game_pk in new_picks_for_history:
        storage.record_pick(history, pick, game_pk, today)
    storage.save_history(history)

    print("Listo.")


def _exposure_summary(bankroll, total_stake_amount):
    pct_of_bankroll = total_stake_amount / bankroll if bankroll else 0
    lines = [
        f"\n💰 <b>Exposición sugerida hoy:</b> ${total_stake_amount:,.2f} "
        f"({pct_of_bankroll*100:.1f}% de tu bankroll de ${bankroll:,.2f})"
    ]
    if pct_of_bankroll >= EXPOSURE_WARNING_PCT:
        lines.append(
            f"⚠️ Eso es {pct_of_bankroll*100:.0f}% de tu bankroll en un solo día — "
            "considera saltarte los picks de riesgo más alto si no te sientes cómodo con esa exposición."
        )
    return "\n".join(lines)


def _process_game(game, odds_events, calibration, bankroll):
    home_id = game["home_team_id"]
    away_id = game["away_team_id"]

    home_season = mlb_data.get_team_season_stats(home_id, SEASON)
    away_season = mlb_data.get_team_season_stats(away_id, SEASON)
    home_recent = mlb_data.get_team_recent_form(home_id, game["date"])
    away_recent = mlb_data.get_team_recent_form(away_id, game["date"])

    home_pitcher = None
    away_pitcher = None
    if game["home_pitcher"]:
        home_pitcher = mlb_data.get_pitcher_stats(game["home_pitcher"]["id"], SEASON)
    if game["away_pitcher"]:
        away_pitcher = mlb_data.get_pitcher_stats(game["away_pitcher"]["id"], SEASON)

    # el pitcheo rival de cada equipo (abridor + ERA de staff completo) es el
    # que afecta su proyeccion de carreras; is_home agrega la ventaja de
    # jugar en casa solo al equipo local.
    home_proj = model_module.project_team_runs(
        home_season, home_recent, away_pitcher,
        is_home=True, opp_team_era=away_season.get("team_era"),
    )
    away_proj = model_module.project_team_runs(
        away_season, away_recent, home_pitcher,
        is_home=False, opp_team_era=home_season.get("team_era"),
    )

    if home_proj is None or away_proj is None:
        return None, [], 0.0  # datos insuficientes (ej: inicio de temporada), nos lo saltamos

    total_proj = home_proj + away_proj
    sample_size = min(
        home_recent.get("sample") or 0,
        away_recent.get("sample") or 0,
    )

    # cuotas reales si estan disponibles
    odds_event = odds_data.match_game_odds(odds_events, game["home_team_name"], game["away_team_name"])
    home_ml_price = odds_data.best_moneyline(odds_event, "home") if odds_event else None
    away_ml_price = odds_data.best_moneyline(odds_event, "away") if odds_event else None
    total_info = odds_data.best_total(odds_event) if odds_event else None

    picks = []
    stake_total_for_game = 0.0

    # --- Moneyline ---
    # probabilidad "cruda" via diferencia de proyeccion de carreras, pasada
    # por una logistica simple para acotarla entre 0 y 1
    diff = home_proj - away_proj
    home_prob_raw = 1 / (1 + pow(2.71828, -0.5 * diff))
    home_prob_raw = model_module.apply_calibration(home_prob_raw, "moneyline", calibration)
    away_prob_raw = 1 - home_prob_raw

    # de-vig: la probabilidad implicita cruda de la cuota siempre suma > 100%
    # (esa es la ganancia de la casa). Quitamos ese vig para comparar el
    # edge del modelo contra la probabilidad "justa" real del mercado.
    home_implied_raw = odds_data.american_to_implied_prob(home_ml_price)
    away_implied_raw = odds_data.american_to_implied_prob(away_ml_price)
    home_implied_fair, away_implied_fair = odds_data.remove_vig_two_way(home_implied_raw, away_implied_raw)

    if home_prob_raw >= away_prob_raw:
        price = home_ml_price
        pick = model_module.build_pick(
            "moneyline",
            f"{game['home_team_name']} gana (ML)",
            home_prob_raw, home_implied_fair, sample_size,
            extra={"side": "home", "price": price},
        )
    else:
        price = away_ml_price
        pick = model_module.build_pick(
            "moneyline",
            f"{game['away_team_name']} gana (ML)",
            away_prob_raw, away_implied_fair, sample_size,
            extra={"side": "away", "price": price},
        )
    stake_pct, stake_amount = model_module.suggested_stake(pick["model_prob"], price, bankroll, KELLY_FRACTION)
    pick["stake_pct"] = stake_pct
    pick["stake_amount"] = stake_amount
    if stake_amount:
        stake_total_for_game += stake_amount
    picks.append(pick)

    # --- Total de carreras ---
    line = total_info["line"] if total_info else round(total_proj * 2) / 2 - 0.5
    over_prob = model_module.poisson_over_prob(total_proj, line)
    over_prob = model_module.apply_calibration(over_prob, "total_over", calibration)
    under_prob = 1 - over_prob

    over_implied_raw = odds_data.american_to_implied_prob(total_info["over_price"]) if total_info else None
    under_implied_raw = odds_data.american_to_implied_prob(total_info["under_price"]) if total_info else None
    over_implied_fair, under_implied_fair = odds_data.remove_vig_two_way(over_implied_raw, under_implied_raw)

    if over_prob >= under_prob:
        price2 = total_info["over_price"] if total_info else None
        pick2 = model_module.build_pick(
            "total_over", f"Over {line}", over_prob, over_implied_fair, sample_size,
            extra={"line": line, "price": price2},
        )
    else:
        price2 = total_info["under_price"] if total_info else None
        pick2 = model_module.build_pick(
            "total_under", f"Under {line}", under_prob, under_implied_fair, sample_size,
            extra={"line": line, "price": price2},
        )
    stake_pct2, stake_amount2 = model_module.suggested_stake(pick2["model_prob"], price2, bankroll, KELLY_FRACTION)
    pick2["stake_pct"] = stake_pct2
    pick2["stake_amount"] = stake_amount2
    if stake_amount2:
        stake_total_for_game += stake_amount2
    picks.append(pick2)

    # --- armar texto del bloque ---
    lines = [
        f"\n🆚 <b>{game['away_team_name']} @ {game['home_team_name']}</b>",
    ]
    if game["away_pitcher"]:
        lines.append(f"  Pitcher visitante: {game['away_pitcher']['name']}")
    if game["home_pitcher"]:
        lines.append(f"  Pitcher local: {game['home_pitcher']['name']}")
    lines.append(f"  Proyección: {game['away_team_name']} {away_proj} - {home_proj} {game['home_team_name']}")

    for p in picks:
        prob_pct = round(p["model_prob"] * 100, 1)
        line_txt = f"  • {p['selection']} — {prob_pct}% — {p['risk']}"
        if p["implied_prob"] is not None:
            edge_pct = round(p["edge"] * 100, 1)
            line_txt += f" (mercado sin vig: {round(p['implied_prob']*100,1)}%, edge: {edge_pct:+}%)"
        if p.get("stake_amount"):
            line_txt += f"\n     💵 Sugerido: ${p['stake_amount']:,.2f} ({p['stake_pct']*100:.1f}% del bankroll)"
        lines.append(line_txt)

    return "\n".join(lines), picks, stake_total_for_game


if __name__ == "__main__":
    main()
