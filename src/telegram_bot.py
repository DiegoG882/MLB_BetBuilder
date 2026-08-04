"""
telegram_bot.py
Manda el mensaje diario al chat de Telegram via la Bot API oficial.
"""

import time

import requests

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2


def send_message(token, chat_id, text):
    if not token or not chat_id:
        print("[telegram_bot] Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID, no se envio nada.")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # Telegram limita ~4096 caracteres por mensaje, partimos si hace falta
    chunks = _split_message(text, 3800)
    ok = True
    for chunk in chunks:
        if not _send_with_retry(url, chat_id, chunk):
            ok = False
    return ok


def _send_with_retry(url, chat_id, chunk):
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
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
            return True
        except requests.RequestException as e:
            last_error = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    print(f"[telegram_bot] ERROR enviando mensaje tras {MAX_RETRIES} intentos -> {last_error}")
    return False


def _split_message(text, max_len):
    if len(text) <= max_len:
        return [text]
    parts = []
    while text:
        parts.append(text[:max_len])
        text = text[max_len:]
    return parts
