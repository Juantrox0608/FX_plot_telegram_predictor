"""
Barrido de configuraciones para buscar mayor win rate / mejor expectativa.

Para cada horizonte se entrena UN modelo y se evalúan varias combinaciones de
umbral (thr_mult) y filtro de tendencia reutilizando el mismo modelo (el
backtest no requiere reentrenar). Reporta una tabla ordenada.

Uso:
    python -m model.experiment
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import torch

from config import DATA_DIR
from model.train import TrainCfg, _prep, backtest, fit_model


HORIZONS = [1, 3, 6, 12]
THR_MULTS = [0.5, 1.0, 1.5]
TREND_FILTERS = [False, True]


def run(data_path):
    df = pd.read_parquet(data_path)
    device = "cpu"
    rows = []

    for h in HORIZONS:
        torch.manual_seed(42); np.random.seed(42)
        base = TrainCfg(horizon=h)
        tr, va, (f_mean, f_std, t_mean, t_std, n) = _prep(df, base)
        model, Xva, best_val = fit_model(tr, va, base, device)
        print(f"[H={h:>2}] entrenado (val_mse={best_val:.4f}, train={len(tr.y):,}, val={len(va.y):,})")

        for thr in THR_MULTS:
            for tf in TREND_FILTERS:
                cfg = TrainCfg(horizon=h, thr_mult=thr, trend_filter=tf)
                m = backtest(model, Xva, va, t_mean, t_std, cfg, device, verbose=False)
                rows.append({
                    "H": h, "thr": thr, "trend": "sí" if tf else "no",
                    "trades": m["n_trades"], "win%": m["win_rate"],
                    "dir%": m["dir_accuracy"], "exp_bp": m["expectancy_bp"],
                    "ret%": m["cum_return_pct"],
                })

    res = pd.DataFrame(rows)
    # Filtramos configs con muy pocas operaciones (poco fiables)
    fiable = res[res["trades"] >= 50].copy()

    print("\n================= TOP por WIN RATE =================")
    print(fiable.sort_values("win%", ascending=False).head(8).to_string(index=False))

    print("\n============== TOP por EXPECTATIVA (bp/op) ==============")
    print(fiable.sort_values("exp_bp", ascending=False).head(8).to_string(index=False))
    return res


if __name__ == "__main__":
    run(DATA_DIR / "EURUSD_H1_4y.parquet")
