"""
Punto de entrada del bot SESSION BREAKER (MT5) — capa de VOLUMEN, 1 cuenta.

Opera ruptura de rango de sesión (Londres + NY) en varios símbolos, en H1, con
control por Telegram. Para MUCHAS cuentas usa run_sb_headless.py + launch_multi.py.

Uso:
    python main_session_breaker.py

OJO: un solo poller de Telegram por token. Para varias cuentas usa el modo
headless (send-only) con launch_multi.py.
"""
from __future__ import annotations

import mt5_client as mc
from config import CONFIG
from sb_telegram import build_sb_application, notify
from session_breaker import SessionBreakerTrader


async def _on_start(app):
    acc = mc.account_info()
    trader = app.bot_data["trader"]
    await notify(
        app,
        f"⚡ Bot SESSION BREAKER iniciado\n"
        f"Cuenta {acc['login']} @ {acc['server']} "
        f"({'DEMO' if acc['is_demo'] else 'REAL'})\n"
        f"{len(trader.symbols)} símbolos × {len(trader.sessions)} sesiones (H1)\n"
        f"Lote {trader.state.lot} | Balance {acc['balance']:.2f} {acc['currency']}\n"
        f"Escribe /status para ver el estado.",
    )


def main() -> None:
    if not CONFIG.mt5_login or not CONFIG.tg_bot_token or not CONFIG.tg_chat_id:
        raise SystemExit("Config incompleta: revisa MT5_LOGIN/PASSWORD/SERVER y TG_BOT_TOKEN/TG_CHAT_ID en el .env")

    mc.connect()
    acc = mc.account_info()
    print(f"Conectado: {acc['login']} @ {acc['server']} | balance {acc['balance']} {acc['currency']}")
    if not acc["is_demo"]:
        print("⚠️  Cuenta NO demo: no abrirá rupturas automáticamente en real.")

    trader = SessionBreakerTrader()
    trader.prime()
    app = build_sb_application(trader)
    app.post_init = _on_start

    try:
        print("Bot SESSION BREAKER corriendo. Ctrl+C para salir.")
        app.run_polling(allowed_updates=["message"])
    finally:
        mc.shutdown()


if __name__ == "__main__":
    main()
