"""
Punto de entrada del bot MULTI-PAR (MT5) — opera 3 pares cointegrados en D1.

Uso:
    python main_multipair.py
"""
from __future__ import annotations

import mt5_client as mc
from config import CONFIG
from multi_pair_trader import PAIRS, MultiPairTrader, pair_tag
from pairs_telegram import build_pairs_application, notify


async def _on_start(app):
    acc = mc.account_info()
    pares = ", ".join(pair_tag(a, b) for a, b, _ in PAIRS)
    await notify(
        app,
        f"🚀 Bot MULTI-PAR iniciado\n"
        f"Cuenta {acc['login']} @ {acc['server']} "
        f"({'DEMO' if acc['is_demo'] else 'REAL'})\n"
        f"Pares ({CONFIG.pairs_timeframe}): {pares}\n"
        f"Balance {acc['balance']:.2f} {acc['currency']}\n"
        f"Escribe /status para ver el estado.",
    )


def main() -> None:
    problems = CONFIG.validate()
    if problems:
        raise SystemExit("Config incompleta:\n- " + "\n- ".join(problems))

    mc.connect()
    acc = mc.account_info()
    pares = ", ".join(pair_tag(a, b) for a, b, _ in PAIRS)
    print(f"Conectado: {acc['login']} @ {acc['server']} | balance {acc['balance']} {acc['currency']}")
    print(f"Timeframe: {CONFIG.pairs_timeframe} | Pares: {pares}")
    if CONFIG.signals_only:
        print("📢 MODO SEÑALES: postea las señales al chat/canal, NO opera.")
    elif acc["is_demo"]:
        print("🧪 Cuenta DEMO: operará automáticamente.")
    elif CONFIG.pairs_allow_real:
        print("✅ Cuenta REAL con PAIRS_ALLOW_REAL=true: OPERARÁ automáticamente cuando haya señal.")
    else:
        print("⚠️  Cuenta REAL sin permiso: solo avisará. Pon PAIRS_ALLOW_REAL=true en el .env para operar.")

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
