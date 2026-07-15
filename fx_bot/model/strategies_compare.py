"""
Compara 3 enfoques clásicos SIN predecir dirección, con reglas simples y
parámetros estándar (no optimizados, para no hacer curve-fitting), en D1 sobre
6 años. Métricas comparables: nº trades, win%, retorno acumulado (neto de
spread) y drawdown máximo.

  1) Trend-following  : cruce SMA 50/200 (siempre en mercado, flipea).
  2) Mean-reversion   : Bollinger(20,2) -> comprar bajo la banda, salir en la media.
  3) Pairs (stat-arb) : z-score del spread EURUSD vs GBPUSD (lookback 20, ±2).

Uso: python -m model.strategies_compare
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import DATA_DIR

SPREAD_RET = 0.0001  # ~1 bp de coste round-turn por instrumento (D1)


def to_daily(df: pd.DataFrame) -> pd.DataFrame:
    d = (df.set_index("time")
           .resample("1D")
           .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
           .dropna())
    return d


def _metrics(name: str, rets: list[float]) -> dict:
    r = np.array(rets)
    if len(r) == 0:
        return {"estrategia": name, "trades": 0}
    eq = np.cumsum(r)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq).max() * 100
    return {
        "estrategia": name,
        "trades": len(r),
        "win%": round((r > 0).mean() * 100, 1),
        "retorno%": round(r.sum() * 100, 1),
        "maxDD%": round(dd, 1),
    }


def trend_following(d: pd.DataFrame) -> list[float]:
    c = d["close"]
    sma_f, sma_s = c.rolling(50).mean(), c.rolling(200).mean()
    sig = np.where(sma_f > sma_s, 1, -1).astype(float)
    sig[sma_s.isna().values] = 0
    rets, pos, entry = [], 0, None
    px = c.values
    for i in range(1, len(px)):
        s = sig[i]
        if s != pos and s != 0:                 # flip
            if pos != 0 and entry is not None:  # cerrar trade previo
                rets.append(pos * (px[i] - entry) / entry - SPREAD_RET)
            pos, entry = s, px[i]
    return rets


def mean_reversion(d: pd.DataFrame) -> list[float]:
    c = d["close"]
    mid = c.rolling(20).mean()
    std = c.rolling(20).std(ddof=0)
    up, lo = mid + 2 * std, mid - 2 * std
    px, midv = c.values, mid.values
    upv, lov = up.values, lo.values
    rets, pos, entry = [], 0, None
    for i in range(len(px)):
        if np.isnan(midv[i]):
            continue
        if pos == 0:
            if px[i] < lov[i]:
                pos, entry = 1, px[i]
            elif px[i] > upv[i]:
                pos, entry = -1, px[i]
        else:  # gestionar salida en la media
            if (pos == 1 and px[i] >= midv[i]) or (pos == -1 and px[i] <= midv[i]):
                rets.append(pos * (px[i] - entry) / entry - SPREAD_RET)
                pos, entry = 0, None
    return rets


def pairs(d_eur: pd.DataFrame, d_gbp: pd.DataFrame) -> list[float]:
    j = pd.DataFrame({"eur": d_eur["close"], "gbp": d_gbp["close"]}).dropna()
    spread = np.log(j["eur"]) - np.log(j["gbp"])
    z = (spread - spread.rolling(20).mean()) / spread.rolling(20).std(ddof=0)
    eur, gbp, zz = j["eur"].values, j["gbp"].values, z.values
    rets, pos, e_en, g_en = [], 0, None, None
    for i in range(len(zz)):
        if np.isnan(zz[i]):
            continue
        if pos == 0:
            if zz[i] < -2:      # spread bajo -> long EUR / short GBP
                pos, e_en, g_en = 1, eur[i], gbp[i]
            elif zz[i] > 2:     # spread alto -> short EUR / long GBP
                pos, e_en, g_en = -1, eur[i], gbp[i]
        else:
            if (pos == 1 and zz[i] >= 0) or (pos == -1 and zz[i] <= 0):
                eur_r = (eur[i] - e_en) / e_en
                gbp_r = (gbp[i] - g_en) / g_en
                rets.append(pos * (eur_r - gbp_r) - 2 * SPREAD_RET)
                pos, e_en, g_en = 0, None, None
    return rets


def main():
    eur = to_daily(pd.read_parquet(DATA_DIR / "EURUSD_H1_6y.parquet"))
    gbp = to_daily(pd.read_parquet(DATA_DIR / "GBPUSD_H1_6y.parquet"))
    print(f"Datos D1: EURUSD {len(eur)} días, GBPUSD {len(gbp)} días "
          f"({eur.index.min().date()} -> {eur.index.max().date()})\n")

    rows = [
        _metrics("1) Trend-following (SMA 50/200)", trend_following(eur)),
        _metrics("2) Mean-reversion (Bollinger 20/2)", mean_reversion(eur)),
        _metrics("3) Pairs EUR/GBP (z-score ±2)", pairs(eur, gbp)),
    ]
    res = pd.DataFrame(rows)
    print(res.to_string(index=False))
    print("\nBuy & hold EURUSD (referencia): "
          f"{round((eur['close'].iloc[-1]/eur['close'].iloc[0]-1)*100,1)}%")
    print("\nNota: retorno = suma de retornos por trade (1x notional), neto de spread.")


if __name__ == "__main__":
    main()
