"""
Configuración central del bot. Carga variables desde .env y expone
un objeto CONFIG con valores tipados y validados.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
# Cada instancia (multi-cuenta) puede apuntar a su propio archivo con ENV_FILE.
# Por defecto usa ".env" (modo single-cuenta de siempre).
_ENV_FILE = os.getenv("ENV_FILE", ".env")
load_dotenv(BASE_DIR / _ENV_FILE)

# Carpetas de trabajo (se crean si no existen)
DATA_DIR = BASE_DIR / "data"
CHECKPOINT_DIR = BASE_DIR / "model" / "checkpoints"
REPORT_DIR = BASE_DIR / "reportes_out"
for _d in (DATA_DIR, CHECKPOINT_DIR, REPORT_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_float(name: str, default: float) -> float:
    try:
        return float(_get(name) or default)
    except ValueError:
        return default


def _get_int(name: str, default: int) -> int:
    try:
        return int(_get(name) or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    # MetaTrader 5
    mt5_login: int
    mt5_password: str
    mt5_server: str
    mt5_path: str

    # Telegram
    tg_bot_token: str
    tg_chat_id: int

    # Trading
    symbol: str
    timeframe: str
    risk_percent: float
    daily_max_loss_percent: float
    mode: str  # "demo" | "real"

    # Datos
    history_years: int

    # Pairs (arbitraje estadístico)
    pair_a: str
    pair_b: str
    pairs_lookback: int
    pairs_entry_z: float
    pairs_stop_z: float
    pairs_min_corr: float
    pairs_cap_per_unit: float
    demo_duration_months: int
    demo_duration_days: int
    demo_license_file: str

    # Session Breaker (capa de volumen)
    sb_symbols: str = "EURUSD,GBPUSD,AUDUSD,USDCHF,USDCAD,NZDUSD"
    sb_sessions: str = "0-7:8-12,9-13:13-17"  # "rango:trade" en hora del servidor MT5
    sb_lot: float = 0.01
    sb_sl_mult: float = 1.0
    sb_tp_mult: float = 2.0
    sb_daily_max_loss: float = 10.0
    sb_buffer_pips: float = 1.0

    # Multi-par (timeframe, lista de pares y opt-in real)
    pairs_timeframe: str = "D1"          # D1 (proven) o H4 (más activo)
    pairs_list: str = "EURUSD-GBPUSD,USDCHF-USDCAD,AUDUSD-NZDUSD"
    pairs_allow_real: bool = False        # opt-in DELIBERADO para operar cuenta real

    def validate(self) -> list[str]:
        """Devuelve una lista de problemas de configuración (vacía si todo OK)."""
        problems: list[str] = []
        if not self.mt5_login:
            problems.append("MT5_LOGIN no configurado")
        if not self.mt5_password:
            problems.append("MT5_PASSWORD no configurado")
        if not self.mt5_server:
            problems.append("MT5_SERVER no configurado")
        if not self.tg_bot_token:
            problems.append("TG_BOT_TOKEN no configurado")
        if not self.tg_chat_id:
            problems.append("TG_CHAT_ID no configurado")
        if not (1.0 <= self.risk_percent <= 5.0):
            problems.append(f"RISK_PERCENT fuera de rango 1-5: {self.risk_percent}")
        if self.mode not in ("demo", "real"):
            problems.append(f"MODE inválido (usa demo|real): {self.mode}")
        if self.demo_duration_months < 0:
            problems.append(f"DEMO_DURATION_MONTHS debe ser mayor o igual a 0: {self.demo_duration_months}")
        if self.demo_duration_days < 0:
            problems.append(f"DEMO_DURATION_DAYS debe ser mayor o igual a 0: {self.demo_duration_days}")
        if self.demo_duration_months == 0 and self.demo_duration_days == 0:
            problems.append("Configura DEMO_DURATION_MONTHS o DEMO_DURATION_DAYS")
        return problems


def load_config() -> Config:
    return Config(
        mt5_login=_get_int("MT5_LOGIN", 0),
        mt5_password=_get("MT5_PASSWORD"),
        mt5_server=_get("MT5_SERVER"),
        mt5_path=_get("MT5_PATH"),
        tg_bot_token=_get("TG_BOT_TOKEN"),
        tg_chat_id=_get_int("TG_CHAT_ID", 0),
        symbol=_get("SYMBOL", "EURUSD") or "EURUSD",
        timeframe=_get("TIMEFRAME", "M1") or "M1",
        risk_percent=_get_float("RISK_PERCENT", 1.0),
        daily_max_loss_percent=_get_float("DAILY_MAX_LOSS_PERCENT", 5.0),
        mode=(_get("MODE", "demo") or "demo").lower(),
        history_years=_get_int("HISTORY_YEARS", 4),
        pair_a=_get("PAIR_A", "EURUSD") or "EURUSD",
        pair_b=_get("PAIR_B", "GBPUSD") or "GBPUSD",
        pairs_lookback=_get_int("PAIRS_LOOKBACK", 20),
        pairs_entry_z=_get_float("PAIRS_ENTRY_Z", 2.0),
        pairs_stop_z=_get_float("PAIRS_STOP_Z", 3.5),
        pairs_min_corr=_get_float("PAIRS_MIN_CORR", 0.6),
        pairs_cap_per_unit=_get_float("PAIRS_CAP_PER_UNIT", 200.0),
        demo_duration_months=_get_int("DEMO_DURATION_MONTHS", 1),
        demo_duration_days=_get_int("DEMO_DURATION_DAYS", 0),
        demo_license_file=_get("DEMO_LICENSE_FILE"),
        sb_symbols=_get("SB_SYMBOLS", "EURUSD,GBPUSD,AUDUSD,USDCHF,USDCAD,NZDUSD")
        or "EURUSD,GBPUSD,AUDUSD,USDCHF,USDCAD,NZDUSD",
        sb_sessions=_get("SB_SESSIONS", "0-7:8-12,9-13:13-17") or "0-7:8-12,9-13:13-17",
        sb_lot=_get_float("SB_LOT", 0.01),
        sb_sl_mult=_get_float("SB_SL_MULT", 1.0),
        sb_tp_mult=_get_float("SB_TP_MULT", 2.0),
        sb_daily_max_loss=_get_float("SB_DAILY_MAX_LOSS", 10.0),
        sb_buffer_pips=_get_float("SB_BUFFER_PIPS", 1.0),
        pairs_timeframe=(_get("PAIRS_TIMEFRAME", "D1") or "D1").upper(),
        pairs_list=_get("PAIRS_LIST", "EURUSD-GBPUSD,USDCHF-USDCAD,AUDUSD-NZDUSD")
        or "EURUSD-GBPUSD,USDCHF-USDCAD,AUDUSD-NZDUSD",
        pairs_allow_real=_get("PAIRS_ALLOW_REAL", "").lower() in ("1", "true", "yes", "si", "sí"),
    )


CONFIG = load_config()
