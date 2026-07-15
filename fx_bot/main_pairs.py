"""
Punto de entrada del bot de PAIRS (arbitraje estadístico EURUSD/GBPUSD).

Arranca: valida config -> conecta MT5 -> lanza el bot de Telegram con el loop
diario de pairs en demo.

Uso:
    python main_pairs.py
"""
from __future__ import annotations

import mt5_client as mc
from config import CONFIG
from pairs_telegram import build_pairs_application, notify
from pairs_trader import PairsTrader


async def _on_start(app):
    acc = mc.account_info()
    await notify(
        app,
        f"🚀 Bot PAIRS iniciado\n"
        f"Cuenta {acc['login']} @ {acc['server']} "
        f"({'DEMO' if acc['is_demo'] else 'REAL'})\n"
        f"{CONFIG.pair_a}/{CONFIG.pair_b} D1 | balance {acc['balance']:.2f} {acc['currency']}\n"
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
        print("⚠️  Cuenta NO demo: el bot no abrirá pares automáticamente en real.")

    trader = PairsTrader()
    trader.prime()  # opera solo desde la próxima vela D1
    app = build_pairs_application(trader)
    app.post_init = _on_start

    try:
        print("Bot PAIRS corriendo. Ctrl+C para salir.")
        app.run_polling(allowed_updates=["message"])
    finally:
        mc.shutdown()


if __name__ == "__main__":
    main()
