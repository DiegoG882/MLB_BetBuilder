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
  5. Si hay bankroll configurado, calcula Kelly fraccionado por pick y
     escala TODOS los montos si la suma del dia pasa el tope de exposicion
     diaria (para nunca sugerir apostar mas de lo razonable en un solo dia).
  6. Manda el reporte completo a Telegram, y si hay bankroll, un SEGUNDO
     mensaje corto con nada mas los picks que si tienen monto sugerido y
     cuanto meterle a cada uno.
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

SEASON = datetime.now().year


def _env_float(name, default):
    """Como os.getenv(name, default) pero tambien cae al default si la
    variable existe pero esta vacia -- pasa seguido con GitHub Actions
    cuando referencias un secret que no configuraste (queda '' en vez de
    no existir), y eso tronaba con float('')."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


KELLY_FRACTION = _env_float("KELLY_FRACTION", model_module.DEFAULT_KELLY_FRACTION)
# Tope de cuanto del bankroll se sugiere exponer EN TOTAL en un solo dia,
# sumando todos los picks. Sin esto, cada pick se topa individual en 5%
# (ver model.MAX_STAKE_PCT_OF_BANKROLL) pero con muchos juegos la suma
# facil pasa de 100% del bankroll, lo cual no tiene sentido. Si la suma
# cruda pasa este tope, escalamos TODOS los montos hacia abajo
# proporcionalmente.
DAILY_EXPOSURE_CAP_PCT = _env_float("DAILY_EXPOSURE_CAP_PCT", 0.20)


def main():
    today = datetime.now(ZoneInfo(TZ)).strftime("%Y-%m-%d")
    print(f"=== MLB Bet Builder - {today} ===")

    bankroll = storage.load_bankroll(default_from_env=os.getenv("BANKROLL_USD"))
    if bankroll:
        print(f"Bankroll configurado: ${bankroll:,.2f} (Kelly fraccionado: {KELLY_FRACTION*100:.0f}%, tope diario: {DAILY_EXPOSURE_CAP_PCT*100:.0f}%)")
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

    # 4) Calcular proyecciones y picks de cada juego (todavia sin montos)
    all_game_data = []
    for game in games:
        data = _compute_game(game, odds_events, calibration)
        if data:
            all_game_data.append(data)

    # 5) Sizing de bankroll (Kelly + tope de exposicion diaria total)
    scaled = _apply_stake_sizing(all_game_data, bankroll)

    # 6) Armar y mandar el reporte completo
    message_lines = [f"⚾ <b>Bet Builder MLB - {today}</b>\n"]
    for data in all_game_data:
        message_lines.append(_render_game_block(data))

    if bankroll:
        message_lines.append(_exposure_summary(all_game_data, bankroll, scaled))

    message_lines.append(
        "\n⚠️ Esto es un modelo estadistico, no una garantia. "
        "Jugá responsable: fijate un límite antes de apostar y respétalo pase lo que pase."
    )
    full_message = "\n".join(message_lines)

    sent = telegram_bot.send_message(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, full_message)
    print(f"Mensaje enviado a Telegram: {sent}")

    # 6b) Segundo mensaje: solo los picks con monto sugerido, bien resumido
    if bankroll:
        summary_message = _render_summary_message(all_game_data, bankroll, today)
        sent_summary = telegram_bot.send_message(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, summary_message)
        print(f"Resumen de apuestas enviado a Telegram: {sent_summary}")

    # 7) Guardar historial
    for data in all_game_data:
        for pick in data["picks"]:
            storage.record_pick(history, pick, data["game"]["game_pk"], today)
    storage.save_history(history)

    print("Listo.")


def _compute_game(game, odds_events, calibration):
    """Calcula proyecciones y picks (moneyline + total) de un juego, sin
    montos de apuesta todavia -- eso se calcula despues, en conjunto con
    todos los juegos del dia, para poder aplicar el tope de exposicion
    diaria total."""
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

    home_proj = model_module.project_team_runs(
        home_season, home_recent, away_pitcher,
        is_home=True, opp_team_era=away_season.get("team_era"),
    )
    away_proj = model_module.project_team_runs(
        away_season, away_recent, home_pitcher,
        is_home=False, opp_team_era=home_season.get("team_era"),
    )

    if home_proj is None or away_proj is None:
        return None  # datos insuficientes (ej: inicio de temporada), nos lo saltamos

    total_proj = home_proj + away_proj
    sample_size = min(
        home_recent.get("sample") or 0,
        away_recent.get("sample") or 0,
    )

    odds_event = odds_data.match_game_odds(odds_events, game["home_team_name"], game["away_team_name"])
    home_ml_price = odds_data.best_moneyline(odds_event, "home") if odds_event else None
    away_ml_price = odds_data.best_moneyline(odds_event, "away") if odds_event else None
    total_info = odds_data.best_total(odds_event) if odds_event else None

    picks = []

    # --- Moneyline ---
    diff = home_proj - away_proj
    home_prob_raw = 1 / (1 + pow(2.71828, -0.5 * diff))
    home_prob_raw = model_module.apply_calibration(home_prob_raw, "moneyline", calibration)
    away_prob_raw = 1 - home_prob_raw

    home_implied_raw = odds_data.american_to_implied_prob(home_ml_price)
    away_implied_raw = odds_data.american_to_implied_prob(away_ml_price)
    home_implied_fair, away_implied_fair = odds_data.remove_vig_two_way(home_implied_raw, away_implied_raw)

    if home_prob_raw >= away_prob_raw:
        pick = model_module.build_pick(
            "moneyline",
            f"{game['home_team_name']} gana (ML)",
            home_prob_raw, home_implied_fair, sample_size,
            extra={"side": "home", "price": home_ml_price},
        )
    else:
        pick = model_module.build_pick(
            "moneyline",
            f"{game['away_team_name']} gana (ML)",
            away_prob_raw, away_implied_fair, sample_size,
            extra={"side": "away", "price": away_ml_price},
        )
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
        pick2 = model_module.build_pick(
            "total_over", f"Over {line}", over_prob, over_implied_fair, sample_size,
            extra={"line": line, "price": total_info["over_price"] if total_info else None},
        )
    else:
        pick2 = model_module.build_pick(
            "total_under", f"Under {line}", under_prob, under_implied_fair, sample_size,
            extra={"line": line, "price": total_info["under_price"] if total_info else None},
        )
    picks.append(pick2)

    return {
        "game": game,
        "home_proj": home_proj,
        "away_proj": away_proj,
        "picks": picks,
    }


def _apply_stake_sizing(all_game_data, bankroll):
    """Calcula Kelly fraccionado por pick (ya topado individualmente en
    model.MAX_STAKE_PCT_OF_BANKROLL) y despues, si la suma de TODOS los
    picks del dia pasa DAILY_EXPOSURE_CAP_PCT, escala todos los montos
    hacia abajo proporcionalmente para que la suma nunca pase ese tope.
    Regresa True si hubo que escalar."""
    if not bankroll:
        for data in all_game_data:
            for p in data["picks"]:
                p["stake_pct"] = 0.0
                p["stake_amount"] = None
        return False

    raw_total_pct = 0.0
    for data in all_game_data:
        for p in data["picks"]:
            price = p["extra"].get("price")
            pct = model_module.kelly_fraction(p["model_prob"], price, KELLY_FRACTION) if price is not None else 0.0
            p["_raw_stake_pct"] = pct
            raw_total_pct += pct

    scale = 1.0
    scaled = False
    if raw_total_pct > DAILY_EXPOSURE_CAP_PCT and raw_total_pct > 0:
        scale = DAILY_EXPOSURE_CAP_PCT / raw_total_pct
        scaled = True

    for data in all_game_data:
        for p in data["picks"]:
            final_pct = p.pop("_raw_stake_pct") * scale
            if final_pct > 0:
                p["stake_pct"] = round(final_pct, 4)
                p["stake_amount"] = round(bankroll * final_pct, 2)
            else:
                p["stake_pct"] = 0.0
                p["stake_amount"] = None

    return scaled


def _render_game_block(data):
    game = data["game"]
    home_proj = data["home_proj"]
    away_proj = data["away_proj"]
    picks = data["picks"]

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

    return "\n".join(lines)


def _exposure_summary(all_game_data, bankroll, scaled):
    total_stake_amount = sum(
        p["stake_amount"] for data in all_game_data for p in data["picks"] if p.get("stake_amount")
    )
    pct_of_bankroll = total_stake_amount / bankroll if bankroll else 0
    lines = [
        f"\n💰 <b>Exposición sugerida hoy:</b> ${total_stake_amount:,.2f} "
        f"({pct_of_bankroll*100:.1f}% de tu bankroll de ${bankroll:,.2f})"
    ]
    if scaled:
        lines.append(
            f"ℹ️ Los montos se escalaron porque la suma cruda pasaba el tope diario de "
            f"{DAILY_EXPOSURE_CAP_PCT*100:.0f}% de tu bankroll -- así no te sugiere apostar más de lo razonable en un solo día."
        )
    return "\n".join(lines)


def _render_summary_message(all_game_data, bankroll, today):
    """Segundo mensaje, corto: solo los picks que sí tienen monto sugerido
    y cuánto meterle a cada uno -- para no tener que releer el reporte
    completo para saber qué apostar."""
    lines = [f"📋 <b>Picks de hoy - {today}</b>\n"]
    total = 0.0
    any_pick = False

    for data in all_game_data:
        game = data["game"]
        matchup = f"{game['away_team_name']} @ {game['home_team_name']}"
        for p in data["picks"]:
            if not p.get("stake_amount"):
                continue
            any_pick = True
            total += p["stake_amount"]
            risk_emoji = p["risk"].split()[0]
            lines.append(
                f"{risk_emoji} <b>{p['selection']}</b> ({matchup}) — ${p['stake_amount']:,.2f} "
                f"({p['stake_pct']*100:.1f}%)"
            )

    if not any_pick:
        lines.append("Ningún pick tuvo edge suficiente hoy para sugerir apuesta.")
    else:
        lines.append(f"\n💰 <b>Total a meter hoy: ${total:,.2f}</b> ({total/bankroll*100:.1f}% de tu bankroll)")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
