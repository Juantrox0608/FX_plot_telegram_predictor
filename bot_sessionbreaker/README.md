# Bot SESSION BREAKER — MT5

Ruptura de rango de sesión (Londres + NY) en **H1**, varios símbolos. Genera
**mucho volumen** (~2.700 operaciones/año con 6 símbolos × 2 sesiones).

> ⚠️ **Honestidad:** en forex el trading es ~break-even (no tiene edge por sí
> solo). Su rentabilidad viene del **rebate por lote**: solo gana si
> `rebate > spread + ~1 USD`. Úsalo en **cuenta ECN/raw de spread bajo**.

## Instalar (VPS Windows con MT5)

```bash
cd bot_sessionbreaker
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env    # edita .env
```

Abre MT5, inicia sesión y activa **Algo Trading** (botón verde).

## Correr — 1 cuenta (con Telegram interactivo)

```bash
python main_session_breaker.py
```

Telegram: `/status` `/positions` `/stats` `/closeall` `/stop` `/resume` `/lot` `/dailyrisk`

## Correr — MUCHAS cuentas (headless, un token compartido)

1. Un **terminal MT5 portable por cuenta**; anota la ruta de cada `terminal64.exe`.
2. Copia `accounts/EXAMPLE.env` a `accounts/cuenta01.env`, `cuenta02.env`, … y
   edita cada uno (MT5 de esa cuenta + `MT5_PATH` de su terminal + el Telegram compartido).
3. Lanza todas:

```bash
python launch_multi.py
```

El lanzador arranca una instancia por cuenta, las vigila y reinicia si alguna
se cae. Cada cuenta reporta a Telegram etiquetada con el nombre de su archivo.

**Recursos:** ~0.45 GB RAM por cuenta (terminal + bot). 16 GB / 4 vCPU ≈ 12 cuentas.

## Las sesiones

`SB_SESSIONS=0-7:8-12,9-13:13-17` → "rango:trade" en **hora del servidor MT5**.
Calíbralo por bróker (cada uno tiene su zona horaria de servidor).
