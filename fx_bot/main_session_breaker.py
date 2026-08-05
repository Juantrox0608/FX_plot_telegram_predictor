"""
Punto de entrada del bot SESSION BREAKER (MT5) — capa de VOLUMEN.

Opera ruptura de rango de sesión (Londres + NY) en varios símbolos, en H1.
Genera muchas operaciones (~2.700/año con 6 símbolos × 2 sesiones). Break-even
en trading; su rentabilidad viene del REBATE por lote (usa cuenta ECN de spread
bajo). Corre junto a la base D1 rentable.

Uso:
    python main_session_breaker.py

OJO: un solo poller de Telegram por token. Si ya corres otro bot (multi-par),
usa un TG_BOT_TOKEN distinto para este, o córrelo en otra cuenta/terminal.
"""
from __future__ import annotations

import mt5_client as mc
from config import CONFIG
from pairs_telegram import build_pairs_application, notify
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
    problems = CONFIG.validate()
    if problems:
        raise SystemExit("Config incompleta:\n- " + "\n- ".join(problems))

    mc.connect()
    acc = mc.account_info()
    print(f"Conectado: {acc['login']} @ {acc['server']} | balance {acc['balance']} {acc['currency']}")
    if not acc["is_demo"]:
        print("⚠️  Cuenta NO demo: no abrirá rupturas automáticamente en real.")

    trader = SessionBreakerTrader()
    trader.prime()
    app = build_pairs_application(trader)
    app.post_init = _on_start

    try:
        print("Bot SESSION BREAKER corriendo. Ctrl+C para salir.")
        app.run_polling(allowed_updates=["message"])
    finally:
        mc.shutdown()


if __name__ == "__main__":
    main()
