"""
Ejecución de órdenes en MetaTrader 5: abrir desde una señal, listar posiciones
y cerrar (incluye cierre de emergencia de todo).

Modelo híbrido: en DEMO se puede abrir automáticamente; en REAL la confirmación
la gestiona la capa de Telegram antes de llamar aquí. Este módulo solo ejecuta.
"""
from __future__ import annotations

from dataclasses import dataclass

from mt5_client import _require_mt5, mt5
from risk import SymbolSpec, atr_levels, lot_for_risk
from strategy import Signal

MAGIC = 20260713  # identifica las operaciones del bot


@dataclass
class OrderResult:
    ok: bool
    message: str
    ticket: int = 0
    volume: float = 0.0
    price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0


def get_symbol_spec(symbol: str) -> SymbolSpec:
    _require_mt5()
    si = mt5.symbol_info(symbol)
    if si is None:
        raise RuntimeError(f"symbol_info None para {symbol}: {mt5.last_error()}")
    return SymbolSpec(
        tick_size=si.trade_tick_size,
        tick_value=si.trade_tick_value,
        volume_min=si.volume_min,
        volume_max=si.volume_max,
        volume_step=si.volume_step,
        digits=si.digits,
        point=si.point,
    )


def _prices(symbol: str) -> tuple[float, float]:
    """Devuelve (ask, bid)."""
    _require_mt5()
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"symbol_info_tick None para {symbol}: {mt5.last_error()}")
    return tick.ask, tick.bid


def _filling_mode(symbol: str):
    """Elige un modo de llenado soportado por el símbolo."""
    _require_mt5()
    si = mt5.symbol_info(symbol)
    mode = getattr(si, "filling_mode", 0)
    # filling_mode es un bitmask: 1=FOK, 2=IOC
    if mode & 2:
        return mt5.ORDER_FILLING_IOC
    if mode & 1:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


def open_from_signal(
    signal: Signal,
    symbol: str,
    balance: float,
    sl_mult: float = 1.5,
    tp_mult: float = 1.5,
    deviation: int = 20,
) -> OrderResult:
    """Dimensiona y envía una orden a mercado a partir de una señal."""
    _require_mt5()
    if signal.direction == 0:
        return OrderResult(False, "Señal sin dirección (esperar)")

    spec = get_symbol_spec(symbol)
    ask, bid = _prices(symbol)
    entry = ask if signal.direction > 0 else bid

    levels = atr_levels(signal.direction, entry, signal.atr, spec, sl_mult, tp_mult)
    lot = lot_for_risk(balance, signal.risk_pct, levels.sl_distance, spec)
    if lot <= 0:
        return OrderResult(False, "Lote calculado 0 (revisa riesgo/ATR/límites del símbolo)")

    order_type = mt5.ORDER_TYPE_BUY if signal.direction > 0 else mt5.ORDER_TYPE_SELL
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": order_type,
        "price": entry,
        "sl": levels.sl,
        "tp": levels.tp,
        "deviation": deviation,
        "magic": MAGIC,
        "comment": "fxbot_v2",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _filling_mode(symbol),
    }
    result = mt5.order_send(request)
    if result is None:
        return OrderResult(False, f"order_send None: {mt5.last_error()}")
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return OrderResult(False, f"Rechazada retcode={result.retcode} ({result.comment})")

    return OrderResult(
        True, "Orden ejecutada", ticket=result.order, volume=lot,
        price=result.price, sl=levels.sl, tp=levels.tp,
    )


def get_positions(symbol: str | None = None) -> list[dict]:
    _require_mt5()
    pos = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    if pos is None:
        return []
    out = []
    for p in pos:
        out.append({
            "ticket": p.ticket,
            "symbol": p.symbol,
            "type": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
            "volume": p.volume,
            "price_open": p.price_open,
            "sl": p.sl, "tp": p.tp,
            "profit": p.profit,
            "magic": p.magic,
        })
    return out


def close_position(ticket: int, deviation: int = 20) -> OrderResult:
    _require_mt5()
    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        return OrderResult(False, f"No existe la posición {ticket}")
    p = pos[0]
    ask, bid = _prices(p.symbol)
    if p.type == mt5.POSITION_TYPE_BUY:
        close_type, price = mt5.ORDER_TYPE_SELL, bid
    else:
        close_type, price = mt5.ORDER_TYPE_BUY, ask
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": p.symbol,
        "volume": p.volume,
        "type": close_type,
        "position": ticket,
        "price": price,
        "deviation": deviation,
        "magic": MAGIC,
        "comment": "fxbot_v2_close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _filling_mode(p.symbol),
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        code = None if result is None else result.retcode
        return OrderResult(False, f"No se pudo cerrar {ticket} (retcode={code})", ticket=ticket)
    return OrderResult(True, f"Posición {ticket} cerrada", ticket=ticket)


def close_all(symbol: str | None = None) -> list[OrderResult]:
    """Cierre de emergencia: cierra todas las posiciones del bot (o todas)."""
    results = []
    for p in get_positions(symbol):
        results.append(close_position(p["ticket"]))
    return results
