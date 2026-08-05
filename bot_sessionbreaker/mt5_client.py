"""
Cliente de MetaTrader 5: conexión, descarga de histórico y (más adelante)
apertura/cierre de órdenes.

Nota: la librería `MetaTrader5` solo funciona en Windows con el terminal MT5
instalado. El import es tolerante para que el resto del proyecto se pueda
importar/probar en entornos sin MT5; al usar las funciones sin la librería se
lanza un error claro.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from config import CONFIG, DATA_DIR

try:
    import MetaTrader5 as mt5  # type: ignore
    _MT5_AVAILABLE = True
except ImportError:  # entorno sin MT5 (p. ej. Linux o aún no instalado)
    mt5 = None  # type: ignore
    _MT5_AVAILABLE = False


# Mapeo de nuestras temporalidades a las constantes de MT5.
# Se resuelve en tiempo de ejecución porque depende de que mt5 esté disponible.
_TIMEFRAME_NAMES = {
    "M1": "TIMEFRAME_M1",
    "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
}


def _require_mt5() -> None:
    if not _MT5_AVAILABLE:
        raise RuntimeError(
            "La librería MetaTrader5 no está disponible. Instala el terminal MT5 "
            "y `pip install MetaTrader5` (solo Windows)."
        )


def timeframe_const(tf: str):
    """Devuelve la constante de MT5 para una temporalidad tipo 'M1', 'H1', etc."""
    _require_mt5()
    name = _TIMEFRAME_NAMES.get(tf.upper())
    if name is None:
        raise ValueError(f"Temporalidad no soportada: {tf}")
    return getattr(mt5, name)


def connect() -> None:
    """Inicializa la conexión con el terminal MT5 y hace login en la cuenta."""
    _require_mt5()
    kwargs = {}
    if CONFIG.mt5_path:
        kwargs["path"] = CONFIG.mt5_path
    if not mt5.initialize(
        login=CONFIG.mt5_login,
        password=CONFIG.mt5_password,
        server=CONFIG.mt5_server,
        **kwargs,
    ):
        raise RuntimeError(f"Fallo al inicializar MT5: {mt5.last_error()}")


def shutdown() -> None:
    if _MT5_AVAILABLE:
        mt5.shutdown()


def account_info() -> dict:
    """Balance, equity y modo de la cuenta (útil para /status y gestión de riesgo)."""
    _require_mt5()
    info = mt5.account_info()
    if info is None:
        raise RuntimeError(f"No pude leer account_info: {mt5.last_error()}")
    return {
        "login": info.login,
        "server": info.server,
        "balance": info.balance,
        "equity": info.equity,
        "currency": info.currency,
        "leverage": info.leverage,
        # trade_mode: 0=demo, 1=concurso, 2=real
        "is_demo": info.trade_mode == 0,
    }


def download_history(
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    years: Optional[int] = None,
    save: bool = True,
    chunk_days: int = 25,
    verbose: bool = False,
) -> pd.DataFrame:
    """
    Descarga `years` años de velas para `symbol`/`timeframe` y devuelve un
    DataFrame con columnas: time, open, high, low, close, tick_volume, spread.

    MT5 no entrega rangos grandes de temporalidades bajas (p. ej. M1) en una
    sola petición, así que descargamos por ventanas de `chunk_days` días y las
    concatenamos. Los tramos sin historial disponible se omiten.

    Si save=True, guarda un parquet en data/.
    """
    _require_mt5()
    symbol = symbol or CONFIG.symbol
    timeframe = timeframe or CONFIG.timeframe
    years = years or CONFIG.history_years

    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"No pude seleccionar el símbolo {symbol}: {mt5.last_error()}")

    tf = timeframe_const(timeframe)
    # MT5 espera datetime *naive* (rechaza tz-aware con "Invalid params").
    end = datetime.now() + timedelta(days=1)  # margen para la vela más reciente
    start = end - timedelta(days=365 * years + 1)

    frames: list[pd.DataFrame] = []
    win_start = start
    while win_start < end:
        win_end = min(win_start + timedelta(days=chunk_days), end)
        rates = mt5.copy_rates_range(symbol, tf, win_start, win_end)
        if rates is not None and len(rates) > 0:
            frames.append(pd.DataFrame(rates))
            if verbose:
                print(f"  {win_start:%Y-%m-%d} -> {win_end:%Y-%m-%d}: {len(rates):,} velas")
        elif verbose:
            print(f"  {win_start:%Y-%m-%d} -> {win_end:%Y-%m-%d}: sin datos")
        win_start = win_end

    if not frames:
        raise RuntimeError(
            f"No se descargaron datos para {symbol} {timeframe}: {mt5.last_error()}"
        )

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)

    if save:
        out = DATA_DIR / f"{symbol}_{timeframe}_{years}y.parquet"
        df.to_parquet(out, index=False)

    return df


if __name__ == "__main__":
    # Prueba manual rápida: descarga y muestra un resumen.
    connect()
    try:
        acc = account_info()
        print(f"Cuenta {acc['login']} @ {acc['server']} | demo={acc['is_demo']} "
              f"| balance={acc['balance']} {acc['currency']}")
        df = download_history()
        print(f"Descargadas {len(df):,} velas de {CONFIG.symbol} {CONFIG.timeframe}")
        print(df.head())
        print(df.tail())
    finally:
        shutdown()
