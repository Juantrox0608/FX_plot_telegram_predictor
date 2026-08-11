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
        stops_level=si.trade_stops_level,
    )


def _prices(symbol: str) -> tuple[float, float]:
    """Devuelve (ask, bid)."""
    _require_mt5()
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"symbol_info_tick None para {symbol}: {mt5.last_error()}")
    return tick.ask, tick.bid


def _round_step(volume: float, step: float) -> float:
    if step <= 0:
        return volume
    return round(round(volume / step) * step, 8)


def _fit_lot_to_margin(symbol: str, order_type, price: float, lot: float, spec) -> float:
    """Reduce el lote si el margen requerido supera el margen libre (90%)."""
    acc = mt5.account_info()
    if acc is None or acc.margin_free <= 0:
        return lot
    margin = mt5.order_calc_margin(order_type, symbol, lot, price)
    if margin is None or margin <= 0:
        return lot
    budget = acc.margin_free * 0.9
    if margin <= budget:
        return lot
    scaled = lot * (budget / margin)
    scaled = _round_step(scaled, spec.volume_step)
    return scaled if scaled >= spec.volume_min else 0.0


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

    # Distancia mínima de stop: el mayor entre el stop del broker y ~3x el spread,
    # para que en M1 (ATR diminuto) los stops no queden pegados al precio.
    spread = max(0.0, ask - bid)
    min_stop = max(spec.stops_level * spec.point, spread * 3)

    levels = atr_levels(signal.direction, entry, signal.atr, spec, sl_mult, tp_mult, min_stop)
    lot = lot_for_risk(balance, signal.risk_pct, levels.sl_distance, spec)
    if lot <= 0:
        return OrderResult(False, "Lote calculado 0 (revisa riesgo/ATR/límites del símbolo)")

    order_type = mt5.ORDER_TYPE_BUY if signal.direction > 0 else mt5.ORDER_TYPE_SELL

    # Chequeo de margen: si el lote no cabe, reducirlo hasta lo que permita el margen libre.
    lot = _fit_lot_to_margin(symbol, order_type, entry, lot, spec)
    if lot <= 0:
        return OrderResult(False, "Margen insuficiente para el lote mínimo")
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


# ============================ PAIRS (2 patas) ============================
PAIRS_MAGIC = 20260714


def _market_order(symbol: str, side: int, lot: float, magic: int,
                  comment: str, deviation: int = 20) -> OrderResult:
    """Orden a mercado simple (sin SL/TP). side: +1 compra, -1 venta."""
    _require_mt5()
    ask, bid = _prices(symbol)
    price = ask if side > 0 else bid
    order_type = mt5.ORDER_TYPE_BUY if side > 0 else mt5.ORDER_TYPE_SELL
    request = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": lot,
        "type": order_type, "price": price, "deviation": deviation,
        "magic": magic, "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC, "type_filling": _filling_mode(symbol),
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        code = None if result is None else result.retcode
        cmt = "" if result is None else result.comment
        return OrderResult(False, f"Rechazada {symbol} retcode={code} ({cmt})")
    return OrderResult(True, "OK", ticket=result.order, volume=lot, price=result.price)


def open_pair(direction: int, symbol_a: str, symbol_b: str, lot: float,
              magic: int = PAIRS_MAGIC) -> dict:
    """
    Abre las 2 patas. direction=+1 -> LONG A / SHORT B ; -1 -> SHORT A / LONG B.
    Cada par usa su propio `magic` para no mezclarse con otros pares.
    Si la 2ª pata falla, cierra la 1ª para no quedar con una pata desnuda.
    """
    _require_mt5()
    side_a = 1 if direction > 0 else -1
    side_b = -side_a
    ra = _market_order(symbol_a, side_a, lot, magic, "pairs_a")
    if not ra.ok:
        return {"ok": False, "message": f"Pata A falló: {ra.message}", "leg_a": ra, "leg_b": None}
    rb = _market_order(symbol_b, side_b, lot, magic, "pairs_b")
    if not rb.ok:
        close_position(ra.ticket)  # rollback pata A
        return {"ok": False, "message": f"Pata B falló (revertí A): {rb.message}",
                "leg_a": ra, "leg_b": rb}
    return {"ok": True, "message": "Par abierto", "leg_a": ra, "leg_b": rb}


def pair_positions(magic: int = PAIRS_MAGIC) -> list[dict]:
    """Posiciones abiertas de un par (por su magic)."""
    return [p for p in get_positions() if p["magic"] == magic]


# ======================= SESSION BREAKER (1 pata + SL/TP) =======================
SB_MAGIC = 20260801


def open_breakout(symbol: str, direction: int, lot: float, sl_price: float,
                  tp_price: float, magic: int = SB_MAGIC, deviation: int = 20) -> OrderResult:
    """
    Orden a mercado con SL y TP fijos en el bróker (para session breakout).
    direction: +1 compra, -1 venta. El bróker gestiona SL/TP; el cierre por fin
    de sesión lo hace el trader.
    """
    _require_mt5()
    ask, bid = _prices(symbol)
    price = ask if direction > 0 else bid
    order_type = mt5.ORDER_TYPE_BUY if direction > 0 else mt5.ORDER_TYPE_SELL
    lot = _fit_lot_to_margin(symbol, order_type, price, lot, get_symbol_spec(symbol))
    if lot <= 0:
        return OrderResult(False, "Margen insuficiente para el lote mínimo")
    request = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": lot,
        "type": order_type, "price": price, "sl": sl_price, "tp": tp_price,
        "deviation": deviation, "magic": magic, "comment": "sb",
        "type_time": mt5.ORDER_TIME_GTC, "type_filling": _filling_mode(symbol),
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        code = None if result is None else result.retcode
        cmt = "" if result is None else result.comment
        return OrderResult(False, f"Rechazada {symbol} retcode={code} ({cmt})")
    return OrderResult(True, "OK", ticket=result.order, volume=lot,
                       price=result.price, sl=sl_price, tp=tp_price)


def sb_positions(magic: int = SB_MAGIC) -> list[dict]:
    """Posiciones abiertas del session breaker (por su magic)."""
    return [p for p in get_positions() if p["magic"] == magic]


def close_pair(magic: int = PAIRS_MAGIC) -> list[OrderResult]:
    """Cierra todas las patas de un par."""
    return [close_position(p["ticket"]) for p in pair_positions(magic)]
