"""
Utilidad para descubrir tu TG_CHAT_ID.

Uso:
  1) Rellena TG_BOT_TOKEN en .env.
  2) En Telegram, envíale CUALQUIER mensaje a tu bot.
  3) Ejecuta:  python get_chat_id.py
  4) Copia el 'chat id' que imprime a TG_CHAT_ID en tu .env.

Usa solo librería estándar (no requiere instalar nada).
"""
from __future__ import annotations

import json
import urllib.request

from config import CONFIG


def main() -> None:
    token = CONFIG.tg_bot_token
    if not token:
        print("❌ Falta TG_BOT_TOKEN en .env")
        return

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"❌ Error consultando Telegram: {e}")
        return

    if not data.get("ok"):
        print(f"❌ Telegram respondió error: {data}")
        return

    results = data.get("result", [])
    if not results:
        print("⚠️  No hay mensajes recientes. Envíale un mensaje a tu bot y "
              "vuelve a ejecutar este script.")
        return

    seen: dict[int, str] = {}
    for upd in results:
        msg = upd.get("message") or upd.get("edited_message") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is not None and cid not in seen:
            who = chat.get("username") or chat.get("first_name") or chat.get("title") or "?"
            seen[cid] = who

    if not seen:
        print("⚠️  Recibí updates pero sin chats. Envía un mensaje de texto normal al bot.")
        return

    print("✅ Chats detectados (usa el tuyo como TG_CHAT_ID):")
    for cid, who in seen.items():
        print(f"   chat id = {cid}   ({who})")


if __name__ == "__main__":
    main()
