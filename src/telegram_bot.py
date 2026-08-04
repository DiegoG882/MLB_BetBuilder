"""
telegram_bot.py
Manda el mensaje diario al chat de Telegram via la Bot API oficial.
"""

import requests


def send_message(token, chat_id, text):
    if not token or not chat_id:
        print("[telegram_bot] Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID, no se envio nada.")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # Telegram limita ~4096 caracteres por mensaje, partimos si hace falta
    chunks = _split_message(text, 3800)
    ok = True
    for chunk in chunks:
        try:
            resp = requests.post(
                url,
                data={
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"[telegram_bot] ERROR enviando mensaje -> {e}")
            ok = False
    return ok


def _split_message(text, max_len):
    if len(text) <= max_len:
        return [text]
    parts = []
    while text:
        parts.append(text[:max_len])
        text = text[max_len:]
    return parts
