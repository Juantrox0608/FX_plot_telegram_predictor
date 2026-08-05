"""
Capa de Telegram: comandos de control + alertas + loop programado que revisa
cada vela H1 y opera en demo.

Comandos:
  /start /help          — ayuda
  /status               — estado del bot, cuenta y última señal
  /positions            — posiciones abiertas
  /closeall             — 🚨 cierre de emergencia de todo
  /stop  /resume        — pausar / reanudar el trading (el bot sigue vivo)
  /risk <1-5>           — fijar % de riesgo base
  /dailyrisk <n>        — fijar % de pérdida diaria del kill-switch
"""
from __future__ import annotations

import asyncio
import functools

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import NetworkError, TimedOut
from telegram.ext import Application, CommandHandler, ContextTypes

from config import CONFIG
from executor import close_all
from trader import Trader

CHECK_INTERVAL = 60  # segundos entre revisiones


def _authorized(func):
    """Solo el chat configurado puede dar órdenes."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat is None or update.effective_chat.id != CONFIG.tg_chat_id:
            return
        return await func(update, context)
    return wrapper


def _trader(context: ContextTypes.DEFAULT_TYPE) -> Trader:
    return context.application.bot_data["trader"]


async def notify(app: Application, text: str) -> None:
    """Envía un mensaje reintentando ante errores de red (Bad Gateway/timeout).

    Los 502 de Telegram son transitorios; reintentamos en silencio y, si aun así
    falla, no dejamos que la excepción tumbe el ciclo del bot.
    """
    for attempt in range(3):
        try:
            await app.bot.send_message(chat_id=CONFIG.tg_chat_id, text=text,
                                       parse_mode=ParseMode.MARKDOWN)
            return
        except (NetworkError, TimedOut):
            if attempt < 2:
                await asyncio.sleep(3 * (attempt + 1))
                continue
            return  # se pierde este aviso, pero el bot sigue vivo
        except Exception:
            # Probable problema de formato Markdown: reintenta como texto plano.
            try:
                await app.bot.send_message(chat_id=CONFIG.tg_chat_id, text=text)
            except Exception:
                pass
            return


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manejador global: traga los errores de red transitorios en silencio.

    Sin esto, un `Bad Gateway` de Telegram imprime un traceback enorme cada vez.
    Los errores de red se ignoran (Telegram se recupera solo); el resto se
    registra de forma compacta.
    """
    err = context.error
    if isinstance(err, (NetworkError, TimedOut)):
        return  # 502/timeout transitorio de Telegram: silencio
    print(f"[telegram] error no de red: {type(err).__name__}: {err}")


# ---------------- comandos ----------------
@_authorized
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Bot FX V2 en marcha.\n"
        "/status /positions /stats /closeall /stop /resume\n"
        "/risk /maxrisk /dailyrisk"
    )


@_authorized
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = await asyncio.to_thread(_trader(context).status_text)
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)


@_authorized
async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = await asyncio.to_thread(_trader(context).positions_text)
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)


@_authorized
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = await asyncio.to_thread(_trader(context).journal_text)
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)


@_authorized
async def cmd_closeall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    results = await asyncio.to_thread(close_all, _trader(context).symbol)
    if not results:
        await update.message.reply_text("No había posiciones que cerrar.")
        return
    ok = sum(1 for r in results if r.ok)
    await update.message.reply_text(f"🚨 Cierre de emergencia: {ok}/{len(results)} posiciones cerradas.")


@_authorized
async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _trader(context).state.running = False
    await update.message.reply_text("⏸️ Trading PAUSADO (no abrirá nuevas operaciones).")


@_authorized
async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _trader(context).state.running = True
    await update.message.reply_text("▶️ Trading REANUDADO.")


@_authorized
async def cmd_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = float(context.args[0])
        assert 1.0 <= val <= 5.0
    except (IndexError, ValueError, AssertionError):
        await update.message.reply_text("Uso: /risk <1-5>")
        return
    _trader(context).state.base_risk = val
    await update.message.reply_text(f"✅ Riesgo base fijado en {val}%.")


@_authorized
async def cmd_dailyrisk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = float(context.args[0])
        assert 0.5 <= val <= 50.0
    except (IndexError, ValueError, AssertionError):
        await update.message.reply_text("Uso: /dailyrisk <0.5-50>")
        return
    _trader(context).state.daily_max_loss = val
    await update.message.reply_text(f"✅ Kill-switch de pérdida diaria fijado en {val}%.")


@_authorized
async def cmd_maxrisk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fija el riesgo MÁXIMO por operación (el techo cuando los indicadores confirman)."""
    try:
        val = float(context.args[0])
        assert 1.0 <= val <= 5.0
    except (IndexError, ValueError, AssertionError):
        await update.message.reply_text("Uso: /maxrisk <1-5>")
        return
    st = _trader(context).state
    st.max_risk = val
    msg = f"✅ Riesgo máximo por operación fijado en {val}%."
    if st.base_risk > val:            # el base no puede superar al máximo
        st.base_risk = val
        msg += f"\n(Ajusté también el riesgo base a {val}%.)"
    await update.message.reply_text(msg)


# ---------------- loop programado ----------------
# Solo avisamos de eventos ACCIONABLES. Los repetitivos (sin señal, señal con
# posición ya abierta, pausado) NO se notifican para no llenar el chat cada vela;
# se consultan con /status y /positions.
NOTIFY_TYPES = {"opened", "closed", "killswitch", "error", "confirm", "news", "license"}


async def trading_job(context: ContextTypes.DEFAULT_TYPE):
    trader = context.application.bot_data["trader"]
    try:
        events = await asyncio.to_thread(trader.check)
    except Exception as e:
        await notify(context.application, f"❌ Error en el ciclo: {e}")
        return
    for ev in events:
        if ev["type"] not in NOTIFY_TYPES:
            continue
        await notify(context.application, ev["text"])


def build_application(trader: Trader) -> Application:
    app = (
        Application.builder()
        .token(CONFIG.tg_bot_token)
        .get_updates_read_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(30)
        .pool_timeout(30)
        .build()
    )
    app.bot_data["trader"] = trader
    app.add_error_handler(on_error)

    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("closeall", cmd_closeall))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("risk", cmd_risk))
    app.add_handler(CommandHandler("maxrisk", cmd_maxrisk))
    app.add_handler(CommandHandler("dailyrisk", cmd_dailyrisk))

    app.job_queue.run_repeating(trading_job, interval=CHECK_INTERVAL, first=10)
    return app
