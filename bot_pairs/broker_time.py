"""Reloj observado y calendario Forex en etiquetas de hora del servidor."""
from datetime import datetime, timezone, timedelta
import json
import logging
import math
import time
from pathlib import Path

class MarketClosed(RuntimeError): pass

class BrokerClock:
    def __init__(self,mode="auto",max_age=120,wait_seconds=2.,poll_seconds=.1):
        self.mode=str(mode); self.max_age=max_age
        self.wait_seconds=wait_seconds; self.poll_seconds=poll_seconds
        self.offset_seconds=None if self.mode=="auto" else self._explicit(self.mode)
    @staticmethod
    def _explicit(mode):
        hours=float(mode)
        if not math.isfinite(hours) or not -12<=hours<=14 or hours*2!=round(hours*2):
            raise ValueError("BROKER_UTC_OFFSET requiere auto o múltiplo de 0,5 h entre -12 y +14")
        return int(hours*3600)
    def refresh(self,mt5,symbols,now=None):
        terminal=mt5.terminal_info()
        if terminal is None or not terminal.connected: raise RuntimeError("MT5 desconectado")
        def stamp(tick): return getattr(tick,"time_msc",0)/1000 or tick.time
        initial={}
        for sym in symbols:
            if not mt5.symbol_select(sym,True): continue
            tick=mt5.symbol_info_tick(sym)
            if tick is not None and math.isfinite(stamp(tick)): initial[sym]=stamp(tick)
        if not initial: raise RuntimeError("Sin cotizaciones para verificar reloj")
        wall=time.time() if now is None else now
        if self.mode!="auto":
            if not any(-5<=wall+self.offset_seconds-ts<=self.max_age for ts in initial.values()):
                raise RuntimeError("Desfase explícito no concuerda con ticks frescos")
            return self.offset_seconds
        # Una cotización congelada hace una hora puede imitar otro desfase.
        # En auto exigir avance de un tick después de seleccionarlo, no solo redondear.
        deadline=time.monotonic()+self.wait_seconds
        while time.monotonic()<deadline:
            candidates=[]
            wall=time.time() if now is None else now
            for sym,old in initial.items():
                tick=mt5.symbol_info_tick(sym)
                if tick is None: continue
                ts=stamp(tick)
                if ts>old and ts-old<=max(10.,self.wait_seconds+5.):
                    offset=int(round((ts-wall)/1800)*1800)
                    if -43200<=offset<=50400 and -5<=wall+offset-ts<=self.max_age:
                        candidates.append((ts,offset))
            if candidates:
                self.offset_seconds=max(candidates)[1]
                return self.offset_seconds
            if self.poll_seconds: time.sleep(self.poll_seconds)
        raise RuntimeError("Reloj no verificable: ticks sin avance; sin órdenes")
    def server_now(self,now=None):
        if self.offset_seconds is None: raise RuntimeError("Reloj no calibrado")
        return datetime.fromtimestamp((time.time() if now is None else now)+self.offset_seconds,timezone.utc).replace(tzinfo=None)
    def age(self,tick,now=None):
        if self.offset_seconds is None: raise RuntimeError("Reloj no calibrado")
        return (time.time() if now is None else now)+self.offset_seconds-tick.time

class ForexCalendar:
    """Horario publicado de JustMarkets + excepciones por símbolo/fecha verificadas."""
    def __init__(self,overrides=None):
        self.overrides=overrides or {}
    @classmethod
    def from_file(cls,path):
        if not path: return cls()
        p=Path(path)
        if p.name.startswith(".env") or p.suffix.lower() in (".key",".pem"):
            raise ValueError("Calendario debe ser JSON público sin secretos")
        data=json.loads(p.read_text(encoding="utf-8"))
        if "sesiones_confirmadas" in data:
            if set(data)-{"nota","pendientes","sesiones_confirmadas"}: raise ValueError("Calendario con campos desconocidos")
            data=data["sesiones_confirmadas"]
            if not isinstance(data,dict): raise ValueError("Sesiones confirmadas deben ser mapa")
        for sym,dates in data.items():
            for date,intervals in dates.items():
                datetime.fromisoformat(date)
                if any(not 0<=a<b<=1440 for a,b in intervals): raise ValueError("Sesión inválida")
        return cls(data)
    def intervals(self,symbol,date):
        override=self.overrides.get(symbol,{}).get(date.isoformat())
        if override is None: override=self.overrides.get("*",{}).get(date.isoformat())
        if override is not None: return override
        if date.weekday()>=5: return []
        return [[2 if date.weekday()==0 else 0,1439 if date.weekday()==4 else 1440]]
    def is_open(self,symbol,server_now):
        minute=server_now.hour*60+server_now.minute+server_now.second/60
        return any(a<=minute<b for a,b in self.intervals(symbol,server_now.date()))
    def previous_trading_date(self,symbol,date):
        for i in range(1,370):
            d=date-timedelta(days=i)
            if self.intervals(symbol,d): return d
        raise RuntimeError("Calendario sin sesión anterior")
    def validate_bars(self,symbol,tf,last,forming,server_now):
        # Timestamps se interpretan como etiquetas de servidor, no UTC físicas.
        last=last.to_pydatetime().replace(tzinfo=None)
        forming=forming.to_pydatetime().replace(tzinfo=None)
        if forming>server_now+timedelta(seconds=5) or last>=forming:
            raise RuntimeError("Velas futuras o sin cierre")
        if tf=="D1":
            expected=self.previous_trading_date(symbol,server_now.date())
            if forming.date()!=server_now.date() or last.date()!=expected:
                raise RuntimeError("Historia D1 congelada o falta sesión cerrada")
        else:
            hours={"H4":4,"H1":1,"M30":.5,"M15":.25,"M5":1/12,"M1":1/60}[tf]
            delta=timedelta(hours=hours)
            if server_now-forming>delta+timedelta(minutes=2): raise RuntimeError("Vela en formación congelada")
            candidate=forming-delta
            for _ in range(11000):
                if any(a<candidate.hour*60+candidate.minute+hours*60 and candidate.hour*60+candidate.minute<b
                       for a,b in self.intervals(symbol,candidate.date())): break
                candidate-=delta
            if last<candidate: raise RuntimeError("Falta última vela cerrada de sesión")
        return (server_now-last).total_seconds()/3600

class NoticeGate:
    def __init__(self,grace_seconds=0):
        self.states={};self.grace_seconds=grace_seconds
    def update(self,key,state,now=None):
        now=time.monotonic() if now is None else now
        old=self.states.get(key)
        log=logging.getLogger(__name__)
        if state is None:
            if old is None: return False
            del self.states[key]
            log.info("Bloqueo %s recuperado tras %.0f segundos",key,now-old["start"])
            return old["notified"]
        changed=old is None or old["state"]!=state
        if old is None: old={"state":state,"start":now,"last":None,"notified":False}
        if changed:
            log.info("Bloqueo %s: %s",key,state)
            old["state"]=state
        self.states[key]=old
        if now-old["start"]<self.grace_seconds: return False
        if changed or not old["notified"] or now-old["last"]>=3600:
            old["last"]=now;old["notified"]=True
            return True
        return False


def configure_notice_logging(directory):
    from logging.handlers import RotatingFileHandler
    path=Path(directory)/"avisos.log";path.parent.mkdir(parents=True,exist_ok=True)
    log=logging.getLogger(__name__)
    if not any(getattr(h,"baseFilename",None)==str(path.resolve()) for h in log.handlers):
        handler=RotatingFileHandler(path,maxBytes=2_000_000,backupCount=3,encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
    log.setLevel(logging.INFO);log.propagate=False
