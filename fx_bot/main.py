"""
Punto de entrada del bot FX V2.

Arranca: valida config -> conecta MT5 -> carga modelo -> lanza el bot de
Telegram con el loop de trading en demo.

Uso:
    python main.py
Detener: Ctrl+C (o /stop en Telegram para pausar solo el trading).
"""
from __future__ import annotations

import mt5_client as mc
from config import CONFIG
from telegram_bot import build_application, notify
from trader import Trader


async def _on_start(app):
    acc = mc.account_info()
    await notify(
        app,
        f"🚀 Bot FX V2 iniciado\n"
        f"Cuenta {acc['login']} @ {acc['server']} "
        f"({'DEMO' if acc['is_demo'] else 'REAL'})\n"
        f"Balance {acc['balance']:.2f} {acc['currency']} | "
        f"{CONFIG.symbol} {CONFIG.timeframe} | riesgo {CONFIG.risk_percent}%\n"
        f"Escribe /status para ver el estado.",
    )


def main() -> None:
    problems = CONFIG.validate()
    if problems:
        raise SystemExit("Config incompleta:\n- " + "\n- ".join(problems))

    mc.connect()
    acc = mc.account_info()
    if not acc["is_demo"]:
        print("⚠️  ATENCIÓN: la cuenta NO es demo. El bot no abrirá operaciones "
              "automáticamente en cuenta real.")
    print(f"Conectado: {acc['login']} @ {acc['server']} | balance {acc['balance']} {acc['currency']}")

    trader = Trader(CONFIG.symbol)
    trader.prime()  # opera solo desde la próxima vela H1 que cierre
    app = build_application(trader)
    app.post_init = _on_start

    try:
        print("Bot corriendo. Ctrl+C para salir.")
        app.run_polling(allowed_updates=["message"])
    finally:
        mc.shutdown()


if __name__ == "__main__":
    main()
