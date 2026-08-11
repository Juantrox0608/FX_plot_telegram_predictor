"""
Núcleo de trading en vivo (independiente de Telegram, para poder probarlo).

Responsabilidades:
  • Detectar cada nueva vela H1 CERRADA.
  • Calcular la señal (IA + indicadores) sobre datos cerrados.
  • En demo con auto-open activado: abrir la operación y dimensionar el riesgo.
  • Una posición a la vez (como en el backtest).
  • Kill-switch de pérdida diaria (cierra todo y detiene por el día).
  • Devolver "eventos" para que la capa de Telegram mande alertas.

Seguridad: solo auto-abre si la cuenta es DEMO. En real, marca la señal como
pendiente de confirmación (no abre sola).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import mt5_client as mc
from config import CHECKPOINT_DIR, CONFIG, DATA_DIR
from executor import close_all, get_positions, open_from_signal
from journal import Journal
from news import NewsFilter
from strategy import Signal, compute_signal, load_model

BARS_LOOKBACK = 300  # velas H1 que traemos para indicadores + ventana


@dataclass
class TraderState:
    running: bool = True            # trading activado (se puede pausar)
    base_risk: float = CONFIG.risk_percent
    max_risk: float = 5.0
    daily_max_loss: float = CONFIG.daily_max_loss_percent
    mode: str = CONFIG.mode
    last_bar_time: pd.Timestamp | None = None
    day_key: str = ""
    day_start_equity: float = 0.0
    killed_today: bool = False
    known_tickets: set[int] = field(default_factory=set)
    last_signal: Signal | None = None


class Trader:
    def __init__(self, symbol: str = CONFIG.symbol, timeframe: str = CONFIG.timeframe):
        self.symbol = symbol
        self.timeframe = timeframe
        self.state = TraderState()
        self.news = NewsFilter()
        self.journal = Journal(DATA_DIR / "journal.db")
        ckpt_path = CHECKPOINT_DIR / f"{symbol}_{timeframe}_4y_gru.pt"
        self.model, self.ckpt = load_model(ckpt_path)

    # ---------- utilidades MT5 ----------
    def _timeframe(self):
        return mc.timeframe_const(self.timeframe)

    def _recent_closed_bars(self) -> pd.DataFrame:
        """Últimas velas SIN la vela en formación (la última cerrada es -1 aquí)."""
        rates = mc.mt5.copy_rates_from_pos(self.symbol, self._timeframe(), 0, BARS_LOOKBACK)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"Sin datos de {self.symbol}: {mc.mt5.last_error()}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df.iloc[:-1].reset_index(drop=True)  # descarta la vela en formación

    def account(self) -> dict:
        return mc.account_info()

    def prime(self) -> None:
        """
        Marca la última vela cerrada como 'ya vista' para que el bot solo opere
        en la PRÓXIMA vela que cierre (entrada limpia, como en el backtest, no
        sobre una vela que cerró hace rato). También sincroniza posiciones.
        """
        df = self._recent_closed_bars()
        self.state.last_bar_time = df["time"].iloc[-1]
        self.state.known_tickets = {p["ticket"] for p in get_positions(self.symbol)}

    # ---------- ciclo principal ----------
    def check(self) -> list[dict]:
        """
        Ejecuta un ciclo: detecta vela nueva, evalúa señal y (si corresponde)
        opera. Devuelve una lista de eventos [{type, text, ...}].
        """
        events: list[dict] = []

        # Cierres detectados (posiciones que desaparecieron desde el último ciclo)
        events += self._detect_closes()

        df = self._recent_closed_bars()
        last_time = df["time"].iloc[-1]

        # Reset diario del kill-switch y equity de referencia
        acc = self.account()
        day_key = last_time.strftime("%Y-%m-%d")
        if day_key != self.state.day_key:
            self.state.day_key = day_key
            self.state.day_start_equity = acc["equity"]
            self.state.killed_today = False

        # Kill-switch por pérdida diaria
        if not self.state.killed_today and self.state.day_start_equity > 0:
            dd = (self.state.day_start_equity - acc["equity"]) / self.state.day_start_equity * 100
            if dd >= self.state.daily_max_loss:
                self.state.killed_today = True
                self.state.running = False
                close_all(self.symbol)
                events.append({"type": "killswitch",
                               "text": f"🛑 Kill-switch: pérdida diaria {dd:.1f}% ≥ "
                                       f"{self.state.daily_max_loss}%. Cerré todo y pausé el trading por hoy."})
                return events

        # ¿Vela nueva?
        if self.state.last_bar_time is not None and last_time <= self.state.last_bar_time:
            return events  # nada nuevo
        self.state.last_bar_time = last_time

        # Calcular señal sobre datos cerrados
        sig = compute_signal(df, self.model, self.ckpt,
                             base_risk=self.state.base_risk, max_risk=self.state.max_risk)
        self.state.last_signal = sig

        if sig.direction == 0:
            events.append({"type": "nosignal",
                           "text": f"🕐 {last_time:%Y-%m-%d %H:%M} — sin señal ({sig.reason})"})
            return events

        lado = "COMPRA 🟢" if sig.direction > 0 else "VENTA 🔴"
        # ¿Ya hay una posición abierta del bot? -> una a la vez
        if any(p["magic"] == 20260713 for p in get_positions(self.symbol)):
            events.append({"type": "skip",
                           "text": f"⏭️ Señal {lado} pero ya hay una posición abierta. Espero."})
            return events

        if not self.state.running:
            events.append({"type": "paused", "text": f"⏸️ Señal {lado} pero el trading está pausado."})
            return events

        # Filtro de noticias: no operar cerca de eventos de alto impacto
        blocked, why = self.news.is_blocked(self.symbol)
        if blocked:
            events.append({"type": "news",
                           "text": f"📰 Señal {lado} bloqueada por noticia de alto impacto: {why}"})
            return events

        # Solo auto-abrir en DEMO
        if not acc["is_demo"]:
            events.append({"type": "confirm",
                           "text": f"⚠️ Señal {lado} en cuenta REAL. No abro sola: confírmala manualmente."})
            return events

        # Abrir en demo
        res = open_from_signal(sig, self.symbol, acc["balance"])
        if res.ok:
            self.state.known_tickets.add(res.ticket)
            self.journal.log_open(
                ticket=res.ticket, symbol=self.symbol, timeframe=self.timeframe,
                direction=sig.direction, bar_time=last_time, pred_ret=sig.pred_ret,
                threshold=sig.threshold, confidence=sig.confidence, votes=sig.votes,
                risk_pct=sig.risk_pct, atr=sig.atr, entry=res.price,
                sl=res.sl, tp=res.tp, lot=res.volume,
            )
            events.append({"type": "opened",
                           "text": (f"{lado} {self.symbol} abierta ✅\n"
                                    f"Ticket {res.ticket} | {res.volume} lotes @ {res.price}\n"
                                    f"SL {res.sl} | TP {res.tp} | riesgo {sig.risk_pct}%\n"
                                    f"{sig.reason}")})
        else:
            events.append({"type": "error", "text": f"❌ No se pudo abrir: {res.message}"})
        return events

    def _detect_closes(self) -> list[dict]:
        events = []
        current = {p["ticket"] for p in get_positions(self.symbol)}
        closed = self.state.known_tickets - current
        for ticket in closed:
            profit, close_price = self._closed_result(ticket)
            self.journal.log_close(ticket=ticket, close_price=close_price,
                                   profit=profit, exit_reason=self._infer_exit(ticket, close_price))
            p = profit if profit is not None else 0.0
            emoji = "🟢" if p >= 0 else "🔴"
            events.append({"type": "closed",
                           "text": f"{emoji} Posición {ticket} cerrada. Resultado: {p:+.2f} USD"})
        self.state.known_tickets = current
        return events

    def _closed_result(self, ticket: int) -> tuple[float | None, float | None]:
        """Devuelve (profit total, precio de cierre) de los deals de la posición."""
        try:
            deals = mc.mt5.history_deals_get(position=ticket)
            if deals:
                profit = round(sum(d.profit for d in deals), 2)
                # el último deal (entry=OUT) trae el precio de cierre
                close_price = deals[-1].price
                return profit, close_price
        except Exception:
            pass
        return None, None

    def _infer_exit(self, ticket: int, close_price: float | None) -> str:
        """Deduce si salió por SL o TP comparando con los niveles guardados."""
        if close_price is None:
            return ""
        try:
            with self.journal._conn() as c:
                row = c.execute("SELECT sl, tp FROM trades WHERE ticket=?", (ticket,)).fetchone()
            if row and row[0] and row[1]:
                sl, tp = row
                return "tp" if abs(close_price - tp) <= abs(close_price - sl) else "sl"
        except Exception:
            pass
        return "manual"

    # ---------- consultas para Telegram ----------
    def status_text(self) -> str:
        acc = self.account()
        pos = get_positions(self.symbol)
        st = self.state
        lines = [
            f"🤖 *Estado del bot*",
            f"Símbolo: {self.symbol} ({self.timeframe})",
            f"Modo: {'DEMO' if acc['is_demo'] else 'REAL'} | Trading: {'ON ✅' if st.running else 'PAUSA ⏸️'}",
            f"Balance: {acc['balance']:.2f} | Equity: {acc['equity']:.2f} {acc['currency']}",
            f"Riesgo base: {st.base_risk}% (máx {st.max_risk}%) | Kill-switch diario: {st.daily_max_loss}%",
            f"Posiciones abiertas: {len(pos)}",
        ]
        if st.killed_today:
            lines.append("🛑 Kill-switch ACTIVADO hoy (trading detenido)")
        if st.last_signal:
            s = st.last_signal
            d = {1: "COMPRA", -1: "VENTA", 0: "ESPERAR"}[s.direction]
            lines.append(f"Última señal: {d} ({s.pred_ret*10000:+.1f} bp, conf {int(s.confidence*100)}%)")
        nxt = self.news.next_events(self.symbol, hours=12)
        if nxt:
            e = nxt[0]
            lines.append(f"Próxima noticia alto impacto: {e.currency} {e.title} @ {e.when:%H:%M UTC}")
        return "\n".join(lines)

    def journal_text(self) -> str:
        s = self.journal.stats()
        return (
            "📒 *Diario (datos para reentrenar la IA)*\n"
            f"Operaciones cerradas: {s['closed']} | abiertas: {s['open']}\n"
            f"Win rate: {s['win_rate']}% | P/L total: {s['total_profit']:+.2f} USD\n"
            f"Expectativa: {s['expectancy']:+.4f} USD/op\n"
            f"Base de datos: data/journal.db"
        )

    def positions_text(self) -> str:
        pos = get_positions(self.symbol)
        if not pos:
            return "No hay posiciones abiertas."
        out = ["📈 *Posiciones abiertas*"]
        for p in pos:
            out.append(f"#{p['ticket']} {p['type']} {p['volume']} @ {p['price_open']} "
                       f"| SL {p['sl']} TP {p['tp']} | P/L {p['profit']:+.2f}")
        return "\n".join(out)
