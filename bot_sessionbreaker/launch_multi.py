"""
Lanzador MULTI-CUENTA del Session Breaker.

Levanta una instancia headless (run_sb_headless.py) por cada archivo en
accounts/*.env, y las vigila: si alguna se cae, la reinicia con backoff.

Cada archivo accounts/<nombre>.env define una cuenta (MT5_LOGIN/PASSWORD/SERVER,
MT5_PATH del terminal de esa cuenta, y el Telegram compartido). La etiqueta de
cada cuenta en Telegram es el nombre del archivo (ej. cuenta01).

Uso:
    python launch_multi.py

Requisitos:
  - Un terminal MT5 (instalación portable) por cuenta; MT5_PATH apunta a su
    terminal64.exe en cada .env.
  - Recursos: ~0.45 GB de RAM por cuenta (terminal + bot). 16 GB aguantan ~12.
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
ACCOUNTS_DIR = BASE / "accounts"
STAGGER_SECONDS = 4      # separar arranques para no saturar CPU/red
MONITOR_SECONDS = 15     # cada cuánto revisar procesos
RESTART_BACKOFF = 10     # espera antes de reiniciar una caída


def _start(env_file: str) -> subprocess.Popen:
    label = Path(env_file).stem
    env = os.environ.copy()
    env["ENV_FILE"] = f"accounts/{Path(env_file).name}"
    env["ACCOUNT_LABEL"] = label
    p = subprocess.Popen([sys.executable, "run_sb_headless.py"], cwd=str(BASE), env=env)
    print(f"[launch] {label} -> PID {p.pid}")
    return p


def main() -> None:
    env_files = sorted(glob.glob(str(ACCOUNTS_DIR / "*.env")))
    # ignora la plantilla de ejemplo
    env_files = [f for f in env_files if Path(f).stem.upper() != "EXAMPLE"]
    if not env_files:
        raise SystemExit("No hay cuentas en accounts/*.env (copia accounts/EXAMPLE.env "
                         "a accounts/cuenta01.env y edítalo).")

    print(f"[launch] {len(env_files)} cuenta(s): {[Path(f).stem for f in env_files]}")
    procs: dict[str, subprocess.Popen] = {}
    for ef in env_files:
        procs[ef] = _start(ef)
        time.sleep(STAGGER_SECONDS)

    try:
        while True:
            time.sleep(MONITOR_SECONDS)
            for ef, p in list(procs.items()):
                if p.poll() is not None:  # el proceso murió
                    print(f"[launch] {Path(ef).stem} cayó (code {p.returncode}); "
                          f"reinicio en {RESTART_BACKOFF}s…")
                    time.sleep(RESTART_BACKOFF)
                    procs[ef] = _start(ef)
    except KeyboardInterrupt:
        print("[launch] cerrando todas las instancias…")
        for p in procs.values():
            p.terminate()
        for p in procs.values():
            try:
                p.wait(timeout=10)
            except Exception:
                p.kill()


if __name__ == "__main__":
    main()
