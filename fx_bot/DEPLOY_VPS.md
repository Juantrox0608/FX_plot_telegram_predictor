# Despliegue en VPS Windows (DatabaseMart u otro)

Guía para dejar el bot FX V2 corriendo **24/5** en un VPS Windows. Pensada para
operar en **H1** (producción). La IA se entrena en tu PC; al VPS solo se le copia
el modelo ya entrenado.

> Requisitos del VPS: **Windows Server 2019/2022**, **≥4 GB RAM** recomendado
> (2 GB va muy justo), RDP con usuario administrador.

---

## 1. Conectarte por RDP

En tu PC (Windows): abre **Conexión a Escritorio remoto** (`mstsc`), pon la **IP**
del VPS y entra con el **usuario/clave** que te dio DatabaseMart.

Al entrar, primero:
- **Cambia la contraseña** por una fuerte (Ctrl+Alt+End → Cambiar contraseña).
- Ejecuta **Windows Update** y reinicia si hace falta.
- Ajusta la zona horaria si quieres (no afecta al bot: todo va en UTC).

## 2. Instalar Python 3.12

Descarga Python **3.12.x** (64-bit) de python.org.
- En el instalador marca **"Add python.exe to PATH"**.
- Verifica en PowerShell:
  ```powershell
  python --version
  ```

> Usamos 3.12 por máxima compatibilidad de librerías. (Local probamos 3.14 y
> también sirvió, pero 3.12 es lo más seguro en el VPS.)

## 3. Instalar MetaTrader 5 (Vantage) e iniciar sesión demo

1. Descarga el terminal MT5 de **Vantage** (o el genérico de MetaQuotes y luego
   añades el servidor de Vantage).
2. Inicia sesión en tu **cuenta demo** (login `25767875`, server
   `VantageMarkets-Demo`).
3. **MUY IMPORTANTE:** activa el botón **"Algo Trading"** de la barra superior
   (debe ponerse **verde**). Sin esto, las órdenes se rechazan con
   `retcode=10027`.
   - También: Herramientas → Opciones → Asesores Expertos → *Permitir trading
     algorítmico* ✅.
4. Deja el terminal **abierto**.

## 4. Instalar Git y clonar el repo

1. Instala **Git for Windows** (trae el Credential Manager).
2. En PowerShell:
   ```powershell
   cd C:\
   git clone -b v2 https://github.com/Juantrox0608/FX_plot_telegram_predictor.git
   cd FX_plot_telegram_predictor\fx_bot
   ```
   Se abrirá el navegador para autenticarte en GitHub (repo privado).

## 5. Instalar dependencias

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 6. Copiar el modelo entrenado (¡importante!)

El modelo (`.pt`) **no está en GitHub** (está en `.gitignore`). Cópialo de tu PC
al VPS:

1. En tu PC, ubica: `fx_bot\model\checkpoints\EURUSD_H1_4y_gru.pt`
2. En la sesión RDP puedes **copiar y pegar** el archivo directamente (el
   portapapeles funciona), o al conectar por RDP habilita *Recursos → Unidades*
   para ver tu disco local.
3. Pégalo en el VPS en: `...\fx_bot\model\checkpoints\EURUSD_H1_4y_gru.pt`

> Los datos históricos NO hace falta copiarlos: se descargan solos desde MT5.

## 7. Configurar el `.env` (en modo H1)

Copia la plantilla y edítala:
```powershell
copy .env.example .env
notepad .env
```
Rellena tus credenciales y pon **`TIMEFRAME=H1`** (producción):
```
MT5_LOGIN=25767875
MT5_PASSWORD=tu_clave_demo
MT5_SERVER=VantageMarkets-Demo
TG_BOT_TOKEN=tu_token
TG_CHAT_ID=tu_chat_id
SYMBOL=EURUSD
TIMEFRAME=H1
RISK_PERCENT=1.0
DAILY_MAX_LOSS_PERCENT=5.0
MODE=demo
```

## 8. Primera prueba manual

```powershell
python main.py
```
Debe llegarte a Telegram *"🚀 Bot FX V2 iniciado"*. Manda `/status` para
confirmar. Déjalo un rato y verifica que reacciona al cierre de cada vela H1.
Para parar: `Ctrl+C`.

## 9. Dejarlo corriendo 24/5 (arranque automático)

Crea un `.bat` de arranque, p. ej. `C:\FX_plot_telegram_predictor\fx_bot\run_bot.bat`:
```bat
@echo off
cd /d C:\FX_plot_telegram_predictor\fx_bot
call .venv\Scripts\activate.bat
:loop
python main.py
echo Bot cerrado. Reiniciando en 15s...
timeout /t 15
goto loop
```
Ese bucle **reinicia el bot solo** si se cae.

Luego, en el **Programador de tareas** (Task Scheduler):
- Crear tarea → **Ejecutar aunque el usuario no haya iniciado sesión** *(o mejor,
  "solo cuando el usuario inicie sesión" + auto-login, porque MT5 necesita una
  sesión interactiva con escritorio)*.
- Desencadenador: **Al iniciar sesión**.
- Acción: iniciar `run_bot.bat`.

> **Importante sobre RDP + MT5:** MT5 necesita una sesión de escritorio activa.
> Cuando termines de configurar, **DESCONECTA** la sesión RDP (cerrar la ventana),
> **no "Cerrar sesión"** — así MT5 y el bot siguen vivos. Si el VPS se reinicia,
> configura **auto-login** para que la sesión y el `.bat` vuelvan solos.

## 10. Operación y control

- Todo se controla por Telegram: `/status` `/positions` `/stats` `/closeall`
  `/stop` `/resume` `/risk` `/dailyrisk`.
- El **diario** (`data/journal.db`) va guardando cada operación. Descárgalo cuando
  quieras y analízalo con `python analyze_journal.py`.

## 11. Seguridad del VPS

- Contraseña de administrador **fuerte** + Windows Update al día.
- Si el proveedor lo permite, **restringe RDP a tu IP** o cambia el puerto.
- No pongas credenciales de cuenta **real** hasta estar listo; mantén la demo.
- El `.env` nunca se sube a GitHub (está en `.gitignore`).

---

### Resumen rápido
RDP → Python 3.12 → MT5 + Algo Trading ✅ → Git clone → `pip install` → copiar
modelo `.pt` → `.env` en H1 → `python main.py` → Task Scheduler + desconectar RDP.
