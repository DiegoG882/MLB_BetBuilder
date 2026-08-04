"""
main.py
Orquestador diario:
  1. Settle: revisa picks pendientes de dias anteriores y actualiza la
     calibracion (el "aprendizaje").
  2. Trae los juegos de hoy + pitchers probables + stats.
  3. Trae cuotas reales (si hay ODDS_API_KEY) y calcula probabilidad
     implicita del mercado.
  4. Genera picks (moneyline + total de carreras) con probabilidad ajustada
     por calibracion y semaforo de riesgo.
  5. Manda todo a Telegram.
  6. Guarda los picks nuevos en el historial (status pending) para que
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

SEASON = datetime.now().year


def main():
    today = datetime.now(ZoneInfo(TZ)).strftime("%Y-%m-%d")
    print(f"=== MLB Bet Builder - {today} ===")

    # 1) Aprender de dias anteriores
    history = storage.load_history()
    calibration = storage.load_calibration()
    settled = settle.settle_pending_picks(history, calibration)
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

    for game in games:
        block, picks = _process_game(game, odds_events, calibration)
        if block:
            message_lines.append(block)
            for pick in picks:
                new_picks_for_history.append((pick, game["game_pk"]))

    message_lines.append(
        "\n⚠️ Esto es un modelo estadistico, no una garantia. "
        "Jugá responsable: fijate un límite antes de apostar y respétalo pase lo que pase."
    )
    full_message = "\n".join(message_lines)

    sent = telegram_bot.send_message(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, full_message)
    print(f"Mensaje enviado a Telegram: {sent}")

    # 6) Guardar historial
    for pick, game_pk in new_picks_for_history:
        storage.record_pick(history, pick, game_pk, today)
    storage.save_history(history)

    print("Listo.")


def _process_game(game, odds_events, calibration):
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

    # el pitcher rival de cada equipo es el que afecta su proyeccion de carreras
    home_proj = model_module.project_team_runs(home_season, home_recent, away_pitcher)
    away_proj = model_module.project_team_runs(away_season, away_recent, home_pitcher)

    if home_proj is None or away_proj is None:
        return None, []  # datos insuficientes (ej: inicio de temporada), nos lo saltamos

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

    # --- Moneyline ---
    # probabilidad "cruda" via diferencia de proyeccion de carreras, pasada
    # por una logistica simple para acotarla entre 0 y 1
    diff = home_proj - away_proj
    home_prob_raw = 1 / (1 + pow(2.71828, -0.5 * diff))
    home_prob_raw = model_module.apply_calibration(home_prob_raw, "moneyline", calibration)
    away_prob_raw = 1 - home_prob_raw

    home_implied = odds_data.american_to_implied_prob(home_ml_price)
    away_implied = odds_data.american_to_implied_prob(away_ml_price)

    if home_prob_raw >= away_prob_raw:
        pick = model_module.build_pick(
            "moneyline",
            f"{game['home_team_name']} gana (ML)",
            home_prob_raw, home_implied, sample_size,
            extra={"side": "home", "price": home_ml_price},
        )
    else:
        pick = model_module.build_pick(
            "moneyline",
            f"{game['away_team_name']} gana (ML)",
            away_prob_raw, away_implied, sample_size,
            extra={"side": "away", "price": away_ml_price},
        )
    picks.append(pick)

    # --- Total de carreras ---
    line = total_info["line"] if total_info else round(total_proj * 2) / 2 - 0.5
    over_prob = model_module.poisson_over_prob(total_proj, line)
    over_prob = model_module.apply_calibration(over_prob, "total_over", calibration)
    under_prob = 1 - over_prob

    over_implied = odds_data.american_to_implied_prob(total_info["over_price"]) if total_info else None
    under_implied = odds_data.american_to_implied_prob(total_info["under_price"]) if total_info else None

    if over_prob >= under_prob:
        pick2 = model_module.build_pick(
            "total_over", f"Over {line}", over_prob, over_implied, sample_size,
            extra={"line": line, "price": total_info["over_price"] if total_info else None},
        )
    else:
        pick2 = model_module.build_pick(
            "total_under", f"Under {line}", under_prob, under_implied, sample_size,
            extra={"line": line, "price": total_info["under_price"] if total_info else None},
        )
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
            line_txt += f" (mercado: {round(p['implied_prob']*100,1)}%, edge: {edge_pct:+}%)"
        lines.append(line_txt)

    return "\n".join(lines), picks


if __name__ == "__main__":
    main()
