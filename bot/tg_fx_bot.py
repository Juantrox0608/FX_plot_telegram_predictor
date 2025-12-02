#!/usr/bin/env python3
import os, re, shlex, subprocess, shutil
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# === Rutas base ===
BASE_DIR = Path(__file__).resolve().parent   # carpeta donde está este archivo

# === Carga .env ===
load_dotenv(BASE_DIR / ".env")

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()

# Puedes dar FX_TO_CSV absoluto en .env.
# Si además pones FX_BIN_DIR, aquí lo usamos solo si FX_TO_CSV no está.
FX_BIN_DIR = os.getenv("FX_BIN_DIR", "/opt/bin").strip()
FX_TO_CSV  = os.getenv("FX_TO_CSV", "").strip() or os.path.join(FX_BIN_DIR, "fx_to_csv_copy")

# Expande ~ si se usó
FX_TO_CSV = os.path.expanduser(FX_TO_CSV)

# Carpeta donde también puedes guardar copias (opcional)
FX_OUT = Path(os.getenv("FX_OUT", BASE_DIR / "out"))
FX_OUT.mkdir(parents=True, exist_ok=True)

# === Validaciones de arranque ===
if not TG_BOT_TOKEN:
    raise SystemExit("Falta TG_BOT_TOKEN en .env")

if not Path(FX_TO_CSV).exists():
    raise SystemExit(f"FX_TO_CSV no existe: {FX_TO_CSV}")
if not os.access(FX_TO_CSV, os.X_OK):
    raise SystemExit(f"FX_TO_CSV no es ejecutable: {FX_TO_CSV} (haz chmod +x)")

# === Ayuda e instrucciones ===
HELP_TXT = (
    "👋 Hola. La estructura del primer mensaje es:\n"
    "<SIMBOLO> <TIEMPO> <VELAS>\n\n"
    "Ejemplos:\n"
    "  EUR/USD m5 2000\n"
    "  XAU/USD h1 1000\n\n"
    "Notas:\n"
    "- SIMBOLO: EUR/USD, EURUSD, GBP/JPY, etc.\n"
    "- TIEMPO: m1, m5, m15, m30, h1, h4, d1 (según disponibilidad)\n"
    "- VELAS: cantidad de barras a descargar\n"
)

# Acepta "EUR/USD m5 2000" o "EURUSD m15 1500" etc.
RX = re.compile(r"^([A-Za-z/]+)\s+([mhd]\d{1,2}|d1)\s+(\d{2,6})$", re.IGNORECASE)

def normalize_symbol(s: str) -> str:
    s = s.strip().upper().replace(" ", "")
    if "/" not in s and len(s) in (6,7):  # EURUSD -> EUR/USD (básico)
        s = s[:3] + "/" + s[3:]
    return s

def make_job_base(symbol: str, tf: str, bars: int) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    clean_sym = symbol.replace("/", "")
    return f"{clean_sym}_{tf.lower()}_{bars}_{stamp}"

def run(cmd, input_text=None, cwd=None, timeout=600):
    """Ejecuta comando y devuelve (rc, stdout, stderr)."""
    p = subprocess.run(
        shlex.split(cmd),
        input=input_text,
        text=True,
        cwd=cwd,
        capture_output=True,
        timeout=timeout
    )
    return p.returncode, p.stdout, p.stderr

# === Handlers ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hola, soy tu bot de FX.\n" + HELP_TXT)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TXT)

async def text_listener(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    # 1) “hola” (y variantes) => mostrar instrucciones
    if text.lower() in ("hola", "ola", "buenas", "hey", "hello", "hi"):
        await update.message.reply_text(HELP_TXT)
        return

    # 2) Si coincide patrón <SIMBOLO> <TF> <VELAS>, extraer CSV
    m = RX.match(text)
    if not m:
        # Ignoramos textos que no cumplen; si prefieres, responde con HELP_TXT
        return

    symbol = normalize_symbol(m.group(1))
    tf     = m.group(2).lower()
    bars   = int(m.group(3))

    await extract_csv(symbol, tf, bars, update.effective_chat.id, context)

async def extract_csv(symbol: str, tf: str, bars: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    job_base = make_job_base(symbol, tf, bars)
    csv_path = BASE_DIR / f"{job_base}.csv"

    # Ejecuta extractor (credenciales embebidas en el binario)
    cmd = f'{FX_TO_CSV} "{symbol}" {tf} {bars}'
    rc, out, err = run(cmd)

    # Diagnóstico: si rc != 0 o no hay salida -> error al extraer
    if rc != 0 or not out.strip():
        msg = (f"❌ Error al extraer datos.\n"
               f"CMD: {cmd}\n"
               f"{(err or '').strip()[:900]}")
        await context.bot.send_message(chat_id=chat_id, text=msg)
        return

    # Guardar CSV en la MISMA carpeta del bot
    try:
        csv_path.write_text(out)
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ No pude escribir el CSV: {e}")
        return

    # Confirmar y adjuntar archivo
    await context.bot.send_message(chat_id=chat_id, text=f"✅ CSV generado: {csv_path.name}")
    try:
        await context.bot.send_document(chat_id=chat_id, document=InputFile(str(csv_path), filename=csv_path.name))
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ No pude adjuntar el CSV: {e}")

    # (Opcional) guarda copia en FX_OUT
    try:
        shutil.copy2(csv_path, FX_OUT / csv_path.name)
    except Exception:
        pass

    # === Mensaje-guía para crear el reporte ===
    fx_report_bin = os.getenv("FX_REPORT", "/opt/bin/fx_report_all")

    # Ejemplo con Ventana T=20, H=1, 60 épocas
    ejemplo_cmd = (
        f'echo "0 0 0 0 0" | {fx_report_bin} "{csv_path}" "{symbol}" 60 32 20 1 20'
    )

    texto_pasos = (
        "🧠 *Cómo generar el reporte (Torch + Indicadores)*\n"
        "\n"
        "• Ventana T = 20 velas, horizonte H = 1 (siguiente vela), 60 épocas:\n"
        f"{ejemplo_cmd}\n"
        "\n"
        "Explicación de cada parámetro:\n"
        f"1) {csv_path.name} → El CSV que acabamos de generar.\n"
        f'2) \"{symbol}\" → Instrumento/símbolo (si lleva \"/\", déjalo entre comillas).\n'
        "3) 60 → *epochs* de entrenamiento (más = más lento, potencialmente mejor ajuste).\n"
        "4) 32 → *batch size* (tamaño del lote por iteración de entrenamiento).\n"
        "5) 20 → T (longitud de ventana, cuántas velas usa la GRU como entrada).\n"
        "6) 1 → H (horizonte: cuántas velas hacia adelante se predice; 1 = siguiente vela).\n"
        "7) 20 → *thresh_bp* (umbral en *basis points*; 20 = 0.20%) para convertir la predicción en puntaje.\n"
        "\n"
        "• Las 5 cifras iniciales \"0 0 0 0 0\" son tus respuestas de *noticias* (0=No, 1=Sí). Puedes cambiarlas, p. ej. \"1 0 1 0 0\".\n"
        "• El reporte generará `indicadores.png` e `informe.html` en el directorio donde ejecutes el comando.\n"
    )

    try:
        await context.bot.send_message(chat_id=chat_id, text=texto_pasos, parse_mode="Markdown")
    except Exception:
        await context.bot.send_message(chat_id=chat_id, text=texto_pasos)

def main():
    app = Application.builder().token(TG_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_listener))
    print("Bot corriendo (polling). Ctrl+C para salir.")
    app.run_polling()

if __name__ == "__main__":
    main()
