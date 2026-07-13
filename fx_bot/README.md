# FX Bot V2 — Python + MT5 + Telegram + IA

Reescritura del predictor FX en Python puro. Descarga histórico de EURUSD desde
MetaTrader 5, decide señales con una IA (GRU) alimentada por datos de mercado e
indicadores, gestiona el riesgo (1–5% con SL/TP por ATR) y se controla por
Telegram. Arranca **siempre en cuenta demo**.

> ⚠️ **Requiere Windows**: la librería `MetaTrader5` de Python solo funciona en
> Windows con el terminal MT5 instalado y abierto.

## Estado (Fase 1 — andamiaje)

| Archivo | Estado | Qué hace |
|---|---|---|
| `config.py` | ✅ | Carga `.env`, valida parámetros. |
| `mt5_client.py` | ✅ | Conexión MT5 + descarga de histórico. Órdenes: pendiente. |
| `indicators.py` | ✅ | TMA, RSI, MACD, Bollinger, ATR (verificados). |
| `model/` | ⏳ | Dataset + GRU + entrenamiento (Fase 3). |
| `strategy.py` | ⏳ | Combina IA + indicadores + noticias (Fase 3–4). |
| `risk.py` | ⏳ | Tamaño de lote por %riesgo y SL(ATR) (Fase 4). |
| `executor.py` | ⏳ | Ejecución híbrida demo/real (Fase 4). |
| `news.py` | ⏳ | Calendario económico (Fase 6). |
| `telegram_bot.py` | ⏳ | Comandos, alertas, cierre de emergencia (Fase 5). |
| `main.py` | ⏳ | Orquestación (Fase 7). |

## Puesta en marcha

1. Instala el **terminal MetaTrader 5** y abre sesión en una **cuenta demo**.
2. Crea el entorno e instala dependencias:
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
3. Copia `.env.example` a `.env` y rellena tus valores (login MT5, servidor,
   token de Telegram de @BotFather, tu chat ID). **No subas `.env` al repo.**
4. Prueba la descarga de datos:
   ```powershell
   python mt5_client.py
   ```

## Seguridad

- Arranca en **demo**; pasar a real es explícito y con confirmación por Telegram.
- Tope de riesgo por operación 1–5% y **kill-switch de pérdida diaria** modificable.
- Comando de **cierre de emergencia** siempre disponible.
- Credenciales solo en `.env` local (ignorado por git).
