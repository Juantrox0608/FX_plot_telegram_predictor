"""
Construcción del dataset para la IA.

Idea: en vez de meter precios crudos (no estacionarios) a la red, derivamos
*features* estacionarias a partir del precio y de los indicadores
(retornos, distancia a medias, RSI, MACD, posición en Bollinger, ATR relativo).
El objetivo es el retorno logarítmico de la vela futura (horizonte H).

Flujo típico (ver train.py):
    df -> make_features() -> split temporal -> estandarizar con stats de train
        -> make_windows() por cada split -> entrenar.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

import indicators as ind

# Nombres de las columnas de features que alimentan al GRU.
FEATURE_COLS = [
    "ret",          # retorno log de close
    "dist_tma20",   # (close - tma20) / close
    "dist_sma50",   # (close - sma50) / close
    "rsi",          # rsi14 / 100
    "macd_hist",    # histograma MACD relativo al precio
    "bb_pos",       # posición dentro de las bandas de Bollinger [-1..1 aprox]
    "atr_rel",      # atr14 / close (volatilidad relativa)
]


@dataclass
class Dataset:
    X: np.ndarray          # (N, T, F) float32
    y: np.ndarray          # (N,) float32  — retorno futuro (target)
    times: np.ndarray      # (N,) datetime64 — tiempo de la vela desde la que se predice
    close: np.ndarray      # (N,) float32 — close de esa vela (para backtest)
    trend: np.ndarray      # (N,) float32 — dist_tma20 SIN escalar (signo = tendencia)
    feature_cols: list[str]


def make_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Recibe un DataFrame OHLC (columnas open/high/low/close, opcional tick_volume)
    y devuelve un DataFrame con las columnas de FEATURE_COLS + 'close' + 'time',
    ya sin filas NaN de calentamiento.
    """
    out = ind.add_all(df)
    close = out["close"]

    feat = pd.DataFrame(index=out.index)
    feat["time"] = out["time"] if "time" in out.columns else out.index
    feat["close"] = close
    feat["ret"] = np.log(close / close.shift(1))
    feat["dist_tma20"] = (close - out["tma20"]) / close
    feat["dist_sma50"] = (close - out["sma50"]) / close
    feat["rsi"] = out["rsi14"] / 100.0
    feat["macd_hist"] = out["macd_hist"] / close
    bb_range = (out["bb_upper"] - out["bb_lower"]).replace(0.0, np.nan)
    feat["bb_pos"] = (close - out["bb_mid"]) / bb_range
    feat["atr_rel"] = out["atr14"] / close

    feat = feat.dropna().reset_index(drop=True)
    return feat


def build_target(close: pd.Series | np.ndarray, horizon: int = 1) -> np.ndarray:
    """Retorno log a `horizon` velas hacia adelante. NaN en las últimas `horizon`."""
    close = pd.Series(np.asarray(close, dtype=np.float64))
    fwd = np.log(close.shift(-horizon) / close)
    return fwd.to_numpy(dtype=np.float32)


def make_windows(
    feat_values: np.ndarray,
    target: np.ndarray,
    times: np.ndarray,
    close: np.ndarray,
    trend: np.ndarray,
    T: int,
) -> Dataset:
    """
    Convierte una matriz de features (n_bars, F) en ventanas deslizantes de
    longitud T. La ventana [i-T+1 .. i] predice el target en la vela i.
    Descarta ventanas cuyo target es NaN (las últimas `horizon`).
    `trend` es una señal sin escalar (dist_tma20) alineada a la vela de decisión.
    """
    n, F = feat_values.shape
    xs, ys, ts, cs, tr = [], [], [], [], []
    for i in range(T - 1, n):
        yt = target[i]
        if not np.isfinite(yt):
            continue
        xs.append(feat_values[i - T + 1 : i + 1])
        ys.append(yt)
        ts.append(times[i])
        cs.append(close[i])
        tr.append(trend[i])
    if not xs:
        raise ValueError("No se generaron ventanas: revisa T/horizonte vs. tamaño de datos.")
    return Dataset(
        X=np.asarray(xs, dtype=np.float32),
        y=np.asarray(ys, dtype=np.float32),
        times=np.asarray(ts),
        close=np.asarray(cs, dtype=np.float32),
        trend=np.asarray(tr, dtype=np.float32),
        feature_cols=list(FEATURE_COLS),
    )
