from types import SimpleNamespace as NS
import pandas as pd
import numpy as np
import pytest
import multi_pair_trader as m
from pairs_strategy import PairsConfig,Action,decide,zscore

@pytest.mark.parametrize("z,pos,want",[(-1.6,0,Action.OPEN_LONG),(1.6,0,Action.OPEN_SHORT),(1.5,0,Action.OPEN_SHORT),(-.3,1,Action.CLOSE),(.3,-1,Action.CLOSE),(-4.6,1,Action.CLOSE),(4.6,-1,Action.CLOSE),(float("nan"),0,Action.HOLD)])
def test_signal_thresholds(z,pos,want):
    assert decide(z,pos,PairsConfig(entry_z=1.5,exit_z=.3,stop_z=4.5))==want

def test_z_correlation_matches_lab_and_no_future():
    rng=np.random.default_rng(17); b=np.exp(np.cumsum(rng.normal(0,.01,200)))
    a=b*np.exp(rng.normal(0,.02,200)); j=pd.DataFrame({"a":a,"b":b})
    cfg=PairsConfig(lookback=60)
    spread=np.log(j.a)-np.log(j.b)
    want=(spread-spread.rolling(60).mean())/spread.rolling(60).std(ddof=0)
    z,_=zscore(j.a,j.b,cfg)
    np.testing.assert_allclose(z,want,equal_nan=True)
    zz,c=m.MultiPairTrader._zcorr(j,cfg)
    assert zz==pytest.approx(want.iloc[-1])
    assert c==pytest.approx(np.log(j.a).diff().rolling(60).corr(np.log(j.b).diff()).iloc[-1])
    before=z.iloc[99]; j.loc[100:,"a"]*=10
    assert zscore(j.a,j.b,cfg)[0].iloc[99]==before

@pytest.fixture
def trader(fake,tmp_path,monkeypatch):
    monkeypatch.setattr(m,"DATA_DIR",tmp_path)
    t=m.MultiPairTrader()
    t.slots=t.slots[:1]
    return t

def test_forming_bar_excluded_and_bad_feed_rejected(fake,trader):
    now=int(pd.Timestamp.now(tz="UTC").timestamp())
    def rates(sym,tf,start,count):
        if start==0: return [dict(time=now,close=1.)]
        assert start==1
        return [dict(time=now-i*86400,close=1.) for i in range(124,0,-1)]
    fake.mt5.copy_rates_from_pos=rates
    frame=trader._daily("EURUSD.m")
    assert len(frame)==124 and frame.time.iloc[-1].timestamp()==now-86400
    fake.terminal.connected=False
    with pytest.raises(RuntimeError): trader._daily("EURUSD.m")

def test_restart_sees_both_positions_without_duplication(fake,trader):
    import executor as ex
    ex.open_pair(1,trader.slots[0].a,trader.slots[0].b,.03,trader.slots[0].magic,lot_b=.03)
    assert trader._pos(trader.slots[0])==1
    assert len(fake.calls)==2

def test_orphan_close_retried_without_new_bar(fake,trader,monkeypatch):
    import executor as ex
    s=trader.slots[0]
    ex.open_pair(1,s.a,s.b,.03,s.magic,lot_b=.03)
    fake.positions.pop(); fake.codes[:]=[10006]
    monkeypatch.setattr(trader,"_pair_frame",lambda s: (_ for _ in ()).throw(AssertionError("Debe cerrar antes de datos")))
    events=trader._check_slot(s,{"balance":3000.,"is_demo":True})
    assert fake.positions and any(e["type"]=="error" for e in events)
    fake.codes[:]=[10009]
    trader._check_slot(s,{"balance":3000.,"is_demo":True})
    assert not fake.positions


def test_nan_correlation_does_not_open(fake,trader,monkeypatch):
    s=trader.slots[0]
    monkeypatch.setattr(trader,"_pair_frame",lambda s:pd.DataFrame({"time":[pd.Timestamp.now(tz="UTC")],"a":[1.],"b":[1.]}))
    monkeypatch.setattr(trader,"_zcorr",lambda *a:(-2.,float("nan")))
    trader._check_slot(s,{"balance":3000.,"is_demo":True})
    assert not fake.calls

def test_close_intent_survives_error(fake,trader,monkeypatch):
    import executor as ex
    s=trader.slots[0]
    ex.open_pair(1,s.a,s.b,.03,s.magic,lot_b=.03)
    monkeypatch.setattr(trader,"_pair_frame",lambda s:pd.DataFrame({"time":[pd.Timestamp.now(tz="UTC")]}))
    monkeypatch.setattr(trader,"_zcorr",lambda *a:(0.,1.))
    fake.codes[:]=[10006,10006]
    trader._check_slot(s,{"balance":3000.,"is_demo":True})
    assert trader.runtime.is_pending(s.magic)
    trader._check_slot(s,{"balance":3000.,"is_demo":True})
    assert not fake.positions and not trader.runtime.is_pending(s.magic)

@pytest.mark.parametrize("z,pos,want",[(-1.5,0,Action.OPEN_LONG),(-4.5,1,Action.CLOSE),(4.5,-1,Action.CLOSE)])
def test_exact_lab_boundaries(z,pos,want):
    assert decide(z,pos,PairsConfig(entry_z=1.5,exit_z=.3,stop_z=4.5))==want

def test_kill_survives_trader_restart_and_resume_blocked(fake,trader,monkeypatch):
    monkeypatch.setattr(trader,"_check_slot",lambda *a:[])
    trader.check(); fake.acc.equity=2800.; trader.check()
    assert trader.state.killed_today and not trader.state.running
    restored=m.MultiPairTrader()
    monkeypatch.setattr(restored,"_check_slot",lambda *a:[])
    fake.acc.equity=3000.; restored.check()
    assert restored.state.killed_today and not restored.state.running
    with pytest.raises(RuntimeError): restored.resume()

def test_closed_profit_includes_costs(fake,trader):
    fake.mt5.history_deals_get=lambda **k:[NS(profit=10.,swap=-2.,commission=-1.,fee=-.5)]
    assert trader._closed_profit(101)==6.5

def test_closed_pair_recovered_after_restart_does_not_reopen(fake,trader,monkeypatch):
    import executor as ex
    s=trader.slots[0]
    ex.open_pair(-1,s.a,s.b,.03,s.magic,lot_b=.03)
    restored=m.MultiPairTrader(); restored.slots=restored.slots[:1]
    monkeypatch.setattr(restored,"_pair_frame",lambda s:pd.DataFrame({"time":[pd.Timestamp.now(tz="UTC")]}))
    monkeypatch.setattr(restored,"_zcorr",lambda *a:(2.,1.))
    restored._check_slot(restored.slots[0],{"balance":3000.,"is_demo":True})
    assert len(fake.calls)==2

def test_missing_last_bar_of_one_leg_blocks(trader,monkeypatch):
    def frame(sym):
        return pd.DataFrame({"time":pd.date_range("2026-10-01",periods=3 if sym==trader.slots[0].a else 2,tz="UTC"),"close":1.})
    monkeypatch.setattr(trader,"_daily",frame)
    with pytest.raises(RuntimeError): trader._pair_frame(trader.slots[0])


def test_full_open_journal_contains_actual_lots_and_sides(fake,trader,monkeypatch):
    s=trader.slots[0]
    monkeypatch.setattr(trader,"_pair_frame",lambda s:pd.DataFrame({"time":[pd.Timestamp.now(tz="UTC")]}))
    monkeypatch.setattr(trader,"_zcorr",lambda *a:(-2.5,.99))
    events=trader.check()
    assert any(e["type"]=="opened" for e in events)
    assert len(fake.positions)==2 and not trader.runtime.is_pending(s.magic)
    with trader.journal._conn() as c:
        rows=c.execute("SELECT symbol,direction,lot,timeframe FROM trades ORDER BY ticket").fetchall()
    assert rows==[(s.a,1,.03,"D1"),(s.b,-1,.03,"D1")]


def test_dead_history_with_fresh_tick_blocks(fake,trader):
    fake.mt5.copy_rates_from_pos=lambda *a:[dict(time=100*86400+i*86400,close=1.) for i in range(125)]
    with pytest.raises(RuntimeError): trader._daily("EURUSD.m")
    assert not fake.calls
