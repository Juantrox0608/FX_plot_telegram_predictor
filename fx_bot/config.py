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
load_dotenv(BASE_DIR / ".env")

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
    )


CONFIG = load_config()
