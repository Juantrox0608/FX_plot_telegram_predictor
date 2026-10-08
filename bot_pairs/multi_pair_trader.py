"""
Bot MULTI-PAR (MT5). Opera varios pares cointegrados a la vez, cada uno en D1
con su propia estrategia de z-score. Una estrategia de reversión del spread por par.

Pares históricos por defecto: EUR/GBP, AUD/NZD, USDCHF/USDCAD.
Cada par tiene su propio 'magic' para no mezclar posiciones.
Kill-switch de pérdida diaria a nivel de CUENTA (global).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import threading
import json
from datetime import datetime, timezone
from broker_time import ForexCalendar, NoticeGate, MarketClosed
from demo_license import demo_status
from pairs_safety import guard_account, fresh_tick, profile_lots, RuntimeStore, get_clock

import numpy as np
import pandas as pd

import mt5_client as mc
from config import CONFIG, DATA_DIR
from executor import PAIRS_MAGIC, close_pair, open_pair, pair_positions
from journal import Journal
from pairs_strategy import Action, PairsConfig, decide, zscore

LOOKBACK_BARS = max(120, CONFIG.pairs_lookback * 3)  # suficientes velas para el z-score


def pair_tag(a: str, b: str) -> str:
    """Etiqueta legible: EURUSD/GBPUSD -> EUR/GBP, USDCHF/USDCAD -> CHF/CAD."""
    a, b = a[:6], b[:6]
    ca = a.replace("USD", "") or a[:3]
    cb = b.replace("USD", "") or b[:3]
    return f"{ca}/{cb}"


def _parse_pairs(spec: str) -> list[tuple[str, str, int]]:
    out=[]; seen=set()
    for i,part in enumerate(spec.split(",")):
        parts=part.strip().split("-")
        if len(parts)!=2: raise ValueError("Par inválido")
        symbols=[]
        for raw in parts:
            raw=raw.strip()
            # Sufijo conserva mayúsculas/minúsculas del bróker.
            base=raw[:-len(CONFIG.symbol_suffix)] if CONFIG.symbol_suffix and raw.endswith(CONFIG.symbol_suffix) else raw
            base=base.upper()
            if len(base)!=6 or not base.isalpha(): raise ValueError("Símbolo inválido")
            symbols.append(base+CONFIG.symbol_suffix)
        a,b=symbols; key=tuple(sorted(symbols))
        if a==b or key in seen: raise ValueError("Par repetido/invertido o patas iguales")
        seen.add(key)
        out.append((a,b,CONFIG.pairs_magic_base+i))
    return out


# Pares a operar desde configuración; plantilla D1 del cliente separada.
PAIRS = _parse_pairs(CONFIG.pairs_list)


@dataclass
class Slot:
    a: str
    b: str
    magic: int
    cfg: PairsConfig
    last_bar_time: pd.Timestamp | None = None
    last_z: float | None = None
    last_corr: float | None = None
    last_update: pd.Timestamp | None = None  # wallclock del último recálculo
    signal_dir: int = 0                       # posición virtual (modo señales)
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
        self._lock = threading.RLock()
        self.clock = get_clock(mc.mt5)
        self.calendar = ForexCalendar.from_file(CONFIG.broker_sessions_file)
        self.notices = NoticeGate()
        self._license_blocks = False
        self.license_reader = lambda: demo_status(state_path=Path(CONFIG.demo_license_file) if CONFIG.demo_license_file else DATA_DIR / "demo_activation.json")
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        scope = json.dumps([CONFIG.instance_id, CONFIG.mt5_login, CONFIG.mt5_server,
                            CONFIG.mt5_path, CONFIG.pairs_magic_base, PAIRS,
                            CONFIG.pairs_timeframe, CONFIG.pairs_lookback,
                            CONFIG.pairs_entry_z, CONFIG.pairs_exit_z, CONFIG.pairs_stop_z,
                            CONFIG.pairs_min_corr, CONFIG.pairs_risk_profile, CONFIG.pairs_size_by_z])
        self.runtime = RuntimeStore(DATA_DIR / "journal_multipair.db", scope)
        self.state.running = self.runtime.get("running", True)
        self.journal = Journal(DATA_DIR / "journal_multipair.db")
        self.slots = [
            Slot(a, b, magic, PairsConfig(
                symbol_a=a, symbol_b=b, lookback=CONFIG.pairs_lookback,
                entry_z=CONFIG.pairs_entry_z, exit_z=CONFIG.pairs_exit_z,
                stop_z=CONFIG.pairs_stop_z, min_corr=CONFIG.pairs_min_corr))
            for a, b, magic in PAIRS
        ]

    # ---------- datos ----------
    def _daily(self, symbol: str) -> pd.DataFrame:
        # Asegura el símbolo en Market Watch (si no, copy_rates falla en terminales nuevos).
        guard_account(mc.mt5)
        if not mc.mt5.symbol_select(symbol, True):
            raise RuntimeError("Símbolo no disponible")
        if self.clock.offset_seconds is None: self.clock.refresh(mc.mt5, sorted({x for a,b,_ in PAIRS for x in (a,b)}))
        if not self.calendar.is_open(symbol, self.server_now()): raise MarketClosed("Sesión cerrada")
        fresh_tick(mc.mt5, symbol, self.clock)
        tf = mc.timeframe_const(CONFIG.pairs_timeframe)
        rates = mc.mt5.copy_rates_from_pos(symbol, tf, 1, LOOKBACK_BARS)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"Sin datos {CONFIG.pairs_timeframe} de {symbol}: {mc.mt5.last_error()}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        if df["time"].duplicated().any() or not df["time"].is_monotonic_increasing:
            raise RuntimeError("Velas duplicadas/desordenadas")
        if not np.isfinite(df["close"]).all() or (df["close"] <= 0).any():
            raise RuntimeError("Precios inválidos")
        forming=mc.mt5.copy_rates_from_pos(symbol, tf, 0, 1)
        if forming is None or not len(forming): raise RuntimeError("Sin vela en formación")
        forming_time=pd.to_datetime(forming[0]["time"], unit="s", utc=True)
        self.calendar.validate_bars(symbol,CONFIG.pairs_timeframe,df["time"].iloc[-1],forming_time,self.server_now())
        return df  # start_pos=1: excluye explícitamente la vela en formación

    def _pair_frame(self, slot: Slot) -> pd.DataFrame:
        da = self._daily(slot.a)[["time", "close"]].rename(columns={"close": "a"})
        db = self._daily(slot.b)[["time", "close"]].rename(columns={"close": "b"})
        if da["time"].iloc[-1] != db["time"].iloc[-1]:
            raise RuntimeError("Patas desincronizadas")
        if not da["time"].equals(db["time"]):
            raise RuntimeError("Faltan velas de una pata; no se rellena ni cruza historia desigual")
        return da.merge(db, on="time", how="inner").reset_index(drop=True)

    def account(self) -> dict:
        return mc.account_info()

    @staticmethod
    def _zcorr(j: pd.DataFrame, cfg: PairsConfig) -> tuple[float, float]:
        z, _ = zscore(j["a"], j["b"], cfg)
        corr = np.log(j["a"]).diff().rolling(cfg.lookback).corr(np.log(j["b"]).diff())
        return float(z.iloc[-1]), float(corr.iloc[-1])

    def prime(self):
        for s in self.slots:
            try:
                j = self._pair_frame(s)
                saved=self.runtime.get(f"processed:{s.magic}")
                s.last_bar_time=pd.Timestamp(saved) if saved else None
                # Calcula z/corr YA, para que /status muestre valores frescos al arrancar
                s.last_z, s.last_corr = self._zcorr(j, s.cfg)
                s.last_update = pd.Timestamp.now(tz="UTC")
                s.known_tickets = {p["ticket"] for p in pair_positions(s.magic)}
            except Exception as e:
                print(f"[prime] {s.a}/{s.b}: {type(e).__name__}; revisar datos/terminal")

    def _pos(self, slot: Slot) -> int:
        positions=pair_positions(slot.magic)
        if not positions: return 0
        if len(positions)!=2 or {p["symbol"] for p in positions}!={slot.a,slot.b}:
            raise RuntimeError("Pata huérfana/posiciones inesperadas; cierre pendiente")
        a=next(p for p in positions if p["symbol"]==slot.a)
        b=next(p for p in positions if p["symbol"]==slot.b)
        if a["type"]==b["type"]: raise RuntimeError("Patas en el mismo sentido; cierre pendiente")
        return 1 if a["type"]=="BUY" else -1

    def _lots(self, slot, balance, z_entry):
        guard_account(mc.mt5, orders=True)
        ticks=[fresh_tick(mc.mt5,sym) for sym in (slot.a,slot.b)]
        specs=[mc.mt5.symbol_info(sym) for sym in (slot.a,slot.b)]
        if any(spec is None for spec in specs): raise RuntimeError("Specs no disponibles")
        multiplier=min(2.5,max(1.,abs(z_entry)/CONFIG.pairs_entry_z)) if CONFIG.pairs_size_by_z else 1.
        return profile_lots(CONFIG.pairs_risk_profile,balance,*specs,
                            (ticks[0].ask+ticks[0].bid)/2,(ticks[1].ask+ticks[1].bid)/2,multiplier)

    # ---------- ciclo ----------
    def server_now(self):
        return self.clock.server_now()

    def _license_events(self):
        try:
            license=self.license_reader()
            self._license_blocks=license.is_expired
            category="expired" if license.is_expired else "warning" if license.days_left<=15 else None
            if category:
                key=f"license:{category}:{license.expires_at.isoformat()}"
                if not self.runtime.get(key,False):
                    self.runtime.put(key,True)
                    return [{"type":"license","text":"Licencia vencida: entradas pausadas; se siguen gestionando salidas." if category=="expired" else f"Licencia: quedan {license.days_left} días. Preparar renovación."}]
            return []
        except Exception:
            self._license_blocks=True
            return [{"type":"license","text":"Licencia no verificable: entradas pausadas; salidas continúan."}] if self.notices.update("license","invalid") else []

    def check(self) -> list[dict]:
        with self._lock:
            try:
                events=self._check_locked()
                if self.notices.update("cycle",None): events.insert(0,{"type":"news","text":"Conexión y reloj recuperados."})
                return events
            except Exception as e:
                return [{"type":"error","text":f"Ciclo bloqueado ({type(e).__name__}): comprobar conexión/reloj."}] if self.notices.update("cycle",type(e).__name__) else []

    def _check_locked(self) -> list[dict]:
        guard_account(mc.mt5)
        # Con offset conocido, mercado cerrado no intenta calibrar ticks inmóviles.
        symbols=sorted({x for s in self.slots for x in (s.a,s.b)})
        if self.clock.offset_seconds is not None:
            if not any(self.calendar.is_open(x,self.server_now()) for x in symbols): return []
        else:
            # Antes de primera calibración, sábado/domingo temprano UTC están cerrados
            # en ambos offsets JustMarkets +2/+3; no inventar frescura de ticks.
            utc=datetime.now(timezone.utc).replace(tzinfo=None)
            if all(not any(self.calendar.is_open(x,utc+pd.Timedelta(hours=h)) for x in symbols) for h in (2,3)): return []
        self.clock.refresh(mc.mt5,symbols)
        acc=self.account()
        today=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
        daily=self.runtime.daily(today,acc["equity"],self.state.daily_max_loss)
        newly_killed=daily["killed"] and not self.state.killed_today
        self.state.day_key=today; self.state.day_start_equity=daily["start"]
        self.state.killed_today=daily["killed"]
        events=self._license_events()
        if daily["killed"]:
            self.pause()
            if not CONFIG.signals_only:
                for s in self.slots: self.runtime.pending(s.magic,True)
            if newly_killed: events.append({"type":"killswitch","text":"Freno diario activado; pausado. Comprobando cierre de cada pata."})
        for s in self.slots:
            if not self.calendar.is_open(s.a,self.server_now()) or not self.calendar.is_open(s.b,self.server_now()):
                continue
            try:
                slot_events=self._check_slot(s,acc)
                if any(e["type"]=="error" for e in slot_events):
                    if self.notices.update(s.magic,"pending_close"): events+=slot_events
                else:
                    if self.notices.update(s.magic,None): events.append({"type":"news","text":f"{pair_tag(s.a,s.b)}: incidencia resuelta."})
                    events+=slot_events
            except MarketClosed: pass
            except Exception as e:
                if self.notices.update(s.magic,type(e).__name__):
                    events.append({"type":"error","text":f"{pair_tag(s.a,s.b)}: {type(e).__name__}; ciclo bloqueado, revisar terminal."})
        return events

    def pause(self):
        with self._lock:
            self.state.running=False; self.runtime.put("running",False)

    def resume(self):
        with self._lock:
            guard_account(mc.mt5)
            daily=self.runtime.daily(pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d"),self.account()["equity"],self.state.daily_max_loss)
            if daily["killed"]: raise RuntimeError("El freno diario sigue activo; no reanudar hoy")
            self.state.running=True; self.runtime.put("running",True)

    def _finish_close(self,s):
        try:
            res=close_pair(s.magic)
            remaining=pair_positions(s.magic)
        except Exception:
            return [{"type":"error","text":f"{pair_tag(s.a,s.b)}: cierre pendiente; no se pudo verificar. Reintento en el próximo ciclo."}]
        self.runtime.pending(s.magic,bool(remaining))
        if remaining:
            return [{"type":"error","text":f"{pair_tag(s.a,s.b)}: quedan {len(remaining)} patas; cierre pendiente, reintentará."}]
        return [{"type":"closed","text":f"{pair_tag(s.a,s.b)}: cierre verificado, sin patas abiertas."}]

    def _check_slot(self, s: Slot, acc: dict) -> list[dict]:
        events = []
        # cierres detectados
        current = {p["ticket"] for p in pair_positions(s.magic)}
        for tk in (s.known_tickets - current):
            profit = self._closed_profit(tk)
            self.journal.log_close(ticket=tk, close_price=None, profit=profit, exit_reason="pair")
        s.known_tickets = current

        # Recuperación antes de pedir otra vela: reintenta incluso con datos congelados.
        if not CONFIG.signals_only:
            try: self._pos(s)
            except RuntimeError: self.runtime.pending(s.magic, True)
            if self.runtime.is_pending(s.magic): return self._finish_close(s)

        j = self._pair_frame(s)
        last_time = j["time"].iloc[-1]
        if s.last_bar_time is not None and last_time <= s.last_bar_time:
            return events  # sin vela nueva
        s.last_bar_time = last_time
        self.runtime.put(f"processed:{s.magic}",last_time.isoformat())

        z_now, corr_now = self._zcorr(j, s.cfg)
        s.last_z, s.last_corr = z_now, corr_now
        s.last_update = pd.Timestamp.now(tz="UTC")

        pos = s.signal_dir if CONFIG.signals_only else self._pos(s)
        action = decide(z_now, pos, s.cfg)
        tag = pair_tag(s.a, s.b)

        if action == Action.CLOSE:
            if CONFIG.signals_only:
                s.signal_dir = 0
                events.append({"type": "closed",
                               "text": f"🔔 *CERRAR {tag}* — el spread volvió (z={z_now:.2f}). "
                                       f"Cierra ambas patas."})
                return events
            self.runtime.pending(s.magic, True)
            return self._finish_close(s)

        if action in (Action.OPEN_LONG, Action.OPEN_SHORT):
            if not self.state.running or self.state.killed_today or self._license_blocks:
                return events
            if not np.isfinite(corr_now) or corr_now < s.cfg.min_corr:
                events.append({"type": "skip", "text": f"⏭️ {tag} señal (z={z_now:.2f}) pero corr baja ({corr_now:.2f})."})
                return events
            if CONFIG.signals_only:
                direction = 1 if action == Action.OPEN_LONG else -1
                s.signal_dir = direction
                legs = (f"🟢 Compra {s.a}  +  🔴 Vende {s.b}" if direction > 0
                        else f"🔴 Vende {s.a}  +  🟢 Compra {s.b}")
                lado = "LONG spread" if direction > 0 else "SHORT spread"
                events.append({"type": "opened",
                               "text": (f"📢 *SEÑAL {tag}* — {lado}\n{legs}\n"
                                        f"z={z_now:.2f} · corr={corr_now:.2f}\n"
                                        f"_Señal informativa. Opera bajo tu criterio; sal cuando llegue el aviso de CERRAR._")})
                return events
            if not acc["is_demo"] and not CONFIG.pairs_allow_real:
                events.append({"type": "confirm",
                               "text": f"⚠️ {tag} señal en cuenta REAL (z={z_now:.2f}) "
                                       f"(activa PAIRS_ALLOW_REAL=true para operar automático)."})
                return events
            direction = 1 if action == Action.OPEN_LONG else -1
            lot_a, lot_b = self._lots(s, acc["balance"], z_now)
            # Intención durable ANTES de la primera pata: un crash fuerza reconciliación.
            self.runtime.pending(s.magic, True)
            r = open_pair(direction, s.a, s.b, lot_a, s.magic, lot_b=lot_b)
            self.runtime.pending(s.magic, r.get("needs_close", not r["ok"]))
            if r["ok"]:
                lado = "LONG" if direction > 0 else "SHORT"
                for leg, sym in ((r["leg_a"], s.a), (r["leg_b"], s.b)):
                    s.known_tickets.add(leg.ticket)
                    self.journal.log_open(
                        ticket=leg.ticket, symbol=sym, timeframe=CONFIG.pairs_timeframe, direction=direction if sym == s.a else -direction,
                        bar_time=last_time, pred_ret=z_now, threshold=s.cfg.entry_z,
                        confidence=corr_now, votes={}, risk_pct=0.0, atr=0.0,
                        entry=leg.price, sl=0.0, tp=0.0, lot=leg.volume)
                events.append({"type": "opened",
                               "text": f"📊 {tag} {lado} spread abierto ✅  z={z_now:.2f} corr={corr_now:.2f} {lot_a}/{lot_b} lotes A/B"})
            else:
                events.append({"type": "error", "text": f"❌ {tag} no abrió: {r['message']}"})
        return events

    def _closed_profit(self, ticket: int):
        try:
            deals = mc.mt5.history_deals_get(position=ticket)
            if deals:
                return round(sum(d.profit + getattr(d,"swap",0.) + getattr(d,"commission",0.) + getattr(d,"fee",0.) for d in deals), 2)
        except Exception:
            pass
        return None

    def close_all(self):
        with self._lock:
            self.pause()
            results=[]
            for s in self.slots:
                self.runtime.pending(s.magic,True)
                results+=close_pair(s.magic)
                self.runtime.pending(s.magic,bool(pair_positions(s.magic)))
            return results

    # ---------- Telegram ----------
    def status_text(self) -> str:
        acc = self.account()
        st = self.state
        # Fecha de la última vela D1 procesada (revela datos "congelados")
        bar = next((s.last_bar_time for s in self.slots if s.last_bar_time is not None), None)
        bar_txt = pd.Timestamp(bar).strftime("%Y-%m-%d") if bar is not None else "—"
        stale = ""
        if bar is not None:
            age_days = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(bar)).days
            if age_days >= 3:
                stale = f"  · antigüedad calendario {age_days}d; validar sesiones del servidor"
        lines = [
            "🤖 *Bot MULTI-PAR (MT5)*",
            f"Modo: {'DEMO' if acc['is_demo'] else 'REAL'} | Trading: {'ON ✅' if st.running else 'PAUSA ⏸️'}",
            f"Balance: {acc['balance']:.2f} | Equity: {acc['equity']:.2f} {acc['currency']}",
            f"Licencia: {'entradas pausadas' if self._license_blocks else 'vigencia por comprobar/activa'}",
            f"Perfil: {CONFIG.pairs_risk_profile} | Kill-switch: {st.daily_max_loss}%",
            f"exit_z: {CONFIG.pairs_exit_z} | size_by_z: {'ON' if CONFIG.pairs_size_by_z else 'OFF'}",
            f"Última vela {CONFIG.pairs_timeframe}: {bar_txt}{stale}",
            "",
        ]
        for s in self.slots:
            try: pv = s.signal_dir if CONFIG.signals_only else self._pos(s)
            except RuntimeError: pv = 2
            pos = {0: "sin par", 1: "LONG", -1: "SHORT", 2: "CIERRE PENDIENTE"}[pv]
            zc = f"z={s.last_z:.2f} corr={s.last_corr:.2f}" if s.last_z is not None else "—"
            lines.append(f"• {pair_tag(s.a, s.b)}: {pos} | {zc}")
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
