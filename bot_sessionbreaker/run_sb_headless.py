"""
Runner HEADLESS del Session Breaker — para correr MUCHAS cuentas a la vez.

Sin poller de Telegram (no recibe comandos): solo EJECUTA el ciclo y ENVÍA
alertas a un chat por HTTP. Así 12 instancias pueden compartir el MISMO token
(el conflicto de getUpdates solo ocurre al hacer polling, no al enviar).

Cada instancia carga su propio .env vía la variable de entorno ENV_FILE, y se
etiqueta con ACCOUNT_LABEL. Las lanza y vigila launch_multi.py.

Uso directo (una cuenta):
    ENV_FILE=accounts/cuenta01.env ACCOUNT_LABEL=cuenta01 python run_sb_headless.py
"""
from __future__ import annotations

import os
import time

import requests

import mt5_client as mc
from config import CONFIG
from session_breaker import SessionBreakerTrader

LABEL = os.getenv("ACCOUNT_LABEL", "cuenta")
NOTIFY_TYPES = {"opened", "closed", "killswitch", "error", "confirm"}
LOOP_SECONDS = 60


def tg_send(text: str) -> None:
    """Envío directo por HTTP (sin polling). Silencioso ante errores de red."""
    if not CONFIG.tg_bot_token or not CONFIG.tg_chat_id:
        return
    url = f"https://api.telegram.org/bot{CONFIG.tg_bot_token}/sendMessage"
    for _ in range(3):
        try:
            r = requests.post(url, json={"chat_id": CONFIG.tg_chat_id,
                                         "text": f"[{LABEL}] {text}"}, timeout=15)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(3)


def main() -> None:
    if not CONFIG.mt5_login:
        raise SystemExit(f"[{LABEL}] Config incompleta: falta MT5_LOGIN en {os.getenv('ENV_FILE', '.env')}")

    mc.connect()
    acc = mc.account_info()
    print(f"[{LABEL}] Conectado: {acc['login']} @ {acc['server']} | balance {acc['balance']} {acc['currency']}")
    trader = SessionBreakerTrader()
    trader.prime()
    tg_send(f"⚡ SB iniciado @ {acc['login']} "
            f"({'DEMO' if acc['is_demo'] else 'REAL'}) | "
            f"{len(trader.symbols)}x{len(trader.sessions)} | lote {trader.state.lot} | "
            f"balance {acc['balance']:.2f} {acc['currency']}")

    try:
        while True:
            try:
                for ev in trader.check():
                    if ev["type"] in NOTIFY_TYPES:
                        tg_send(ev["text"])
            except Exception as e:
                print(f"[{LABEL}] error en ciclo: {e}")
                tg_send(f"❌ error en ciclo: {e}")
            time.sleep(LOOP_SECONDS)
    except KeyboardInterrupt:
        print(f"[{LABEL}] detenido por el usuario.")
    finally:
        mc.shutdown()


if __name__ == "__main__":
    main()
