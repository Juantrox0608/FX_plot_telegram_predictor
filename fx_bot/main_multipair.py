"""
Punto de entrada del bot MULTI-PAR (MT5) — opera 3 pares cointegrados en D1.

Uso:
    python main_multipair.py
"""
from __future__ import annotations

import mt5_client as mc
from config import CONFIG
from demo_license import ensure_demo_active
from multi_pair_trader import PAIRS, MultiPairTrader
from pairs_telegram import build_pairs_application, notify


async def _on_start(app):
    acc = mc.account_info()
    demo = ensure_demo_active()
    pares = ", ".join(f"{a[:3]}/{b[:3]}" for a, b, _ in PAIRS)
    await notify(
        app,
        f"🚀 Bot MULTI-PAR iniciado\n"
        f"Cuenta {acc['login']} @ {acc['server']} "
        f"({'DEMO' if acc['is_demo'] else 'REAL'})\n"
        f"Pares (D1): {pares}\n"
        f"Balance {acc['balance']:.2f} {acc['currency']}\n"
        f"{demo.summary()}\n"
        f"Escribe /status para ver el estado.",
    )


def main() -> None:
    demo = ensure_demo_active()
    print(demo.summary())

    problems = CONFIG.validate()
    if problems:
        raise SystemExit("Config incompleta:\n- " + "\n- ".join(problems))

    mc.connect()
    acc = mc.account_info()
    print(f"Conectado: {acc['login']} @ {acc['server']} | balance {acc['balance']} {acc['currency']}")
    if not acc["is_demo"]:
        print("⚠️  Cuenta NO demo: no abrirá pares automáticamente en real.")

    trader = MultiPairTrader()
    trader.prime()
    app = build_pairs_application(trader)
    app.post_init = _on_start

    try:
        print("Bot MULTI-PAR corriendo. Ctrl+C para salir.")
        app.run_polling(allowed_updates=["message"])
    finally:
        mc.shutdown()


if __name__ == "__main__":
    main()
