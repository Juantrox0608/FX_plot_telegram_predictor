"""
Punto de entrada del bot MULTI-PAR (MT5) — opera 3 pares cointegrados en D1.

Uso:
    python main_multipair.py
"""
from __future__ import annotations

import mt5_client as mc
from config import CONFIG, DATA_DIR
from pairs_safety import InstanceLock, guard_account
from multi_pair_trader import PAIRS, MultiPairTrader, pair_tag
from pairs_telegram import build_pairs_application, notify


async def _on_start(app):
    acc = mc.account_info()
    pares = ", ".join(pair_tag(a, b) for a, b, _ in PAIRS)
    await notify(
        app,
        f"🚀 Bot MULTI-PAR iniciado\n"
        f"Instancia {CONFIG.instance_id}: cuenta/servidor verificados "
        f"({'DEMO' if acc['is_demo'] else 'REAL'})\n"
        f"Pares ({CONFIG.pairs_timeframe}): {pares}\n"
        f"Balance {acc['balance']:.2f} {acc['currency']}\n"
        f"Escribe /status para ver el estado.",
    )


def main() -> None:
    problems = CONFIG.validate()
    if problems:
        raise SystemExit("Config incompleta:\n- " + "\n- ".join(problems))

    with InstanceLock(DATA_DIR, CONFIG.mt5_path):
        try:
            mc.connect()
            guard_account(mc.mt5)
            acc = mc.account_info()
            # Solo etiqueta de instancia y modo; no datos privados en los logs.
            print(f"Instancia {CONFIG.instance_id}: {'DEMO' if acc['is_demo'] else 'REAL'}; cuenta y servidor verificados")
            trader = MultiPairTrader()
            trader.prime()
            app = build_pairs_application(trader)
            app.post_init = _on_start
            app.run_polling(allowed_updates=["message"])
        finally:
            mc.shutdown()


if __name__ == "__main__":
    main()
