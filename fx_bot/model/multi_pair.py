"""
Prueba la estrategia de pairs (config robusta) en varias combinaciones de
monedas, para ver que el edge no es exclusivo de EUR/GBP.

Usa la misma sim realista de cuenta $100 (2 patas, lote mínimo, stop_z + filtro
de correlación). Reporta por combinación: trades, win%, crecimiento de $100 y DD.

Uso: python -m model.multi_pair
"""
from __future__ import annotations

from itertools import combinations

import pandas as pd

from config import DATA_DIR
from model.pairs_backtest import simulate
from model.strategies_compare import to_daily
from pairs_strategy import PairsConfig

# Todos /USD (positivamente correlacionados) -> spread log(A)-log(B) directo
XXXUSD = ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"]
# USD/XXX entre sí también correlacionan
USDXXX = ["USDCHF", "USDCAD"]


def _load(sym: str) -> pd.DataFrame:
    return to_daily(pd.read_parquet(DATA_DIR / f"{sym}_H1_6y.parquet"))


def main():
    cfg = PairsConfig(stop_z=3.5, min_corr=0.6)  # config robusta
    data = {}
    for s in XXXUSD + USDXXX:
        try:
            data[s] = _load(s)
        except Exception:
            pass

    combos = list(combinations([s for s in XXXUSD if s in data], 2))
    if all(s in data for s in USDXXX):
        combos.append((USDXXX[0], USDXXX[1]))

    rows = []
    for a, b in combos:
        r = simulate(data[a], data[b], cfg, start=100.0, capital_per_unit=200.0)
        rows.append({
            "par": f"{a[:3]}/{b[:3]}",
            "trades": r["trades"], "win%": r["win%"],
            "$100→": f"${r['final_$']:.0f}", "retorno%": r["return%"],
            "maxDD%": r["maxDD%"], "quiebra": r["blown"],
        })

    res = pd.DataFrame(rows).sort_values("retorno%", ascending=False)
    print("PAIRS en varias monedas | cuenta $100 @1:500 | 6 años D1 | config robusta\n")
    print(res.to_string(index=False))
    ok = res[(res["retorno%"] > 0) & (res["quiebra"] == "no")]
    print(f"\nCombinaciones rentables y sin quiebre: {len(ok)}/{len(res)}")


if __name__ == "__main__":
    main()
