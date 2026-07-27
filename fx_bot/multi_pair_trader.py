"""
Bot MULTI-PAR (MT5). Opera varios pares cointegrados a la vez, cada uno en D1
con su propia estrategia de z-score. Más operaciones que un solo par, mismo edge.

Pares por defecto (validados en D1): EUR/GBP, AUD/NZD, USDCHF/USDCAD.
Cada par tiene su propio 'magic' para no mezclar posiciones.
Kill-switch de pérdida diaria a nivel de CUENTA (global).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import mt5_client as mc
from config import CONFIG, DATA_DIR
from demo_license import demo_status
from executor import PAIRS_MAGIC, close_pair, open_pair, pair_positions
from journal import Journal
from pairs_strategy import Action, PairsConfig, decide, zscore

LOOKBACK_BARS = 80

# Pares a operar: (símbolo A, símbolo B, magic único)
PAIRS = [
    ("EURUSD", "GBPUSD", PAIRS_MAGIC + 0),
    ("AUDUSD", "NZDUSD", PAIRS_MAGIC + 1),
    ("USDCHF", "USDCAD", PAIRS_MAGIC + 2),
]


@dataclass
class Slot:
    a: str
    b: str
    magic: int
    cfg: PairsConfig
    last_bar_time: pd.Timestamp | None = None
    last_z: float | None = None
    last_corr: float | None = None
    known_tickets: set[int] = field(default_factory=set)


@dataclass
class GlobalState:
    running: bool = True
    daily_max_loss: float = CONFIG.daily_max_loss_percent
    cap_per_unit: float = CONFIG.pairs_cap_per_unit
    day_key: str = ""
    day_start_equity: float = 0.0
    killed_today: bool = False


class MultiPairTrader:
    def __init__(self):
        self.state = GlobalState()
        self.journal = Journal(DATA_DIR / "journal_multipair.db")
        self.slots = [
            Slot(a, b, magic, PairsConfig(
                symbol_a=a, symbol_b=b, lookback=CONFIG.pairs_lookback,
                entry_z=CONFIG.pairs_entry_z, stop_z=CONFIG.pairs_stop_z,
                min_corr=CONFIG.pairs_min_corr))
            for a, b, magic in PAIRS
        ]

    # ---------- datos ----------
    def _daily(self, symbol: str) -> pd.DataFrame:
        rates = mc.mt5.copy_rates_from_pos(symbol, mc.timeframe_const("D1"), 0, LOOKBACK_BARS)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"Sin datos D1 de {symbol}: {mc.mt5.last_error()}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df.iloc[:-1]  # sin la vela en formación

    def _pair_frame(self, slot: Slot) -> pd.DataFrame:
        da = self._daily(slot.a)[["time", "close"]].rename(columns={"close": "a"})
        db = self._daily(slot.b)[["time", "close"]].rename(columns={"close": "b"})
        return da.merge(db, on="time", how="inner").reset_index(drop=True)

    def account(self) -> dict:
        return mc.account_info()

    def prime(self):
        for s in self.slots:
            try:
                j = self._pair_frame(s)
                s.last_bar_time = j["time"].iloc[-1]
                s.known_tickets = {p["ticket"] for p in pair_positions(s.magic)}
            except Exception as e:
                print(f"[prime] {s.a}/{s.b}: {e}")

    def _pos(self, slot: Slot) -> int:
        for p in pair_positions(slot.magic):
            if p["symbol"] == slot.a:
                return 1 if p["type"] == "BUY" else -1
        return 0

    def _lot(self, balance: float) -> float:
        units = max(1, int(balance // self.state.cap_per_unit))
        return round(units * 0.01, 2)

    # ---------- ciclo ----------
    def check(self) -> list[dict]:
        events: list[dict] = []

        if demo_status().is_expired:
            self.state.running = False
            return [{"type": "license", "text": f"Demo vencida. Trading pausado. {demo_status().summary()}"}]

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
                for s in self.slots:
                    close_pair(s.magic)
                return [{"type": "killswitch",
                         "text": f"🛑 Kill-switch: pérdida diaria {dd:.1f}%. Cerré TODOS los pares y pausé."}]

        # Cada par, independiente
        for s in self.slots:
            try:
                events += self._check_slot(s, acc)
            except Exception as e:
                events.append({"type": "error", "text": f"❌ {s.a}/{s.b}: {e}"})
        return events

    def _check_slot(self, s: Slot, acc: dict) -> list[dict]:
        events = []
        # cierres detectados
        current = {p["ticket"] for p in pair_positions(s.magic)}
        for tk in (s.known_tickets - current):
            profit = self._closed_profit(tk)
            self.journal.log_close(ticket=tk, close_price=None, profit=profit, exit_reason="pair")
        s.known_tickets = current

        j = self._pair_frame(s)
        last_time = j["time"].iloc[-1]
        if s.last_bar_time is not None and last_time <= s.last_bar_time:
            return events  # sin vela nueva
        s.last_bar_time = last_time

        z, _ = zscore(j["a"], j["b"], s.cfg)
        corr = np.log(j["a"]).diff().rolling(s.cfg.lookback).corr(np.log(j["b"]).diff())
        z_now = float(z.iloc[-1]); corr_now = float(corr.iloc[-1])
        s.last_z, s.last_corr = z_now, corr_now

        pos = self._pos(s)
        action = decide(z_now, pos, s.cfg)
        tag = f"{s.a[:3]}/{s.b[:3]}"

        if action == Action.CLOSE:
            res = close_pair(s.magic)
            ok = all(r.ok for r in res)
            events.append({"type": "closed" if ok else "error",
                           "text": f"{'✅' if ok else '⚠️'} {tag} cerrado (z={z_now:.2f})."})
            return events

        if action in (Action.OPEN_LONG, Action.OPEN_SHORT):
            if not self.state.running:
                return events
            if corr_now < s.cfg.min_corr:
                events.append({"type": "skip", "text": f"⏭️ {tag} señal (z={z_now:.2f}) pero corr baja ({corr_now:.2f})."})
                return events
            if not acc["is_demo"]:
                events.append({"type": "confirm", "text": f"⚠️ {tag} señal en cuenta REAL (z={z_now:.2f}). Confírmala."})
                return events
            direction = 1 if action == Action.OPEN_LONG else -1
            lot = self._lot(acc["balance"])
            r = open_pair(direction, s.a, s.b, lot, s.magic)
            if r["ok"]:
                lado = "LONG" if direction > 0 else "SHORT"
                for leg, sym in ((r["leg_a"], s.a), (r["leg_b"], s.b)):
                    s.known_tickets.add(leg.ticket)
                    self.journal.log_open(
                        ticket=leg.ticket, symbol=sym, timeframe="D1", direction=direction,
                        bar_time=last_time, pred_ret=z_now, threshold=s.cfg.entry_z,
                        confidence=corr_now, votes={}, risk_pct=0.0, atr=0.0,
                        entry=leg.price, sl=0.0, tp=0.0, lot=lot)
                events.append({"type": "opened",
                               "text": f"📊 {tag} {lado} spread abierto ✅  z={z_now:.2f} corr={corr_now:.2f} {lot} lotes/pata"})
            else:
                events.append({"type": "error", "text": f"❌ {tag} no abrió: {r['message']}"})
        return events

    def _closed_profit(self, ticket: int):
        try:
            deals = mc.mt5.history_deals_get(position=ticket)
            if deals:
                return round(sum(d.profit for d in deals), 2)
        except Exception:
            pass
        return None

    def close_all(self):
        res = []
        for s in self.slots:
            res += close_pair(s.magic)
            s.known_tickets = set()
        return res

    # ---------- Telegram ----------
    def status_text(self) -> str:
        acc = self.account()
        st = self.state
        lines = [
            "🤖 *Bot MULTI-PAR (MT5)*",
            f"Modo: {'DEMO' if acc['is_demo'] else 'REAL'} | Trading: {'ON ✅' if st.running else 'PAUSA ⏸️'}",
            f"Balance: {acc['balance']:.2f} | Equity: {acc['equity']:.2f} {acc['currency']}",
            f"Capital/lote: {st.cap_per_unit} | Kill-switch: {st.daily_max_loss}%",
            "",
        ]
        for s in self.slots:
            pos = {0: "sin par", 1: "LONG", -1: "SHORT"}[self._pos(s)]
            zc = f"z={s.last_z:.2f} corr={s.last_corr:.2f}" if s.last_z is not None else "—"
            lines.append(f"• {s.a[:3]}/{s.b[:3]}: {pos} | {zc}")
        if st.killed_today:
            lines.append("🛑 Kill-switch ACTIVADO hoy")
        return "\n".join(lines)

    def positions_text(self) -> str:
        out = ["📈 *Posiciones*"]
        any_pos = False
        for s in self.slots:
            for p in pair_positions(s.magic):
                any_pos = True
                out.append(f"{p['symbol']} {p['type']} {p['volume']} | P/L {p['profit']:+.2f}")
        return "\n".join(out) if any_pos else "No hay pares abiertos."

    def journal_text(self) -> str:
        s = self.journal.stats()
        return (f"📒 *Diario multi-par*\nPatas cerradas: {s['closed']} | abiertas: {s['open']}\n"
                f"P/L total: {s['total_profit']:+.2f} {mc.account_info().get('currency','USD')}")
