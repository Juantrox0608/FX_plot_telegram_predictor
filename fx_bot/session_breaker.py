"""
Session Breaker (H1) — capa de VOLUMEN.

Idea: durante la sesión tranquila el precio consolida en un rango; al abrir la
sesión activa (Londres / NY) rompe ese rango con dirección. Colocamos la orden
al romper el borde, con SL/TP en múltiplos del tamaño del rango, y cerramos al
final de la ventana de la sesión si sigue abierta.

IMPORTANTE (honestidad): en forex esta estrategia es ~break-even; NO tiene edge
por sí sola. Su propósito es generar MUCHAS operaciones (volumen) con sangrado
mínimo. Solo es rentable si tu cuenta paga rebate por lote > (spread + ~1 USD).
Úsala en cuenta ECN/raw de spread bajo. Corre sobre la base D1 rentable.

Las horas de sesión son en HORA DEL SERVIDOR MT5 (las mismas que trae
copy_rates), así que coinciden con el backtest hecho sobre los datos descargados
por el mismo pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import mt5_client as mc
from config import CONFIG, DATA_DIR
from executor import SB_MAGIC, close_position, open_breakout, sb_positions
from journal import Journal

BARS_LOOKBACK = 48  # velas H1 (~2 días) para reconstruir sesiones del día
PIP = 0.0001


def parse_sessions(spec: str) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """'0-7:8-12,9-13:13-17' -> [((0,7),(8,12)), ((9,13),(13,17))]."""
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        rng, trade = part.split(":")
        r0, r1 = (int(x) for x in rng.split("-"))
        t0, t1 = (int(x) for x in trade.split("-"))
        out.append(((r0, r1), (t0, t1)))
    return out


# Estados de un slot (un símbolo en una sesión concreta, durante un día)
WAITING, ARMED, IN_TRADE, DONE = "waiting", "armed", "in_trade", "done"


@dataclass
class Slot:
    symbol: str
    sess_idx: int
    range_win: tuple[int, int]
    trade_win: tuple[int, int]
    magic: int
    active_date: object = None
    state: str = WAITING
    up: float = 0.0
    dn: float = 0.0
    R: float = 0.0
    ticket: int = 0
    last_bar_time: pd.Timestamp | None = None


@dataclass
class GlobalState:
    running: bool = True
    daily_max_loss: float = CONFIG.sb_daily_max_loss  # % de la cuenta
    cap_per_unit: float = 0.0  # presente para compatibilidad con /cap (no se usa)
    lot: float = CONFIG.sb_lot
    day_key: str = ""
    day_start_equity: float = 0.0
    killed_today: bool = False


class SessionBreakerTrader:
    def __init__(self):
        self.state = GlobalState()
        self.journal = Journal(DATA_DIR / "journal_sessionbreaker.db")
        self.symbols = [s.strip() for s in CONFIG.sb_symbols.split(",") if s.strip()]
        self.sessions = parse_sessions(CONFIG.sb_sessions)
        self.sl_mult = CONFIG.sb_sl_mult
        self.tp_mult = CONFIG.sb_tp_mult
        self.buffer = CONFIG.sb_buffer_pips * PIP
        # un slot por (símbolo, sesión); cada sesión su propio magic para no mezclar
        self.slots: list[Slot] = []
        for sym in self.symbols:
            for i, (rw, tw) in enumerate(self.sessions):
                self.slots.append(Slot(sym, i, rw, tw, SB_MAGIC + i))

    # ---------- datos ----------
    def _bars(self, symbol: str) -> pd.DataFrame:
        rates = mc.mt5.copy_rates_from_pos(symbol, mc.timeframe_const("H1"), 0, BARS_LOOKBACK)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"Sin datos H1 de {symbol}: {mc.mt5.last_error()}")
        df = pd.DataFrame(rates)
        t = pd.to_datetime(df["time"], unit="s", utc=True)
        df["hour"] = t.dt.hour
        df["date"] = t.dt.date
        df["ts"] = t
        return df.iloc[:-1]  # sin la vela en formación

    def account(self) -> dict:
        return mc.account_info()

    def prime(self):
        # marca los slots como ya evaluados hasta la última vela para no reaccionar a pasado
        for s in self.slots:
            try:
                df = self._bars(s.symbol)
                s.last_bar_time = df["ts"].iloc[-1]
                s.active_date = df["date"].iloc[-1]
                s.state = DONE  # arranca inactivo hoy; empieza limpio en la próxima sesión/día
            except Exception as e:
                print(f"[prime] {s.symbol} s{s.sess_idx}: {e}")

    # ---------- ciclo ----------
    def check(self) -> list[dict]:
        events: list[dict] = []
        acc = self.account()

        # Kill-switch GLOBAL de cuenta
        day_key = pd.Timestamp.utcnow().strftime("%Y-%m-%d")
        if day_key != self.state.day_key:
            self.state.day_key = day_key
            self.state.day_start_equity = acc["equity"]
            self.state.killed_today = False
        if not self.state.killed_today and self.state.day_start_equity > 0:
            dd = (self.state.day_start_equity - acc["equity"]) / self.state.day_start_equity * 100
            if dd >= self.state.daily_max_loss:
                self.state.killed_today = True
                self.state.running = False
                for p in sb_positions():
                    close_position(p["ticket"])
                return [{"type": "killswitch",
                         "text": f"🛑 Kill-switch: pérdida diaria {dd:.1f}%. Cerré todo y pausé."}]

        for s in self.slots:
            try:
                events += self._check_slot(s, acc)
            except Exception as e:
                events.append({"type": "error", "text": f"❌ {s.symbol} s{s.sess_idx}: {e}"})
        return events

    def _check_slot(self, s: Slot, acc: dict) -> list[dict]:
        events: list[dict] = []
        df = self._bars(s.symbol)
        last = df.iloc[-1]
        today = last["date"]
        cur_hour = int(last["hour"])

        # nuevo día -> reinicia el slot
        if s.active_date != today:
            s.active_date = today
            s.state = WAITING
            s.ticket = 0

        # solo procesamos lógica de ruptura una vez por vela nueva
        new_bar = s.last_bar_time is None or last["ts"] > s.last_bar_time
        s.last_bar_time = last["ts"]

        t0, t1 = s.trade_win
        r0, r1 = s.range_win

        # posición cerrada por el bróker (SL/TP)
        if s.state == IN_TRADE and s.ticket:
            still = any(p["ticket"] == s.ticket for p in sb_positions(s.magic))
            if not still:
                self._log_close(s.ticket)
                s.state = DONE
                s.ticket = 0
                events.append({"type": "closed",
                               "text": f"✅ {s.symbol} s{s.sess_idx}: cerrado por SL/TP."})
                return events

        # cierre por fin de ventana de sesión
        if s.state == IN_TRADE and cur_hour >= t1:
            for p in sb_positions(s.magic):
                if p["ticket"] == s.ticket:
                    close_position(p["ticket"])
                    self._log_close(p["ticket"])
            s.state = DONE
            s.ticket = 0
            events.append({"type": "closed",
                           "text": f"✅ {s.symbol} s{s.sess_idx}: cerrado a fin de sesión."})
            return events

        if s.state == DONE:
            return events

        # ¿estamos en la ventana de trading?
        if s.state == WAITING:
            if cur_hour >= t1:
                s.state = DONE
                return events
            if cur_hour < t0:
                return events  # aún no abre la sesión
            # dentro de la ventana: calcular rango del día
            rng = df[(df["date"] == today) & (df["hour"] >= r0) & (df["hour"] < r1)]
            if len(rng) < 2:
                s.state = DONE
                return events
            hi = float(rng["high"].max()); lo = float(rng["low"].min())
            s.R = hi - lo
            if s.R <= 0:
                s.state = DONE
                return events
            s.up = hi + self.buffer
            s.dn = lo - self.buffer
            s.state = ARMED

        # ARMED: buscar ruptura en la vela recién cerrada
        if s.state == ARMED and new_bar:
            if cur_hour >= t1:
                s.state = DONE
                return events
            direction = 0
            if last["high"] >= s.up:
                direction = 1
            elif last["low"] <= s.dn:
                direction = -1
            if direction != 0:
                events += self._open(s, direction, acc)
        return events

    def _open(self, s: Slot, direction: int, acc: dict) -> list[dict]:
        if not self.state.running:
            return []
        if not acc["is_demo"]:
            return [{"type": "confirm",
                     "text": f"⚠️ {s.symbol} s{s.sess_idx} ruptura en cuenta REAL. Confírmala."}]
        tick = mc.mt5.symbol_info_tick(s.symbol)
        if tick is None:
            return [{"type": "error", "text": f"❌ {s.symbol}: sin tick."}]
        entry = tick.ask if direction > 0 else tick.bid
        sl = entry - direction * self.sl_mult * s.R
        tp = entry + direction * self.tp_mult * s.R
        r = open_breakout(s.symbol, direction, self.state.lot, sl, tp, s.magic)
        if not r.ok:
            s.state = DONE
            return [{"type": "error", "text": f"❌ {s.symbol} s{s.sess_idx} no abrió: {r.message}"}]
        s.ticket = r.ticket
        s.state = IN_TRADE
        lado = "LONG" if direction > 0 else "SHORT"
        self.journal.log_open(
            ticket=r.ticket, symbol=s.symbol, timeframe="H1", direction=direction,
            bar_time=s.last_bar_time, pred_ret=0.0, threshold=0.0, confidence=0.0,
            votes={}, risk_pct=0.0, atr=s.R, entry=r.price, sl=sl, tp=tp, lot=r.volume)
        return [{"type": "opened",
                 "text": f"⚡ {s.symbol} s{s.sess_idx} {lado} ruptura  {r.volume} lote  "
                         f"(rango {s.R/PIP:.0f} pips)"}]

    def _log_close(self, ticket: int):
        profit = None
        try:
            deals = mc.mt5.history_deals_get(position=ticket)
            if deals:
                profit = round(sum(d.profit for d in deals), 2)
        except Exception:
            pass
        self.journal.log_close(ticket=ticket, close_price=None, profit=profit, exit_reason="sb")

    def close_all(self):
        res = []
        for p in sb_positions():
            res.append(close_position(p["ticket"]))
        for s in self.slots:
            if s.state == IN_TRADE:
                s.state = DONE
                s.ticket = 0
        return res

    # ---------- Telegram (interfaz duck-typed para build_pairs_application) ----------
    def status_text(self) -> str:
        acc = self.account()
        st = self.state
        abiertas = len(sb_positions())
        armados = sum(1 for s in self.slots if s.state == ARMED)
        lines = [
            "⚡ *Bot SESSION BREAKER (volumen)*",
            f"Modo: {'DEMO' if acc['is_demo'] else 'REAL'} | Trading: {'ON ✅' if st.running else 'PAUSA ⏸️'}",
            f"Balance: {acc['balance']:.2f} | Equity: {acc['equity']:.2f} {acc['currency']}",
            f"Lote: {st.lot} | Kill-switch: {st.daily_max_loss}%",
            f"Símbolos: {len(self.symbols)} × {len(self.sessions)} sesiones",
            f"Posiciones abiertas: {abiertas} | vigilando ruptura: {armados}",
        ]
        s = self.journal.stats()
        lines.append(f"Operaciones cerradas (histórico): {s['closed']}")
        if st.killed_today:
            lines.append("🛑 Kill-switch ACTIVADO hoy")
        return "\n".join(lines)

    def positions_text(self) -> str:
        out = ["📈 *Posiciones (Session Breaker)*"]
        pos = sb_positions()
        if not pos:
            return "No hay posiciones abiertas."
        for p in pos:
            out.append(f"{p['symbol']} {p['type']} {p['volume']} | P/L {p['profit']:+.2f}")
        return "\n".join(out)

    def journal_text(self) -> str:
        s = self.journal.stats()
        cur = mc.account_info().get("currency", "USD")
        return (f"📒 *Diario Session Breaker*\nCerradas: {s['closed']} | abiertas: {s['open']}\n"
                f"P/L total: {s['total_profit']:+.2f} {cur}")
