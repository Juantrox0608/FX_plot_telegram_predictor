"""Controles del multi-par. Sin credenciales, red ni inicialización de MT5."""
from decimal import Decimal, ROUND_FLOOR
import math
import json
import sqlite3
import time
from pathlib import Path
from config import CONFIG

PROFILES = {"conservador": .01, "moderado": .02, "agresivo": .03}

def floor_step(value, step):
    return float((Decimal(str(value))/Decimal(str(step))).to_integral_value(rounding=ROUND_FLOOR)*Decimal(str(step)))

def profile_lots(profile, balance, spec_a, spec_b, price_a, price_b, multiplier=1.):
    if profile not in PROFILES or not math.isfinite(balance) or balance < 1000:
        raise ValueError("Perfil inválido o balance inferior a US$1.000")
    if not math.isfinite(multiplier) or not 1 <= multiplier <= 2.5:
        raise ValueError("Multiplicador inválido")
    specs=(spec_a,spec_b); prices=(price_a,price_b)
    notionals=[]
    for s,price in zip(specs,prices):
        if not all(math.isfinite(v) and v>0 for v in (price,s.trade_contract_size,s.volume_step,s.volume_min,s.volume_max)):
            raise ValueError("Specs o precios inválidos")
        if s.currency_profit=="USD": n=s.trade_contract_size*price
        elif s.currency_base=="USD": n=s.trade_contract_size
        else: raise ValueError("Solo mayores con USD; no se inventa conversión")
        notionals.append(n)
    # Perfil sobre la pata con mayor nocional; jamás elevar al mínimo.
    base=float(Decimal(str(PROFILES[profile]))*Decimal(str(balance))/Decimal("1000")*Decimal(str(multiplier)))
    anchor=0 if notionals[0]>=notionals[1] else 1
    base=floor_step(base,specs[anchor].volume_step)
    target=base*notionals[anchor]
    lots=tuple(base if i==anchor else floor_step(target/n,s.volume_step) for i,(n,s) in enumerate(zip(notionals,specs)))
    for lot,s in zip(lots,specs):
        if lot<s.volume_min or lot>s.volume_max: raise ValueError("Lote fuera de mínimos/máximos; no se aumenta")
    return lots

def guard_account(mt5, *, orders=False):
    terminal=mt5.terminal_info()
    acc=mt5.account_info()
    if terminal is None or not terminal.connected or acc is None:
        raise RuntimeError("MT5 desconectado; sin órdenes")
    if not CONFIG.mt5_login or not CONFIG.mt5_server or acc.login!=CONFIG.mt5_login or acc.server!=CONFIG.mt5_server:
        raise RuntimeError("Cuenta/servidor distintos de los esperados")
    terminal_path=getattr(terminal,"path","")
    if not terminal_path or Path(terminal_path).resolve().as_posix().casefold() != Path(CONFIG.mt5_path).resolve().parent.as_posix().casefold():
        raise RuntimeError("Terminal distinto del MT5_PATH esperado")
    if acc.currency!="USD" or acc.margin_mode!=getattr(mt5,"ACCOUNT_MARGIN_MODE_RETAIL_HEDGING",2):
        raise RuntimeError("Se requiere cuenta USD con cobertura (hedging)")
    if orders:
        if CONFIG.signals_only: raise RuntimeError("SIGNALS_ONLY: órdenes bloqueadas")
        if acc.trade_mode!=getattr(mt5,"ACCOUNT_TRADE_MODE_DEMO",0) and not CONFIG.pairs_allow_real:
            raise RuntimeError("Cuenta real sin PAIRS_ALLOW_REAL")
        if not terminal.trade_allowed or terminal.tradeapi_disabled or not acc.trade_allowed:
            raise RuntimeError("Trading deshabilitado en el terminal/cuenta")
    return acc

def fresh_tick(mt5,symbol):
    tick=mt5.symbol_info_tick(symbol)
    if tick is None or not all(math.isfinite(v) and v>0 for v in (tick.ask,tick.bid)) or tick.ask<tick.bid:
        raise RuntimeError("Cotización inválida")
    age=time.time()-tick.time
    if age < -5 or age > CONFIG.pairs_max_tick_age:
        raise RuntimeError("Cotización congelada/futura; sin órdenes")
    return tick

class RuntimeStore:
    """Estado duradero ligado a cuenta, servidor y configuración de pares."""
    def __init__(self,path,scope):
        self.path=str(path)
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self.conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS runtime (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            row=c.execute("SELECT value FROM runtime WHERE key='scope'").fetchone()
            if row and row[0]!=scope: raise RuntimeError("Base de datos pertenece a otra instancia/cuenta/configuración")
            c.execute("INSERT OR IGNORE INTO runtime VALUES ('scope',?)",(scope,))
    def conn(self): return sqlite3.connect(self.path,timeout=10)
    def get(self,key,default=None):
        with self.conn() as c:
            row=c.execute("SELECT value FROM runtime WHERE key=?",(key,)).fetchone()
        return json.loads(row[0]) if row else default
    def put(self,key,value):
        with self.conn() as c: c.execute("INSERT OR REPLACE INTO runtime VALUES (?,?)",(key,json.dumps(value,allow_nan=False)))
    def daily(self,day,equity,limit):
        if not math.isfinite(equity) or not math.isfinite(limit) or not 0<limit<=50:
            raise RuntimeError("Equity/límite diario inválidos")
        state=self.get("daily")
        if state is None or state["day"]!=day: state={"day":day,"start":equity,"killed":False}
        if equity <= 0 or state["start"] <= 0 or (state["start"]-equity)/state["start"]*100>=limit: state["killed"]=True
        self.put("daily",state)
        return state
    def pending(self,magic,value): self.put(f"pending:{magic}",bool(value))
    def is_pending(self,magic): return self.get(f"pending:{magic}",False)

class InstanceLock:
    """Bloqueo del SO: una instancia por carpeta Y terminal. Se libera al morir."""
    def __init__(self,data_dir,terminal):
        import hashlib
        import tempfile
        import os
        if not terminal: raise RuntimeError("MT5_PATH propio obligatorio")
        self.files=[]
        directory=Path(tempfile.gettempdir())/"cuanti_pairs_locks"
        directory.mkdir(exist_ok=True)
        try:
            for key in (str(Path(data_dir).resolve()).casefold(),str(Path(terminal).resolve()).casefold()):
                path=directory/(hashlib.sha256(key.encode()).hexdigest()+".lock")
                f=path.open("a+b"); self.files.append(f)
                f.seek(0); f.write(b"0"); f.flush(); f.seek(0)
                if os.name=="nt":
                    import msvcrt
                    msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.close()
            raise RuntimeError("Ya hay una instancia usando la carpeta o terminal") from None
    def close(self):
        for f in self.files: f.close()
        self.files=[]
    def __enter__(self): return self
    def __exit__(self,*a): self.close()
