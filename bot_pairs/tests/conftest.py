import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
import pytest
import socket
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Import-time isolation: no dotenv, no installed MT5, no Telegram calls.
dotenv = ModuleType("dotenv")
dotenv.load_dotenv = lambda *a, **k: None
sys.modules["dotenv"] = dotenv
mt5 = ModuleType("MetaTrader5")
mt5.initialize = lambda *a, **k: (_ for _ in ()).throw(AssertionError("No conectar terminales"))
sys.modules["MetaTrader5"] = mt5
import os
os.environ["ENV_FILE"] = "__TEST_NO_ENV__"
os.environ["INSTANCE_ID"] = "test"
os.environ["PAIRS_DATA_DIR"] = str(Path(__file__).parent / "__runtime_unused")
os.environ["MT5_LOGIN"] = "123"
os.environ["MT5_SERVER"] = "Simulated-Demo"
os.environ["MT5_PATH"] = "C:/Simulated/terminal64.exe"
os.environ["PAIRS_MAGIC_BASE"] = "91000"
os.environ["SYMBOL_SUFFIX"] = ".m"
os.environ["PAIRS_RISK_PROFILE"] = "conservador"
@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Red prohibida")))
@pytest.fixture
def fake(monkeypatch):
    import time
    import executor as ex
    import mt5_client as mc
    calls=[]; positions=[]; codes=[]
    acc=NS(login=123, server="Simulated-Demo", trade_mode=0, currency="USD", margin_mode=2,
           balance=3000., equity=3000., margin_free=3000., trade_allowed=True, leverage=100)
    terminal=NS(path="C:/Simulated",connected=True, trade_allowed=True, tradeapi_disabled=False)
    spec=NS(volume_min=.01, volume_max=100., volume_step=.01, trade_contract_size=100000.,
            currency_base="EUR", currency_profit="USD", filling_mode=1,
            trade_tick_size=.00001, trade_tick_value=1., digits=5, point=.00001, trade_stops_level=0)
    tick=NS(time=int(time.time()), ask=1.2, bid=1.1999)
    def send(req):
        calls.append(req.copy())
        code=codes.pop(0) if codes else 10009
        if code in (10009,10010):
            if "position" in req:
                found=next((x for x in positions if x.ticket==req["position"]),None)
                if found:
                    if code==10009: positions.remove(found)
                    else: found.volume/=2
            else:
                vol=req["volume"] if code==10009 else req["volume"]/2
                tk=100+len(calls)
                positions.append(NS(ticket=tk,identifier=tk,symbol=req["symbol"],type=req["type"],
                    volume=vol,magic=req["magic"],price_open=req["price"],sl=0.,tp=0.,profit=0.))
        return NS(retcode=code,order=99999,price=req["price"],volume=req["volume"],comment="simulado")
    def get(**kw):
        return tuple(x for x in positions if all(getattr(x,k)==v for k,v in kw.items()))
    values=dict(account_info=lambda:acc, terminal_info=lambda:terminal,
                symbol_info=lambda s:spec, symbol_info_tick=lambda s:tick,
                positions_get=get, order_send=send, order_calc_margin=lambda *a:100.,
                symbol_select=lambda *a:True, last_error=lambda:(0,"simulado"),
                ORDER_TYPE_BUY=0,ORDER_TYPE_SELL=1,POSITION_TYPE_BUY=0,TRADE_ACTION_DEAL=1,
                ORDER_TIME_GTC=0,ORDER_FILLING_FOK=0,ORDER_FILLING_IOC=1,ORDER_FILLING_RETURN=2,
                TRADE_RETCODE_DONE=10009,TRADE_RETCODE_DONE_PARTIAL=10010,TIMEFRAME_D1=16408,
                TIMEFRAME_H4=16388,ACCOUNT_MARGIN_MODE_RETAIL_HEDGING=2,
                ACCOUNT_TRADE_MODE_DEMO=0)
    for k,v in values.items(): monkeypatch.setattr(mt5,k,v,raising=False)
    return NS(acc=acc,terminal=terminal,spec=spec,tick=tick,calls=calls,positions=positions,codes=codes,mt5=mt5)
