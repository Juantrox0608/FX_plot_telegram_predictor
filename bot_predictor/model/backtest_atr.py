"""
Backtest EVENTO A EVENTO de la salida real por SL/TP (ATR), que es como operará
el bot en vivo (a diferencia del backtest de train.py, que salía tras H velas).

Reglas:
  • Señal de la IA (|pred| > umbral) -> entrar al cierre de esa vela.
  • SL/TP = entrada ± ATR*mult. Se revisan high/low de cada vela siguiente.
  • Si en una misma vela se tocan SL y TP, se asume SL primero (conservador).
  • Una posición a la vez (no solapadas). Tope de duración configurable.
  • Coste de spread por operación.
  • Solo se opera en el tramo de VALIDACIÓN (out-of-sample).

Métricas: win rate, expectativa, profit factor, retorno acumulado, drawdown máx.

Uso:
    python -m model.backtest_atr
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import torch

import indicators as ind
from config import CHECKPOINT_DIR, DATA_DIR
from model.dataset import FEATURE_COLS, make_features
from model.train import TrainCfg
from strategy import load_model

SPREAD = 0.00007
MAX_HOLD = 48  # velas máximas en una operación (H1: 2 días)


def _predict_all(df: pd.DataFrame, model, ckpt) -> pd.DataFrame:
    """Devuelve un frame alineado con time, high, low, close, atr, pred_ret, cut."""
    T = ckpt["T"]
    f_mean, f_std = ckpt["f_mean"], ckpt["f_std"]
    t_mean, t_std = ckpt["t_mean"], ckpt["t_std"]

    feat = make_features(df)
    d = ind.add_all(df)[["time", "high", "low", "atr14"]]
    feat = feat.merge(d, on="time", how="left")

    scaled = (feat[FEATURE_COLS].to_numpy(np.float32) - f_mean) / f_std
    n = len(scaled)
    preds = np.full(n, np.nan, dtype=np.float32)

    # ventanas en batch
    idx = list(range(T - 1, n))
    X = np.stack([scaled[i - T + 1 : i + 1] for i in idx]).astype(np.float32)
    with torch.no_grad():
        out = model(torch.tensor(X)).cpu().numpy() * t_std + t_mean
    for k, i in enumerate(idx):
        preds[i] = out[k]

    feat["pred_ret"] = preds
    return feat


def simulate(feat: pd.DataFrame, thr: float, cut: int,
             sl_mult: float, tp_mult: float, max_hold: int = MAX_HOLD) -> dict:
    close = feat["close"].to_numpy(np.float64)
    high = feat["high"].to_numpy(np.float64)
    low = feat["low"].to_numpy(np.float64)
    atr = feat["atr14"].to_numpy(np.float64)
    pred = feat["pred_ret"].to_numpy(np.float64)
    n = len(close)

    trades = []
    i = max(cut, 1)
    while i < n - 1:
        p = pred[i]
        if not np.isfinite(p) or abs(p) <= thr or not np.isfinite(atr[i]) or atr[i] <= 0:
            i += 1
            continue
        direction = 1 if p > 0 else -1
        entry = close[i]
        sl_d = atr[i] * sl_mult
        tp_d = atr[i] * tp_mult
        if direction > 0:
            sl, tp = entry - sl_d, entry + tp_d
        else:
            sl, tp = entry + sl_d, entry - tp_d

        exit_price = None
        j = i + 1
        end = min(n, i + 1 + max_hold)
        while j < end:
            if direction > 0:
                if low[j] <= sl:   exit_price = sl; break
                if high[j] >= tp:  exit_price = tp; break
            else:
                if high[j] >= sl:  exit_price = sl; break
                if low[j] <= tp:   exit_price = tp; break
            j += 1
        if exit_price is None:
            exit_price = close[min(j, n - 1)]  # cierre por tiempo

        ret = direction * (exit_price - entry) / entry - SPREAD
        trades.append(ret)
        i = j + 1  # sin solapar

    trades = np.array(trades)
    if len(trades) == 0:
        return {"sl": sl_mult, "tp": tp_mult, "trades": 0}

    wins = trades > 0
    gross_win = trades[wins].sum()
    gross_loss = -trades[~wins].sum()
    equity = np.cumsum(trades)
    peak = np.maximum.accumulate(equity)
    max_dd = float((peak - equity).max())

    return {
        "sl": sl_mult, "tp": tp_mult,
        "trades": int(len(trades)),
        "win%": round(float(wins.mean()) * 100, 1),
        "exp_bp": round(float(trades.mean()) * 10000, 2),
        "pf": round(float(gross_win / gross_loss), 2) if gross_loss > 0 else float("inf"),
        "ret%": round(float(trades.sum()) * 100, 2),
        "maxDD%": round(max_dd * 100, 2),
    }


def simulate_timed(feat: pd.DataFrame, thr: float, cut: int,
                   sl_mult: float, hold: int) -> dict:
    """
    Salida HÍBRIDA: SL de protección + salida por tiempo tras `hold` velas
    (o antes si salta el SL). Sin TP lejano.
    """
    close = feat["close"].to_numpy(np.float64)
    high = feat["high"].to_numpy(np.float64)
    low = feat["low"].to_numpy(np.float64)
    atr = feat["atr14"].to_numpy(np.float64)
    pred = feat["pred_ret"].to_numpy(np.float64)
    n = len(close)

    trades = []
    i = max(cut, 1)
    while i < n - 1:
        p = pred[i]
        if not np.isfinite(p) or abs(p) <= thr or not np.isfinite(atr[i]) or atr[i] <= 0:
            i += 1
            continue
        direction = 1 if p > 0 else -1
        entry = close[i]
        sl_d = atr[i] * sl_mult
        sl = entry - sl_d if direction > 0 else entry + sl_d

        exit_price = None
        j = i + 1
        end = min(n, i + 1 + hold)
        while j < end:
            if direction > 0 and low[j] <= sl:
                exit_price = sl; break
            if direction < 0 and high[j] >= sl:
                exit_price = sl; break
            j += 1
        if exit_price is None:
            exit_price = close[min(j, n - 1)]  # salida por tiempo

        ret = direction * (exit_price - entry) / entry - SPREAD
        trades.append(ret)
        i = j + 1

    trades = np.array(trades)
    if len(trades) == 0:
        return {"sl": sl_mult, "hold": hold, "trades": 0}
    wins = trades > 0
    gross_win = trades[wins].sum(); gross_loss = -trades[~wins].sum()
    equity = np.cumsum(trades); peak = np.maximum.accumulate(equity)
    return {
        "sl": sl_mult, "hold": hold, "trades": int(len(trades)),
        "win%": round(float(wins.mean()) * 100, 1),
        "exp_bp": round(float(trades.mean()) * 10000, 2),
        "pf": round(float(gross_win / gross_loss), 2) if gross_loss > 0 else float("inf"),
        "ret%": round(float(trades.sum()) * 100, 2),
        "maxDD%": round(float((peak - equity).max()) * 100, 2),
    }


def main():
    df = pd.read_parquet(DATA_DIR / "EURUSD_H1_4y.parquet")
    model, ckpt = load_model(CHECKPOINT_DIR / "EURUSD_H1_4y_gru.pt")
    thr = ckpt["signal_threshold"]

    feat = _predict_all(df, model, ckpt)
    cut = int(len(feat) * (1 - TrainCfg.val_fraction))
    print(f"Backtest sobre validación: filas={len(feat):,}, val desde "
          f"{pd.Timestamp(feat['time'].iloc[cut]).date()}\n")

    rows = []
    for sl_mult in [1.0, 1.5, 2.0]:
        for tp_mult in [1.5, 2.0, 3.0, 4.0]:
            rows.append(simulate(feat, thr, cut, sl_mult, tp_mult))
    res = pd.DataFrame(rows).sort_values("ret%", ascending=False)
    print("=== A) Salida SL/TP por ATR ===")
    print(res.to_string(index=False))

    rows2 = []
    for sl_mult in [1.0, 1.5, 2.0, 3.0]:
        for hold in [3, 6, 9, 12]:
            rows2.append(simulate_timed(feat, thr, cut, sl_mult, hold))
    res2 = pd.DataFrame(rows2).sort_values("ret%", ascending=False)
    print("\n=== B) Salida HÍBRIDA (SL protección + salida por tiempo) ===")
    print(res2.to_string(index=False))
    print("\nNota: 'ret%' = suma de retornos por operación (1x notional), no compuesto.")


if __name__ == "__main__":
    main()
