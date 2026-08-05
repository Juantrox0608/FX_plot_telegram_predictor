"""
Núcleo en vivo del bot de PAIRS (EURUSD vs GBPUSD).

Cada nueva vela D1 cerrada:
  • Baja el histórico diario de ambos pares y los alinea.
  • Calcula z-score del spread + correlación (config robusta validada).
  • Decide: abrir par / cerrar par / esperar. Filtro de correlación al abrir.
  • Sizing: 0.01 lote por pata por cada `cap_per_unit` de balance.
  • Kill-switch de pérdida diaria.
  • Registra en el diario y emite eventos para Telegram.

Solo auto-abre en cuenta DEMO (en real, marca para confirmar).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

import mt5_client as mc
from config import CONFIG, DATA_DIR
from demo_license import demo_status
from executor import PAIRS_MAGIC, close_pair, open_pair, pair_positions
from journal import Journal
from pairs_strategy import Action, PairsConfig, decide, zscore

LOOKBACK_BARS = 80  # velas D1 que traemos (suficiente para lookback + corr)


@dataclass
class PairsState:
    running: bool = True
    daily_max_loss: float = CONFIG.daily_max_loss_percent
    cap_per_unit: float = CONFIG.pairs_cap_per_unit
    last_bar_time: pd.Timestamp | None = None
    day_key: str = ""
    day_start_equity: float = 0.0
    killed_today: bool = False
    known_tickets: set[int] = field(default_factory=set)
    last_z: float | None = None
    last_corr: float | None = None


class PairsTrader:
    def __init__(self):
        self.a = CONFIG.pair_a
        self.b = CONFIG.pair_b
        self.cfg = PairsConfig(
            symbol_a=self.a, symbol_b=self.b, lookback=CONFIG.pairs_lookback,
            entry_z=CONFIG.pairs_entry_z, stop_z=CONFIG.pairs_stop_z,
            min_corr=CONFIG.pairs_min_corr,
        )
        self.state = PairsState()
        self.journal = Journal(DATA_DIR / "journal_pairs.db")

    # ---------- datos ----------
    def _daily(self, symbol: str) -> pd.DataFrame:
        tf = mc.timeframe_const("D1")
        rates = mc.mt5.copy_rates_from_pos(symbol, tf, 0, LOOKBACK_BARS)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"Sin datos D1 de {symbol}: {mc.mt5.last_error()}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df.iloc[:-1]  # descarta la vela diaria en formación

    def _pair_frame(self) -> pd.DataFrame:
        da = self._daily(self.a)[["time", "close"]].rename(columns={"close": "a"})
        db = self._daily(self.b)[["time", "close"]].rename(columns={"close": "b"})
        return da.merge(db, on="time", how="inner").reset_index(drop=True)

    def account(self) -> dict:
        return mc.account_info()

    def prime(self):
        j = self._pair_frame()
        self.state.last_bar_time = j["time"].iloc[-1]
        self.state.known_tickets = {p["ticket"] for p in pair_positions()}

    # ---------- estado de posición ----------
    def position_state(self) -> int:
        """0 sin par, +1 long-spread (long A), -1 short-spread (short A)."""
        for p in pair_positions():
            if p["symbol"] == self.a:
                return 1 if p["type"] == "BUY" else -1
        return 0

    def _units_lot(self, balance: float) -> float:
        units = max(1, int(balance // self.state.cap_per_unit))
        return round(units * 0.01, 2)

    # ---------- ciclo ----------
    def check(self) -> list[dict]:
        events = self._detect_closes()
        demo = demo_status()
        if demo.is_expired:
            self.state.running = False
            return [{
                "type": "license",
                "text": f"Demo vencida. Trading pausado. {demo.summary()}",
            }]
        j = self._pair_frame()
        last_time = j["time"].iloc[-1]
        acc = self.account()

        # reset diario / kill-switch
        day_key = last_time.strftime("%Y-%m-%d")
        if day_key != self.state.day_key:
            self.state.day_key = day_key
            self.state.day_start_equity = acc["equity"]
            self.state.killed_today = False
        if not self.state.killed_today and self.state.day_start_equity > 0:
            dd = (self.state.day_start_equity - acc["equity"]) / self.state.day_start_equity * 100
            if dd >= self.state.daily_max_loss:
                self.state.killed_today = True
                self.state.running = False
                close_pair()
                events.append({"type": "killswitch",
                               "text": f"🛑 Kill-switch: pérdida diaria {dd:.1f}%. Cerré el par y pausé."})
                return events

        # ¿vela nueva?
        if self.state.last_bar_time is not None and last_time <= self.state.last_bar_time:
            return events
        self.state.last_bar_time = last_time

        # señal
        z, _ = zscore(j["a"], j["b"], self.cfg)
        corr = np.log(j["a"]).diff().rolling(self.cfg.lookback).corr(np.log(j["b"]).diff())
        z_now = float(z.iloc[-1]); corr_now = float(corr.iloc[-1])
        self.state.last_z, self.state.last_corr = z_now, corr_now

        pos = self.position_state()
        action = decide(z_now, pos, self.cfg)

        if action == Action.CLOSE:
            res = close_pair()
            ok = all(r.ok for r in res)
            events.append({"type": "closed" if ok else "error",
                           "text": f"{'✅' if ok else '⚠️'} Par cerrado (z={z_now:.2f})."})
            return events

        if action in (Action.OPEN_LONG, Action.OPEN_SHORT):
            if not self.state.running:
                events.append({"type": "paused", "text": f"⏸️ Señal de par pero pausado (z={z_now:.2f})."})
                return events
            if corr_now < self.cfg.min_corr:
                events.append({"type": "skip",
                               "text": f"⏭️ Señal (z={z_now:.2f}) pero correlación baja ({corr_now:.2f}). No opero."})
                return events
            if not acc["is_demo"]:
                events.append({"type": "confirm",
                               "text": f"⚠️ Señal de par en cuenta REAL (z={z_now:.2f}). Confírmala manualmente."})
                return events

            direction = 1 if action == Action.OPEN_LONG else -1
            lot = self._units_lot(acc["balance"])
            r = open_pair(direction, self.a, self.b, lot)
            if r["ok"]:
                lado = "LONG spread (long %s/short %s)" % (self.a, self.b) if direction > 0 \
                    else "SHORT spread (short %s/long %s)" % (self.a, self.b)
                for leg, sym in ((r["leg_a"], self.a), (r["leg_b"], self.b)):
                    self.state.known_tickets.add(leg.ticket)
                    self.journal.log_open(
                        ticket=leg.ticket, symbol=sym, timeframe="D1", direction=direction,
                        bar_time=last_time, pred_ret=z_now, threshold=self.cfg.entry_z,
                        confidence=corr_now, votes={}, risk_pct=0.0, atr=0.0,
                        entry=leg.price, sl=0.0, tp=0.0, lot=lot)
                events.append({"type": "opened",
                               "text": (f"📊 Par abierto ✅ {lado}\n"
                                        f"z={z_now:.2f} | corr={corr_now:.2f} | {lot} lotes/pata")})
            else:
                events.append({"type": "error", "text": f"❌ No se pudo abrir el par: {r['message']}"})
        return events

    def _detect_closes(self) -> list[dict]:
        events = []
        current = {p["ticket"] for p in pair_positions()}
        closed = self.state.known_tickets - current
        total = 0.0
        for ticket in closed:
            profit = self._closed_profit(ticket)
            self.journal.log_close(ticket=ticket, close_price=None, profit=profit, exit_reason="pair")
            total += profit or 0.0
        if closed:
            emoji = "🟢" if total >= 0 else "🔴"
            events.append({"type": "closed",
                           "text": f"{emoji} Par cerrado. Resultado combinado: {total:+.2f} USD"})
        self.state.known_tickets = current
        return events

    def _closed_profit(self, ticket: int):
        try:
            deals = mc.mt5.history_deals_get(position=ticket)
            if deals:
                return round(sum(d.profit for d in deals), 2)
        except Exception:
            pass
        return None

    def close_all(self) -> list:
        return close_pair()

    # ---------- textos Telegram ----------
    def status_text(self) -> str:
        acc = self.account()
        st = self.state
        pos = self.position_state()
        estado = {0: "sin par", 1: "LONG spread", -1: "SHORT spread"}[pos]
        lines = [
            "🤖 *Bot PAIRS*",
            f"Par: {self.a} / {self.b} (D1)",
            f"Modo: {'DEMO' if acc['is_demo'] else 'REAL'} | Trading: {'ON ✅' if st.running else 'PAUSA ⏸️'}",
            f"Balance: {acc['balance']:.2f} | Equity: {acc['equity']:.2f} {acc['currency']}",
            f"Posición: {estado}",
            f"Entrada z=±{self.cfg.entry_z} | stop z=±{self.cfg.stop_z} | corr≥{self.cfg.min_corr}",
            f"Capital/lote: {st.cap_per_unit} | Kill-switch: {st.daily_max_loss}%",
            demo_status().summary(),
        ]
        if st.last_z is not None:
            lines.append(f"Última lectura: z={st.last_z:.2f}, corr={st.last_corr:.2f}")
        if st.killed_today:
            lines.append("🛑 Kill-switch ACTIVADO hoy")
        return "\n".join(lines)

    def positions_text(self) -> str:
        pos = pair_positions()
        if not pos:
            return "No hay par abierto."
        out = ["📈 *Patas abiertas*"]
        for p in pos:
            out.append(f"#{p['ticket']} {p['symbol']} {p['type']} {p['volume']} | P/L {p['profit']:+.2f}")
        out.append(f"P/L combinado: {sum(p['profit'] for p in pos):+.2f}")
        return "\n".join(out)

    def journal_text(self) -> str:
        s = self.journal.stats()
        return (f"📒 *Diario pairs*\nPatas cerradas: {s['closed']} | abiertas: {s['open']}\n"
                f"P/L total: {s['total_profit']:+.2f} USD\nBase: data/journal_pairs.db")
