"""
Indicadores técnicos (vectorizados con pandas). Estos alimentan tanto la
lógica de confirmación como los *features* de la IA.

Convención: todas las funciones reciben un pd.Series (o DataFrame OHLC) y
devuelven pd.Series alineadas al índice de entrada (NaN al inicio hasta que
hay suficientes datos).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def tma(series: pd.Series, period: int) -> pd.Series:
    """
    Triangular Moving Average: doble suavizado (SMA de una SMA), lo que da una
    línea más suave y menos ruidosa que la SMA/EMA simple. Ideal para robustecer
    la lectura de tendencia.
    """
    if period < 1:
        raise ValueError("period debe ser >= 1")
    first = int(np.ceil((period + 1) / 2))
    second = int(np.floor((period + 1) / 2))
    return sma(sma(series, first), second)


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI de Wilder."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - (100 / (1 + rs))
    out[avg_loss == 0] = 100.0
    return out


def macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    """Devuelve DataFrame con columnas macd, signal, hist."""
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return pd.DataFrame(
        {"macd": macd_line, "signal": signal_line, "hist": hist}
    )


def bollinger(
    series: pd.Series, period: int = 20, num_std: float = 2.0
) -> pd.DataFrame:
    """Devuelve DataFrame con columnas mid, upper, lower."""
    mid = sma(series, period)
    std = series.rolling(window=period, min_periods=period).std(ddof=0)
    return pd.DataFrame(
        {"mid": mid, "upper": mid + num_std * std, "lower": mid - num_std * std}
    )


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average True Range (Wilder). df debe tener columnas high, low, close.
    Se usa para dimensionar SL/TP según volatilidad.
    """
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def add_all(df: pd.DataFrame) -> pd.DataFrame:
    """
    Añade todos los indicadores como columnas nuevas a un DataFrame OHLC.
    Devuelve una copia. Columnas requeridas: open, high, low, close.
    """
    out = df.copy()
    close = out["close"]

    out["tma20"] = tma(close, 20)
    out["sma50"] = sma(close, 50)
    out["rsi14"] = rsi(close, 14)

    macd_df = macd(close)
    out["macd"] = macd_df["macd"]
    out["macd_signal"] = macd_df["signal"]
    out["macd_hist"] = macd_df["hist"]

    bb = bollinger(close)
    out["bb_mid"] = bb["mid"]
    out["bb_upper"] = bb["upper"]
    out["bb_lower"] = bb["lower"]

    out["atr14"] = atr(out, 14)
    return out
