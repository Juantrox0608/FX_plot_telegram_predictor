"""
Estrategia de PAIRS (arbitraje estadístico): opera la reversión del spread entre
dos pares correlacionados (por defecto EURUSD vs GBPUSD).

Señal = z-score del spread:
  z < -entry  -> spread bajo  -> LONG spread  (long A / short B)
  z > +entry  -> spread alto  -> SHORT spread (short A / long B)
  |z| <= exit -> cerrar

Este módulo NO ejecuta órdenes; solo calcula la señal. Sirve igual para el
backtest (recorriendo el histórico) y para el bot en vivo (última vela).
El hedge ratio (beta) es opcional: en la versión BASE se asume 1:1 (lo que ya
validamos +20% robusto); beta rodante es una mejora a probar después.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd


class Action(str, Enum):
    OPEN_LONG = "open_long_spread"    # long A / short B
    OPEN_SHORT = "open_short_spread"  # short A / long B
    CLOSE = "close"
    HOLD = "hold"


@dataclass
class PairsConfig:
    symbol_a: str = "EURUSD"
    symbol_b: str = "GBPUSD"
    lookback: int = 20
    entry_z: float = 2.0
    exit_z: float = 0.0
    stop_z: float = 0.0      # 0 = sin stop; si |z| supera esto en contra, cortar
    min_corr: float = 0.0    # 0 = sin filtro; correlación mínima para abrir
    use_beta: bool = False   # BASE = 1:1; True = hedge ratio rodante (mejora)


def rolling_beta(log_a: pd.Series, log_b: pd.Series, lookback: int) -> pd.Series:
    cov = log_a.rolling(lookback).cov(log_b)
    var = log_b.rolling(lookback).var()
    return (cov / var).fillna(1.0)


def zscore(a_close: pd.Series, b_close: pd.Series, cfg: PairsConfig) -> tuple[pd.Series, pd.Series]:
    """Devuelve (z, beta) alineados. beta=1 si use_beta=False."""
    la, lb = np.log(a_close), np.log(b_close)
    if cfg.use_beta:
        beta = rolling_beta(la, lb, cfg.lookback)
    else:
        beta = pd.Series(1.0, index=la.index)
    spread = la - beta * lb
    z = (spread - spread.rolling(cfg.lookback).mean()) / spread.rolling(cfg.lookback).std(ddof=0)
    return z, beta


def decide(z_now: float, position: int, cfg: PairsConfig) -> Action:
    """
    position: 0 sin posición, +1 long-spread, -1 short-spread.
    Devuelve la acción a tomar en esta vela.
    """
    if not np.isfinite(z_now):
        return Action.HOLD
    if position == 0:
        if z_now < -cfg.entry_z:
            return Action.OPEN_LONG
        if z_now > cfg.entry_z:
            return Action.OPEN_SHORT
        return Action.HOLD
    # con posición abierta: stop de seguridad si el spread se dispara en contra
    if cfg.stop_z > 0:
        if position == 1 and z_now < -cfg.stop_z:
            return Action.CLOSE
        if position == -1 and z_now > cfg.stop_z:
            return Action.CLOSE
    # cerrar al volver al centro
    if position == 1 and z_now >= -cfg.exit_z:
        return Action.CLOSE
    if position == -1 and z_now <= cfg.exit_z:
        return Action.CLOSE
    return Action.HOLD
