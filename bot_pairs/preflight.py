"""Diagnóstico solo lectura: no dotenv, órdenes, Telegram, DB ni activación de licencia."""
from datetime import datetime, timezone
import math
import time
import pandas as pd
import numpy as np
from broker_time import BrokerClock, ForexCalendar
from pairs_strategy import PairsConfig, zscore

class ReadOnlyMT5:
    ALLOWED=frozenset({"terminal_info","account_info","symbol_select","symbol_info","symbol_info_tick",
                      "copy_rates_from_pos","order_calc_margin","TIMEFRAME_D1","TIMEFRAME_H4",
                      "TIMEFRAME_H1","TIMEFRAME_M30","TIMEFRAME_M15","TIMEFRAME_M5","TIMEFRAME_M1",
                      "ORDER_TYPE_BUY","ORDER_TYPE_SELL"})
    def __init__(self,client): self.__client=client
    def __getattr__(self,name):
        if name not in self.ALLOWED: raise RuntimeError("API no permitida en preflight")
        return getattr(self.__client,name)

def collect_report(client,cfg,now=None):
    from pairs_safety import profile_lots
    mt5=ReadOnlyMT5(client)
    acc=mt5.account_info(); terminal=mt5.terminal_info()
    from pathlib import Path
    verified=(acc is not None and terminal is not None and terminal.connected
              and bool(cfg.mt5_login) and acc.login==cfg.mt5_login and acc.server==cfg.mt5_server
              and bool(cfg.mt5_path) and Path(terminal.path).resolve()==Path(cfg.mt5_path).resolve().parent
              and acc.currency=="USD" and acc.margin_mode==2 and acc.trade_mode==0)
    report={"readonly":True,"account_checks":bool(verified),"pairs":[],"symbols":[],"offset_hours":None,"errors":[]}
    if not verified:
        report["errors"].append("Cuenta/servidor/terminal/demo/USD/hedging no coinciden; diagnóstico detenido.")
        return report
    pairs=[]
    for part in cfg.pairs_list.split(","):
        a,b=part.split("-")
        pairs.append(tuple(s.strip() if cfg.symbol_suffix and s.strip().endswith(cfg.symbol_suffix)
                           else s.strip().upper()+cfg.symbol_suffix for s in (a,b)))
    symbols=sorted({s for pair in pairs for s in pair})
    clock=BrokerClock(cfg.broker_utc_offset,cfg.pairs_max_tick_age)
    try:
        clock.refresh(mt5,symbols,now=now)
        report["offset_hours"]=clock.offset_seconds/3600
    except Exception:
        report["errors"].append("Reloj/ticks no verificables. Ejecutar durante sesión abierta con cotizaciones en avance.")
    calendar=ForexCalendar.from_file(cfg.broker_sessions_file)
    specs={};ticks={};frames={}
    tf=getattr(mt5,"TIMEFRAME_"+cfg.pairs_timeframe)
    for sym in symbols:
        info=mt5.symbol_info(sym)
        public={"symbol":sym,"found":info is not None}
        report["symbols"].append(public)
        if info is None: continue
        specs[sym]=info
        public.update({"minimum":info.volume_min,"step":info.volume_step,"contract":info.trade_contract_size,"filling_mask":info.filling_mode})
        ticks[sym]=mt5.symbol_info_tick(sym)
        if clock.offset_seconds is None: continue
        try:
            tick=ticks[sym]
            if tick is None or not -5<=clock.age(tick,now)<=cfg.pairs_max_tick_age: raise RuntimeError()
            rates=mt5.copy_rates_from_pos(sym,tf,1,max(120,cfg.pairs_lookback*3))
            forming=mt5.copy_rates_from_pos(sym,tf,0,1)
            df=pd.DataFrame(rates);df["time"]=pd.to_datetime(df["time"],unit="s",utc=True)
            if df["time"].duplicated().any() or not df["time"].is_monotonic_increasing or not np.isfinite(df.close).all() or (df.close<=0).any(): raise RuntimeError()
            public["last_bar_age_hours"]=calendar.validate_bars(sym,cfg.pairs_timeframe,df.time.iloc[-1],pd.to_datetime(forming[0]["time"],unit="s",utc=True),clock.server_now(now))
            frames[sym]=df
        except Exception:
            public["history_ok"]=False
    for a,b in pairs:
        item={"pair":f"{a}/{b}","profile":cfg.pairs_risk_profile};report["pairs"].append(item)
        try:
            sa,sb=specs[a],specs[b];ta,tb=ticks[a],ticks[b]
            lot_a,lot_b=profile_lots(cfg.pairs_risk_profile,acc.balance,sa,sb,(ta.ask+ta.bid)/2,(tb.ask+tb.bid)/2)
            item["lots"]=[lot_a,lot_b]
            # No esconder riesgo: calcular ambas direcciones, no solo una.
            margins=[]
            for side in (1,-1):
                total=0.
                for sym,lot,tick,buy in ((a,lot_a,ta,side>0),(b,lot_b,tb,side<0)):
                    value=mt5.order_calc_margin(mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL,sym,lot,tick.ask if buy else tick.bid)
                    if value is None or not math.isfinite(value) or value<=0: raise RuntimeError()
                    total+=value
                margins.append(total)
            budget=acc.margin_free*.9
            item["margin_budget_pct"]=[100*x/budget if budget>0 else None for x in margins]
            item["margin_ok"]=budget>0 and max(margins)<=budget
            da,db=frames[a],frames[b]
            if not da.time.equals(db.time): raise RuntimeError()
            cfg_z=PairsConfig(lookback=cfg.pairs_lookback)
            z,_=zscore(da.close,db.close,cfg_z)
            corr=np.log(da.close).diff().rolling(cfg.pairs_lookback).corr(np.log(db.close).diff())
            item["z"]=float(z.iloc[-1]) if np.isfinite(z.iloc[-1]) else None
            item["corr"]=float(corr.iloc[-1]) if np.isfinite(corr.iloc[-1]) else None
        except Exception:
            item["ready"]=False
    report["ready"]=(not report["errors"] and len(frames)==len(symbols)
                     and all(p.get("margin_ok") and p.get("z") is not None and p.get("corr") is not None for p in report["pairs"]))
    return report

def render_report(r):
    lines=["PREFLIGHT — SOLO LECTURA, SIN ÓRDENES", "Cuenta/servidor/terminal DEMO, USD hedging: "+("OK" if r["account_checks"] else "NO VERIFICADO"),
           f"Desfase observado/verificado: {r['offset_hours']} h", "Calendario: Forex publicado; excepciones por símbolo/fecha si configuradas."]
    for s in r["symbols"]:
        lines.append(f"{s['symbol']}: encontrado={s['found']}; mínimo={s.get('minimum','—')}; paso={s.get('step','—')}; contrato={s.get('contract','—')}; filling mask={s.get('filling_mask','—')}; antigüedad vela={s.get('last_bar_age_hours','—')} h")
    for p in r["pairs"]:
        lines.append(f"{p['pair']}: perfil={p['profile']}; lotes A/B={p.get('lots','—')}; margen % del presupuesto90% (long/short)={p.get('margin_budget_pct','—')}; margen OK={p.get('margin_ok',False)}; z={p.get('z','—')}; corr={p.get('corr','—')}")
    lines+=r["errors"]
    lines.append("Listo para revisión: "+("SÍ" if r.get("ready") else "NO; corregir/verificar lo señalado"))
    return "\n".join(lines)

def main():
    import argparse,os
    parser=argparse.ArgumentParser(description="Diagnóstico demo sin órdenes y sin leer .env. Configuración por entorno del proceso.")
    parser.parse_args()
    os.environ["CUANTI_NO_ENV"]="1"
    # No contraseña, servidor ni login a initialize: se adjunta al terminal ya abierto.
    from config import CONFIG
    if not CONFIG.mt5_path or not CONFIG.mt5_login or not CONFIG.mt5_server:
        print("Faltan MT5_PATH, MT5_LOGIN o MT5_SERVER en el entorno privado. No se lee .env.")
        return 2
    import MetaTrader5 as mt5
    if not mt5.initialize(path=CONFIG.mt5_path):
        print("No se pudo adjuntar al terminal esperado."); return 2
    try:
        report=collect_report(mt5,CONFIG)
        print(render_report(report))
        return 0 if report.get("ready") else 2
    except Exception:
        print("Diagnóstico no completado; revisar terminal/configuración. No se imprimen datos privados.")
        return 2
    finally: mt5.shutdown()

if __name__=="__main__": raise SystemExit(main())
