"""
Gestión de riesgo: cálculo del tamaño de lote a partir del % de riesgo y la
distancia del stop loss (que sale del ATR). También calcula SL/TP.

La idea: nunca arriesgar más del % configurado del balance en una operación.
El tamaño del lote se deduce de cuánto se perdería si salta el SL.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SymbolSpec:
    """Datos del símbolo necesarios para dimensionar (de mt5.symbol_info)."""
    tick_size: float      # trade_tick_size (mínimo movimiento de precio)
    tick_value: float     # trade_tick_value (valor monetario de un tick por lote)
    volume_min: float
    volume_max: float
    volume_step: float
    digits: int
    point: float
    stops_level: int = 0  # trade_stops_level: distancia mínima SL/TP en points


def _round_step(volume: float, step: float) -> float:
    if step <= 0:
        return volume
    return round(round(volume / step) * step, 8)


def lot_for_risk(
    balance: float,
    risk_pct: float,
    sl_distance_price: float,
    spec: SymbolSpec,
) -> float:
    """
    Devuelve el volumen (lotes) para arriesgar `risk_pct`% del balance si el
    precio recorre `sl_distance_price` (en precio) hasta el SL.

    Se limita a [volume_min, volume_max] y se redondea al volume_step.
    """
    if sl_distance_price <= 0 or spec.tick_size <= 0 or spec.tick_value <= 0:
        return 0.0

    risk_amount = balance * (risk_pct / 100.0)
    ticks = sl_distance_price / spec.tick_size
    loss_per_lot = ticks * spec.tick_value
    if loss_per_lot <= 0:
        return 0.0

    lot = risk_amount / loss_per_lot
    lot = _round_step(lot, spec.volume_step)
    lot = max(spec.volume_min, min(spec.volume_max, lot))
    return lot


@dataclass
class Levels:
    entry: float
    sl: float
    tp: float
    sl_distance: float
    tp_distance: float
    rr: float  # ratio riesgo/beneficio


def atr_levels(
    direction: int,
    entry: float,
    atr: float,
    spec: SymbolSpec,
    sl_mult: float = 1.5,
    tp_mult: float = 1.5,
    min_stop_price: float = 0.0,
) -> Levels:
    """
    Calcula SL/TP basados en ATR. Por defecto RR = 1:1 (sl=tp=1.5*ATR): es la
    única relación que resultó rentable en el backtest evento-a-evento, porque
    el edge de la IA es de corto alcance y un TP lejano rara vez se alcanza.
    `min_stop_price` fuerza una distancia mínima (stop del broker / spread) para
    evitar stops degenerados (útil sobre todo en M1, donde el ATR es diminuto).
    direction: +1 compra, -1 venta.
    """
    sl_dist = max(atr * sl_mult, min_stop_price)
    tp_dist = max(atr * tp_mult, min_stop_price)
    if direction > 0:  # compra
        sl = entry - sl_dist
        tp = entry + tp_dist
    else:              # venta
        sl = entry + sl_dist
        tp = entry - tp_dist
    r = round(entry, spec.digits)
    return Levels(
        entry=r,
        sl=round(sl, spec.digits),
        tp=round(tp, spec.digits),
        sl_distance=sl_dist,
        tp_distance=tp_dist,
        rr=round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0,
    )
