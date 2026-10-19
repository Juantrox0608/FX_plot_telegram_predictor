"""Prueba de Telegram desde la consola: manda un mensaje al chat del cliente con el bot configurado.

Uso (desde la carpeta del bot):
    $env:ENV_FILE = 'C:/Cuanti/cliente_01/secrets/cliente.env'; .venv\\Scripts\\python probar_telegram.py
No muestra el token. No toca MT5 ni abre operaciones.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

from config import CONFIG

HINTS = {
    401: "El token no es válido: revisa TG_BOT_TOKEN (cópialo de nuevo desde @BotFather).",
    400: "El chat no existe para este bot: el cliente debe escribirle /start al bot primero, y revisa TG_CHAT_ID.",
    403: "El cliente bloqueó al bot o nunca le escribió: que abra el bot y le mande /start.",
}


def send(token: str, chat_id: int, text: str, timeout: float = 15.0) -> tuple[bool, str]:
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout) as r:
            body = json.load(r)
        return bool(body.get("ok")), "Mensaje enviado. Revisa el Telegram del cliente."
    except urllib.error.HTTPError as e:
        return False, HINTS.get(e.code, f"Telegram respondió con error {e.code}.")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return False, f"Sin conexión a Telegram desde este equipo ({type(e).__name__})."


def main() -> int:
    if not CONFIG.tg_bot_token or not CONFIG.tg_chat_id:
        print("Faltan TG_BOT_TOKEN o TG_CHAT_ID: revisa ENV_FILE y el archivo cliente.env.")
        return 2
    text = (f"✅ Prueba de conexión: bot de pares {CONFIG.instance_id or ''} listo.\n"
            f"Pares: {CONFIG.pairs_list} · {CONFIG.pairs_timeframe}\n"
            f"Cuenta real permitida: {'sí' if CONFIG.pairs_allow_real else 'no (solo demo)'} · "
            f"Cuenta pequeña: {'sí' if CONFIG.pairs_small_account else 'no'} · "
            f"Freno diario: {CONFIG.daily_max_loss_percent:g} %")
    ok, msg = send(CONFIG.tg_bot_token, CONFIG.tg_chat_id, text)
    print(("OK: " if ok else "ERROR: ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
