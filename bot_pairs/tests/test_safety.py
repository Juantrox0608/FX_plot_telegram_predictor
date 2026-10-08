from types import SimpleNamespace as NS
from dataclasses import replace
import math
import pytest
import executor as ex
from config import CONFIG

@pytest.mark.parametrize("change", ["real","login","server","currency","netting","disconnect","disabled","stale"])
def test_order_boundary_blocks_unsafe_account(fake,change):
    if change=="real": fake.acc.trade_mode=2
    elif change=="login": fake.acc.login=999
    elif change=="server": fake.acc.server="Other"
    elif change=="currency": fake.acc.currency="EUR"
    elif change=="netting": fake.acc.margin_mode=0
    elif change=="disconnect": fake.terminal.connected=False
    elif change=="disabled": fake.terminal.tradeapi_disabled=True
    elif change=="stale": fake.tick.time=1
    with pytest.raises(RuntimeError): ex.open_pair(1,"EURUSD.m","GBPUSD.m",.03,91000,lot_b=.04)
    assert fake.calls==[]

def test_two_different_lots_and_position_tickets(fake):
    r=ex.open_pair(1,"EURUSD.m","GBPUSD.m",.03,91000,lot_b=.04)
    assert r["ok"] and [x["volume"] for x in fake.calls]==[.03,.04]
    assert r["leg_a"].ticket==fake.positions[0].ticket!=99999
    assert r["leg_b"].ticket==fake.positions[1].ticket

@pytest.mark.parametrize("codes,remaining", [([10009,10006,10009],False),([10009,10006,10006],True),([10010,10009],False),([10009,10010,10009,10009],False)])
def test_partial_or_second_leg_failure_cleanup_is_truthful(fake,codes,remaining):
    fake.codes[:]=codes
    r=ex.open_pair(1,"EURUSD.m","GBPUSD.m",.03,91000,lot_b=.04)
    assert not r["ok"]
    assert r["needs_close"]==remaining
    assert bool(fake.positions)==remaining
    if remaining: assert "pendiente" in r["message"].lower()

def test_none_positions_is_error_not_flat(fake):
    fake.mt5.positions_get=lambda **k:None
    with pytest.raises(RuntimeError): ex.pair_positions(91000)

def test_margin_rejected_before_first_leg(fake):
    fake.acc.margin_free=150.
    with pytest.raises(RuntimeError): ex.open_pair(1,"EURUSD.m","GBPUSD.m",.03,91000,lot_b=.04)
    assert not fake.calls

def test_real_close_also_requires_optin(fake):
    ex.open_pair(1,"EURUSD.m","GBPUSD.m",.03,91000,lot_b=.04)
    fake.calls.clear(); fake.acc.trade_mode=2
    with pytest.raises(RuntimeError): ex.close_pair(91000)
    assert not fake.calls

@pytest.mark.parametrize("profile,expected", [("conservador",(.03,.04)),("moderado",(.06,.08)),("agresivo",(.09,.12))])
def test_profile_balancing_floor(profile,expected):
    from pairs_safety import profile_lots
    a=NS(volume_step=.01,volume_min=.01,volume_max=100.,trade_contract_size=100000.,currency_base="EUR",currency_profit="USD")
    b=NS(**vars(a))
    assert profile_lots(profile,3000.,a,b,1.4,1.)==expected

def test_direct_usd_bases_and_below_minimum():
    from pairs_safety import profile_lots
    a=NS(volume_step=.01,volume_min=.01,volume_max=100.,trade_contract_size=100000.,currency_base="USD",currency_profit="CHF")
    assert profile_lots("conservador",3000.,a,a,.9,1.35)==(.03,.03)
    with pytest.raises(ValueError): profile_lots("conservador",999.,a,a,.9,1.35)
    a.volume_min=.05
    with pytest.raises(ValueError): profile_lots("conservador",3000.,a,a,.9,1.35)

def test_persisted_kill_and_scope(tmp_path):
    from pairs_safety import RuntimeStore
    p=tmp_path/"state.db"
    one=RuntimeStore(p,"account123")
    state=one.daily("2026-10-08",1000.,5.)
    assert not state["killed"]
    state=one.daily("2026-10-08",949.,5.)
    assert state["killed"]
    two=RuntimeStore(p,"account123")
    assert two.daily("2026-10-08",999.,5.)["killed"]
    with pytest.raises(RuntimeError): RuntimeStore(p,"different-account")
    assert not two.daily("2026-10-09",999.,5.)["killed"]

def test_pending_close_survives_restart(tmp_path):
    from pairs_safety import RuntimeStore
    p=tmp_path/"state.db"
    one=RuntimeStore(p,"a"); one.pending(91000,True)
    assert RuntimeStore(p,"a").is_pending(91000)


def test_suffix_and_unique_magics():
    from multi_pair_trader import _parse_pairs
    assert _parse_pairs("EURUSD-GBPUSD")==[("EURUSD.m","GBPUSD.m",91000)]
    assert _parse_pairs("EURUSD.m-GBPUSD.m")==[("EURUSD.m","GBPUSD.m",91000)]
    with pytest.raises(ValueError): _parse_pairs("EURUSD-GBPUSD,GBPUSD-EURUSD")


def test_zero_unknown_margin_and_nonfinite_volume(fake):
    fake.mt5.order_calc_margin=lambda *a:None
    with pytest.raises(RuntimeError): ex.open_pair(1,"EURUSD.m","GBPUSD.m",.03,91000,lot_b=.04)
    assert not fake.calls


def test_optin_real_still_requires_identity(fake,monkeypatch):
    import config
    monkeypatch.setattr(config,"CONFIG",replace(CONFIG,pairs_allow_real=True))
    import pairs_safety
    monkeypatch.setattr(pairs_safety,"CONFIG",config.CONFIG)
    fake.acc.trade_mode=2
    assert ex.open_pair(1,"EURUSD.m","GBPUSD.m",.03,91000,lot_b=.04)["ok"]
    fake.acc.login=999
    with pytest.raises(RuntimeError): ex.close_pair(91000)


def test_instances_with_same_terminal_or_data_block(tmp_path):
    from pairs_safety import InstanceLock
    a=tmp_path/"data-a"; b=tmp_path/"data-b"; t=tmp_path/"terminal-a.exe"; u=tmp_path/"terminal-b.exe"
    with InstanceLock(a,t):
        with pytest.raises(RuntimeError): InstanceLock(a,u)
        with pytest.raises(RuntimeError): InstanceLock(b,t)
        with InstanceLock(b,u): pass
    with InstanceLock(a,t): pass


def test_crash_intent_for_two_legs_is_reconciled(fake,tmp_path,monkeypatch):
    import multi_pair_trader as m
    monkeypatch.setattr(m,"DATA_DIR",tmp_path)
    first=m.MultiPairTrader(); s=first.slots[0]
    first.runtime.pending(s.magic,True)
    ex.open_pair(1,s.a,s.b,.03,s.magic,lot_b=.03)
    restored=m.MultiPairTrader()
    restored._check_slot(restored.slots[0],{"balance":3000.,"is_demo":True})
    assert not fake.positions


def test_partial_close_retries_until_flat(fake):
    ex.open_pair(1,"EURUSD.m","GBPUSD.m",.03,91000,lot_b=.04)
    fake.codes[:]=[10010,10009]
    result=ex.close_pair(91000)
    assert not all(r.ok for r in result) and len(fake.positions)==1
    ex.close_pair(91000)
    assert not fake.positions


@pytest.mark.parametrize("changes",[{"pairs_lookback":1},{"pairs_entry_z":float("nan")},{"pairs_stop_z":1.},{"pairs_min_corr":float("nan")},{"daily_max_loss_percent":float("nan")}])
def test_invalid_strategy_config_is_rejected(changes):
    cfg=replace(CONFIG,**changes)
    assert any("estrategia" in p.lower() or "diario" in p.lower() for p in cfg.validate())


def test_missing_terminal_path_is_rejected(fake):
    fake.terminal.path="C:/Other"
    with pytest.raises(RuntimeError): ex.open_pair(1,"EURUSD.m","GBPUSD.m",.03,91000,lot_b=.04)
    assert not fake.calls


def test_floor_is_not_rounded_to_nearest():
    from pairs_safety import profile_lots
    a=NS(volume_step=.01,volume_min=.01,volume_max=100.,trade_contract_size=100000.,currency_base="EUR",currency_profit="USD")
    assert profile_lots("conservador",3000.,a,a,1.59,1.)==(.03,.04)


@pytest.mark.parametrize("equity",[0.,-100.])
def test_insolvent_equity_triggers_durable_kill(tmp_path,equity):
    from pairs_safety import RuntimeStore
    st=RuntimeStore(tmp_path/"state.db","a")
    st.daily("2026-10-08",1000.,5.)
    assert st.daily("2026-10-08",equity,5.)["killed"]
    assert st.daily("2026-10-08",1000.,5.)["killed"]
