import logging,json
from datetime import datetime
from pathlib import Path
import pytest
from broker_time import NoticeGate,ForexCalendar
import multi_pair_trader as m


def test_short_block_and_recovery_are_log_only(caplog):
    caplog.set_level(logging.INFO)
    gate=NoticeGate(grace_seconds=600)
    assert not gate.update("cycle","stale",0)
    assert not gate.update("cycle","stale",599)
    assert not gate.update("cycle",None,599)
    assert "cycle" in caplog.text and "recuper" in caplog.text.lower()


def test_long_block_alerts_then_hourly_and_recovers():
    g=NoticeGate(grace_seconds=600)
    assert not g.update("cycle","stale",0)
    assert g.update("cycle","stale",600)
    assert not g.update("cycle","stale",601)
    assert g.update("cycle","stale",4200)
    assert g.update("cycle",None,4201)
    assert not g.update("cycle",None,4202)


def test_changing_error_does_not_restart_grace():
    g=NoticeGate(grace_seconds=600)
    assert not g.update("cycle","stale",0)
    assert not g.update("cycle","offline",599)
    assert g.update("cycle","offline",600)
    assert g.update("cycle","stale",601)


def test_trader_transient_cycle_has_no_client_events(fake,tmp_path,monkeypatch):
    monkeypatch.setattr(m,"DATA_DIR",tmp_path)
    trader=m.MultiPairTrader()
    monkeypatch.setattr(trader,"_check_locked",lambda:(_ for _ in ()).throw(RuntimeError("simulated")))
    assert trader.check()==[]
    monkeypatch.setattr(trader,"_check_locked",lambda:[])
    assert trader.check()==[]
    assert not fake.calls


def test_holiday_example_is_inactive_until_verified(tmp_path):
    path=Path(__file__).parents[1]/"sesiones_justmarkets.example.json"
    data=json.loads(path.read_text(encoding="utf-8"))
    assert len(data["pendientes"])==2 and all(x["estado"]=="por verificar en el terminal" for x in data["pendientes"])
    calendar=ForexCalendar.from_file(path)
    assert calendar.is_open("EURUSD.m",datetime(2026,12,25,12))
    data["sesiones_confirmadas"]={"*":{"2026-12-25":[]}}
    verified=tmp_path/"sessions.json";verified.write_text(json.dumps(data))
    assert not ForexCalendar.from_file(verified).is_open("EURUSD.m",datetime(2026,12,25,12))


def test_short_block_is_written_to_local_log(tmp_path):
    from broker_time import configure_notice_logging
    log=logging.getLogger("broker_time");old=list(log.handlers);level=log.level;propagate=log.propagate
    try:
        configure_notice_logging(tmp_path)
        g=NoticeGate(grace_seconds=600)
        assert not g.update("cycle","stale",0)
        assert not g.update("cycle",None,120)
        content=(tmp_path/"avisos.log").read_text(encoding="utf-8")
        assert "stale" in content and "recuperado" in content
    finally:
        for h in list(log.handlers):
            if h not in old: h.close();log.removeHandler(h)
        log.handlers=old;log.setLevel(level);log.propagate=propagate
