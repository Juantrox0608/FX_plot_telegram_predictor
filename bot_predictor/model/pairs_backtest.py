"""
Backtest REALISTA del bot de pairs (EURUSD vs GBPUSD) sobre una cuenta chica.

Modela lo real: dos patas (long A / short B), lote mínimo 0.01 por pata, P&L en
dólares desde los precios de cada leg, coste de spread por pata, compounding del
lote a medida que crece el balance, y quiebre si no alcanza el margen.

Usa la estrategia BASE validada (pairs_strategy, 1:1, z-score ±2). Sweep de
agresividad (cuánto capital por cada 0.01 lote).

Uso: python -m model.pairs_backtest
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import DATA_DIR
from model.strategies_compare import to_daily
from pairs_strategy import Action, PairsConfig, decide, zscore

PIP = 0.0001
PIP_VALUE_PER_001LOT = 0.10   # USD por pip por 0.01 lote (EURUSD/GBPUSD, cuenta USD)
CONTRACT = 100_000
SPREAD_PIPS = 1.0             # coste por pata (ida) — realista
MAX_HOLD = 30                 # días máx en un trade
LEVERAGE = 500


def simulate(eur, gbp, cfg: PairsConfig, start=100.0, capital_per_unit=100.0) -> dict:
    j = pd.DataFrame({"a": eur["close"], "b": gbp["close"]}).dropna()
    z, _ = zscore(j["a"], j["b"], cfg)
    corr = (np.log(j["a"]).diff().rolling(cfg.lookback)
            .corr(np.log(j["b"]).diff())).values
    a = j["a"].values; b = j["b"].values; zz = z.values
    n = len(a)

    balance = start; peak = start; max_dd = 0.0
    trades = wins = 0; blown = False
    pos = 0; a_en = b_en = None; units = 0; held = 0

    for i in range(n):
        if not np.isfinite(zz[i]):
            continue
        if pos == 0:
            act = decide(zz[i], 0, cfg)
            if cfg.min_corr > 0 and not (np.isfinite(corr[i]) and corr[i] >= cfg.min_corr):
                act = Action.HOLD  # relación no correlacionada -> no operar
            if act in (Action.OPEN_LONG, Action.OPEN_SHORT):
                # tamaño: 1 unidad (0.01 lote/pata) por cada 'capital_per_unit' de balance
                units = max(1, int(balance // capital_per_unit))
                margin = units * 0.01 * CONTRACT * a[i] / LEVERAGE * 2  # dos patas
                if balance <= margin:
                    blown = True; break
                pos = 1 if act == Action.OPEN_LONG else -1
                a_en, b_en, held = a[i], b[i], 0
        else:
            held += 1
            act = decide(zz[i], pos, cfg)
            if act == Action.CLOSE or held >= MAX_HOLD:
                # P&L en dólares de las dos patas (long A/short B si pos=+1)
                pips_a = (a[i] - a_en) / PIP * pos
                pips_b = (b[i] - b_en) / PIP * (-pos)
                gross = (pips_a + pips_b) * units * PIP_VALUE_PER_001LOT
                cost = 2 * SPREAD_PIPS * units * PIP_VALUE_PER_001LOT  # 2 patas
                pnl = gross - cost
                balance += pnl
                trades += 1; wins += pnl > 0
                peak = max(peak, balance)
                max_dd = max(max_dd, (peak - balance) / peak * 100)
                pos = 0
                if balance <= 5:
                    blown = True; break

    return {
        "cap/unit": capital_per_unit,
        "trades": trades,
        "win%": round(wins / trades * 100, 1) if trades else 0.0,
        "final_$": round(balance, 2),
        "return%": round((balance / start - 1) * 100, 1),
        "maxDD%": round(max_dd, 1),
        "blown": "SÍ 💀" if blown else "no",
    }


def main():
    eur = to_daily(pd.read_parquet(DATA_DIR / "EURUSD_H1_6y.parquet"))
    gbp = to_daily(pd.read_parquet(DATA_DIR / "GBPUSD_H1_6y.parquet"))
    print(f"Backtest PAIRS EUR/GBP | cuenta $100 | {eur.index.min().date()} -> "
          f"{eur.index.max().date()} (6 años D1) | cap/unit=200 (conservador)\n")

    configs = {
        "BASE (1:1, sin controles)": PairsConfig(),
        "+ stop_z=3.5":              PairsConfig(stop_z=3.5),
        "+ stop + corr>0.6":         PairsConfig(stop_z=3.5, min_corr=0.6),
        "+ stop + corr + hedge β":   PairsConfig(stop_z=3.5, min_corr=0.6, use_beta=True),
    }
    rows = []
    for name, cfg in configs.items():
        r = simulate(eur, gbp, cfg, start=100.0, capital_per_unit=200.0)
        r = {"config": name, **{k: v for k, v in r.items() if k != "cap/unit"}}
        rows.append(r)
    print(pd.DataFrame(rows).to_string(index=False))
    print("\nObjetivo: bajar el maxDD manteniendo el retorno. Solo se queda lo que ayuda.")


if __name__ == "__main__":
    main()
