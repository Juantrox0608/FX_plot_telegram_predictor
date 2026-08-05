"""
Capa de Telegram del SESSION BREAKER (autocontenida, sin dependencias del
stack de pairs ni IA). notify resiliente + manejador de errores + comandos.
"""
from __future__ import annotations

import asyncio
import functools

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import NetworkError, TimedOut
from telegram.ext import Application, CommandHandler, ContextTypes

from config import CONFIG
from session_breaker import SessionBreakerTrader

CHECK_INTERVAL = 60  # segundos

NOTIFY_TYPES = {"opened", "closed", "killswitch", "error", "confirm"}


def _authorized(func):
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat is None or update.effective_chat.id != CONFIG.tg_chat_id:
            return
        return await func(update, context)
    return wrapper


async def notify(app: Application, text: str) -> None:
    """Envía reintentando ante errores de red (Bad Gateway/timeout de Telegram)."""
    for attempt in range(3):
        try:
            await app.bot.send_message(chat_id=CONFIG.tg_chat_id, text=text,
                                       parse_mode=ParseMode.MARKDOWN)
            return
        except (NetworkError, TimedOut):
            if attempt < 2:
                await asyncio.sleep(3 * (attempt + 1))
                continue
            return
        except Exception:
            try:
                await app.bot.send_message(chat_id=CONFIG.tg_chat_id, text=text)
            except Exception:
                pass
            return


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, (NetworkError, TimedOut)):
        return
    print(f"[telegram] error no de red: {type(err).__name__}: {err}")


def _trader(context) -> SessionBreakerTrader:
    return context.application.bot_data["trader"]


@_authorized
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚡ Bot SESSION BREAKER en marcha.\n"
        "/status /positions /stats /closeall /stop /resume /lot /dailyrisk"
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
    res = await asyncio.to_thread(_trader(context).close_all)
    if not res:
        await update.message.reply_text("No había posiciones abiertas.")
        return
    ok = sum(1 for r in res if r.ok)
    await update.message.reply_text(f"🚨 Cierre de emergencia: {ok}/{len(res)} posiciones cerradas.")


@_authorized
async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _trader(context).state.running = False
    await update.message.reply_text("⏸️ Trading PAUSADO (no abrirá nuevas rupturas).")


@_authorized
async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _trader(context).state.running = True
    await update.message.reply_text("▶️ Trading REANUDADO.")


@_authorized
async def cmd_lot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = float(context.args[0])
        assert 0.01 <= val <= 100.0
    except (IndexError, ValueError, AssertionError):
        await update.message.reply_text("Uso: /lot <0.01-100>")
        return
    _trader(context).state.lot = val
    await update.message.reply_text(f"✅ Lote fijado en {val}.")


@_authorized
async def cmd_dailyrisk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = float(context.args[0])
        assert 0.5 <= val <= 50.0
    except (IndexError, ValueError, AssertionError):
        await update.message.reply_text("Uso: /dailyrisk <0.5-50>")
        return
    _trader(context).state.daily_max_loss = val
    await update.message.reply_text(f"✅ Kill-switch de pérdida diaria en {val}%.")


async def trading_job(context: ContextTypes.DEFAULT_TYPE):
    trader = context.application.bot_data["trader"]
    try:
        events = await asyncio.to_thread(trader.check)
    except Exception as e:
        await notify(context.application, f"❌ Error en el ciclo: {e}")
        return
    for ev in events:
        if ev["type"] in NOTIFY_TYPES:
            await notify(context.application, ev["text"])


def build_sb_application(trader: SessionBreakerTrader) -> Application:
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
    app.add_handler(CommandHandler("lot", cmd_lot))
    app.add_handler(CommandHandler("dailyrisk", cmd_dailyrisk))
    app.job_queue.run_repeating(
        trading_job, interval=CHECK_INTERVAL, first=10,
        job_kwargs={"max_instances": 3, "misfire_grace_time": 30},
    )
    return app
