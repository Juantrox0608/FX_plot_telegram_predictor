"""
Capa de Telegram del bot de PAIRS. Reutiliza autorización, notify y el filtro de
eventos del bot direccional. Comandos adaptados al par.
"""
from __future__ import annotations

import asyncio

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from config import CONFIG
from pairs_trader import PairsTrader
from telegram_bot import NOTIFY_TYPES, _authorized, notify

CHECK_INTERVAL = 60  # segundos


def _trader(context) -> PairsTrader:
    return context.application.bot_data["trader"]


@_authorized
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Bot PAIRS en marcha.\n"
        "/status /positions /stats /closeall /stop /resume /cap /dailyrisk"
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
        await update.message.reply_text("No había par abierto.")
        return
    ok = sum(1 for r in res if r.ok)
    await update.message.reply_text(f"🚨 Cierre de emergencia: {ok}/{len(res)} patas cerradas.")


@_authorized
async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _trader(context).state.running = False
    await update.message.reply_text("⏸️ Trading PAUSADO (no abrirá nuevos pares).")


@_authorized
async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _trader(context).state.running = True
    await update.message.reply_text("▶️ Trading REANUDADO.")


@_authorized
async def cmd_cap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = float(context.args[0])
        assert val >= 10
    except (IndexError, ValueError, AssertionError):
        await update.message.reply_text("Uso: /cap <capital por 0.01 lote> (mayor = más conservador)")
        return
    _trader(context).state.cap_per_unit = val
    await update.message.reply_text(f"✅ Capital por lote fijado en {val}.")


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


def build_pairs_application(trader: PairsTrader) -> Application:
    app = Application.builder().token(CONFIG.tg_bot_token).build()
    app.bot_data["trader"] = trader
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("closeall", cmd_closeall))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("cap", cmd_cap))
    app.add_handler(CommandHandler("dailyrisk", cmd_dailyrisk))
    app.job_queue.run_repeating(trading_job, interval=CHECK_INTERVAL, first=10)
    return app
