from datetime import datetime, timezone, timedelta
from types import SimpleNamespace as NS
from dataclasses import replace
import pytest
import pandas as pd
import executor as ex
import multi_pair_trader as m
from config import CONFIG

BASE=datetime(2026,10,8,12,tzinfo=timezone.utc).timestamp()

@pytest.mark.parametrize("hours",[2,3])
def test_server_offset_quote_is_fresh(fake,monkeypatch,hours):
    import pairs_safety as safety
    monkeypatch.setattr(safety,"CONFIG",replace(CONFIG,broker_utc_offset=str(hours)))
    monkeypatch.setattr(safety.time,"time",lambda:BASE)
    fake.tick.time=int(BASE+hours*3600-2)
    assert safety.fresh_tick(fake.mt5,"EURUSD.m")==fake.tick


def test_auto_clock_does_not_relabel_frozen_hour_as_fresh(fake):
    from broker_time import BrokerClock
    fake.tick.time=int(BASE+3*3600-3600)
    clock=BrokerClock("auto",max_age=120,wait_seconds=0)
    with pytest.raises(RuntimeError): clock.refresh(fake.mt5,["EURUSD.m"],now=BASE)


def test_clock_recalibrates_dst_and_rejects_disconnection(fake):
    from broker_time import BrokerClock
    clock=BrokerClock("auto",max_age=120,wait_seconds=.02,poll_seconds=0)
    seq=iter([NS(time=BASE+3*3600-1),NS(time=BASE+3*3600)])
    fake.mt5.symbol_info_tick=lambda s:next(seq)
    assert clock.refresh(fake.mt5,["EURUSD.m"],now=BASE)==10800
    seq=iter([NS(time=BASE+2*3600-1),NS(time=BASE+2*3600)])
    assert clock.refresh(fake.mt5,["EURUSD.m"],now=BASE)==7200
    fake.terminal.connected=False
    with pytest.raises(RuntimeError): clock.refresh(fake.mt5,["EURUSD.m"],now=BASE)

@pytest.mark.parametrize("server_date",["2026-10-10T12:00","2026-10-11T12:00","2026-10-12T00:01"])
def test_closed_weekend_is_silent(fake,tmp_path,monkeypatch,server_date):
    from broker_time import ForexCalendar
    monkeypatch.setattr(m,"DATA_DIR",tmp_path)
    t=m.MultiPairTrader(); t.clock.offset_seconds=10800
    t.server_now=lambda:datetime.fromisoformat(server_date)
    t.calendar=ForexCalendar()
    fake.tick.time=1
    assert t.check()==[] and not fake.calls


def test_monday_friday_closed_signal_not_discarded_by_prime(fake,tmp_path,monkeypatch):
    monkeypatch.setattr(m,"DATA_DIR",tmp_path)
    t=m.MultiPairTrader(); t.slots=t.slots[:1]
    friday=pd.Timestamp("2026-10-09",tz="UTC")
    frame=pd.DataFrame({"time":[friday],"a":[1.],"b":[1.]})
    monkeypatch.setattr(t,"_pair_frame",lambda s:frame)
    monkeypatch.setattr(t,"_zcorr",lambda *a:(-2.5,.99))
    t.prime()
    t._check_slot(t.slots[0],{"balance":3000.,"is_demo":True})
    assert len(fake.calls)==2
    t._check_slot(t.slots[0],{"balance":3000.,"is_demo":True})
    assert len(fake.calls)==2


def test_calendar_monday_and_holiday():
    from broker_time import ForexCalendar
    c=ForexCalendar({"EURUSD.m":{"2026-12-25":[]}})
    assert c.is_open("EURUSD.m",datetime(2026,10,12,0,2))
    assert not c.is_open("EURUSD.m",datetime(2026,12,25,12))
    assert c.previous_trading_date("EURUSD.m",datetime(2026,10,12).date()).isoformat()=="2026-10-09"
    assert c.previous_trading_date("EURUSD.m",datetime(2026,12,28).date()).isoformat()=="2026-12-24"


def test_daily_accepts_friday_on_monday_but_rejects_stale_forming(fake,tmp_path,monkeypatch):
    monkeypatch.setattr(m,"DATA_DIR",tmp_path)
    t=m.MultiPairTrader()
    t.clock.offset_seconds=10800
    t.server_now=lambda:datetime(2026,10,12,9)
    fake.tick.time=int(datetime(2026,10,12,9,tzinfo=timezone.utc).timestamp())
    import pairs_safety
    monkeypatch.setattr(pairs_safety.time,"time",lambda:fake.tick.time-10800)
    monkeypatch.setattr(pairs_safety,"CONFIG",replace(CONFIG,broker_utc_offset="3"))
    friday=pd.Timestamp("2026-10-09",tz="UTC").timestamp()
    monday=pd.Timestamp("2026-10-12",tz="UTC").timestamp()
    fake.mt5.copy_rates_from_pos=lambda s,tf,pos,n:[dict(time=monday if pos==0 else friday,close=1.2)]
    assert t._daily("EURUSD.m").time.iloc[-1].timestamp()==friday
    fake.mt5.copy_rates_from_pos=lambda *a:[dict(time=friday,close=1.2)]
    with pytest.raises(RuntimeError): t._daily("EURUSD.m")


def test_symbol_is_selected_before_tick(fake,tmp_path,monkeypatch):
    monkeypatch.setattr(m,"DATA_DIR",tmp_path)
    t=m.MultiPairTrader(); selected=set()
    fake.mt5.symbol_select=lambda s,b:selected.add(s) or True
    def tick(s):
        assert s in selected
        return fake.tick
    fake.mt5.symbol_info_tick=tick
    now=int(pd.Timestamp.now(tz="UTC").timestamp())
    fake.mt5.copy_rates_from_pos=lambda s,tf,pos,n:[dict(time=now if pos==0 else now-86400,close=1.)]
    t._daily("EURUSD.m")


def test_error_notices_on_change_recovery_and_hourly():
    from broker_time import NoticeGate
    gate=NoticeGate()
    assert gate.update("EURGBP","stale",0)
    assert not gate.update("EURGBP","stale",60)
    assert gate.update("EURGBP","stale",3600)
    assert gate.update("EURGBP",None,3601)
    assert not gate.update("EURGBP",None,3602)


def test_expired_license_blocks_entries_but_manages_exit(fake,tmp_path,monkeypatch):
    monkeypatch.setattr(m,"DATA_DIR",tmp_path)
    t=m.MultiPairTrader();t.slots=t.slots[:1]
    t.license_reader=lambda:NS(is_expired=True,days_left=0,expires_at=datetime(2026,10,1,tzinfo=timezone.utc))
    monkeypatch.setattr(t,"_pair_frame",lambda s:pd.DataFrame({"time":[pd.Timestamp.now(tz="UTC")]}))
    monkeypatch.setattr(t,"_zcorr",lambda *a:(-2.5,.99))
    events=t.check()
    assert not fake.calls and any(e["type"]=="license" for e in events)
    s=t.slots[0]
    ex.open_pair(1,s.a,s.b,.03,s.magic,lot_b=.03)
    s.last_bar_time=None
    monkeypatch.setattr(t,"_zcorr",lambda *a:(0.,.99))
    t.check()
    assert not fake.positions


def test_license_warning_once_at_15_days(fake,tmp_path,monkeypatch):
    monkeypatch.setattr(m,"DATA_DIR",tmp_path)
    t=m.MultiPairTrader()
    t.license_reader=lambda:NS(is_expired=False,days_left=15,expires_at=datetime(2026,10,23,tzinfo=timezone.utc))
    monkeypatch.setattr(t,"_check_slot",lambda *a:[])
    assert sum(e["type"]=="license" for e in t.check())==1
    assert not t.check()


def test_preflight_report_cannot_send_orders_or_leak_private_data(fake):
    from preflight import collect_report,render_report
    fake.mt5.order_send=lambda *a:(_ for _ in ()).throw(AssertionError("Orders forbidden"))
    fake.mt5.copy_rates_from_pos=lambda s,tf,pos,n:[dict(time=BASE-i*86400,close=1.+i*.00001) for i in range(100,0,-1)] if pos else [dict(time=BASE,close=1.)]
    report=collect_report(fake.mt5,CONFIG,now=BASE)
    text=render_report(report)
    assert "Simulated-Demo" not in text and "123" not in text
    assert "C:/Simulated" not in text and "simulation-only" not in text
    assert "EURUSD.m" in text and "margen" in text.lower()
    assert report["readonly"] and not fake.calls


def test_client_template_is_annual():
    from pathlib import Path
    text=(Path(__file__).resolve().parents[1]/".env.cliente.example").read_text()
    assert "DEMO_DURATION_MONTHS=12" in text


def test_preflight_six_pairs_ready_with_verified_server_time(fake):
    import numpy as np
    from preflight import collect_report, ReadOnlyMT5
    cfg=replace(CONFIG,broker_utc_offset="3",pairs_lookback=60,
        pairs_list="EURUSD-GBPUSD,EURUSD-AUDUSD,EURUSD-NZDUSD,GBPUSD-AUDUSD,GBPUSD-NZDUSD,USDCHF-USDCAD")
    fake.tick.time=int(BASE+10800)
    today=pd.Timestamp("2026-10-08",tz="UTC")
    dates=pd.bdate_range(end=today-pd.Timedelta(days=1),periods=180)
    def rates(sym,tf,pos,n):
        if pos==0: return [dict(time=today.timestamp(),close=1.2)]
        phase=sum(i*ord(c) for i,c in enumerate(sym,1))*.001
        return [dict(time=d.timestamp(),close=1.2*np.exp(.0005*i+.002*np.sin(i*.37+phase))) for i,d in enumerate(dates)]
    fake.mt5.copy_rates_from_pos=rates
    report=collect_report(fake.mt5,cfg,now=BASE)
    assert report["ready"] and report["offset_hours"]==3, __import__("json").dumps(report)
    assert len(report["pairs"])==6 and len(report["symbols"])==6
    assert all(p["margin_ok"] and p["z"] is not None and p["corr"] is not None for p in report["pairs"])
    with pytest.raises(RuntimeError): ReadOnlyMT5(fake.mt5).order_send({})
    assert not fake.calls


def test_preflight_rejects_wrong_account_and_stale_clock(fake):
    from preflight import collect_report
    fake.acc.trade_mode=2
    assert not collect_report(fake.mt5,CONFIG,now=BASE)["account_checks"]
    fake.acc.trade_mode=0
    fake.tick.time=int(BASE-3600)
    report=collect_report(fake.mt5,CONFIG,now=BASE)
    assert not report["ready"] and report["offset_hours"] is None
