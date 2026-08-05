# Bot MULTI-PAR (Pairs) — MT5

Arbitraje estadístico (z-score del spread) sobre pares cointegrados en **D1**.
Edge real, pocas operaciones (~22/año), drawdown bajo. Market-neutral.

Pares por defecto: EUR/GBP, AUD/NZD, USDCHF/USDCAD.

## Instalar (en la VPS Windows con MT5)

```bash
cd bot_pairs
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env    # y edita .env con tus datos
```

Abre MT5, inicia sesión en la cuenta demo y activa **Algo Trading** (botón verde).

## Correr

```bash
python main_multipair.py     # multi-par (EUR/GBP + AUD/NZD + USDCHF/USDCAD)
# o
python main_pairs.py         # un solo par
```

## Telegram

`/status` `/positions` `/stats` `/closeall` `/stop` `/resume` `/cap` `/dailyrisk`

El `/status` muestra la **fecha de la última vela D1**: si lleva ≥3 días sin
cambiar, es que MT5 perdió la conexión con el bróker (no un bug del bot).

## Nota

Un solo bot por token de Telegram. Si corres también el Session Breaker, usa
un token distinto.
