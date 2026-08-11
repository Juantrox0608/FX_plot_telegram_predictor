"""
Simulador REALISTA de una cuenta pequeña (por defecto $100 a 1:500) operando la
estrategia sobre datos históricos, con salida por SL/TP (ATR) evento a evento.

Modela lo que un backtest de % no captura:
  • Lote mínimo 0.01 -> en una cuenta de $100 el riesgo real por trade no puede
    bajar de ~1.8%, aunque pidas 1%.
  • Margen (apalancamiento): si no alcanza para el lote, se reduce o no se opera.
  • Spread por operación.
  • Compounding: el tamaño escala con el balance vivo.
  • Quiebre: si el balance no cubre el margen del lote mínimo -> cuenta liquidada.

Solo se simula sobre el tramo de VALIDACIÓN (out-of-sample).

Uso:
    python -m model.account_sim
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import CHECKPOINT_DIR, DATA_DIR
from model.backtest_atr import _predict_all
from model.train import TrainCfg
from strategy import load_model

# Constantes de mercado / cuenta
CONTRACT = 100_000          # 1.0 lote EURUSD = 100k EUR
PIP = 0.0001
PIP_VALUE_PER_LOT = 10.0    # USD por pip por 1.0 lote (EURUSD, cuenta USD)
MIN_LOT, LOT_STEP, MAX_LOT = 0.01, 0.01, 100.0
SPREAD_PIPS = 0.7
MAX_HOLD = 48


def _round_lot(v: float) -> float:
    return max(MIN_LOT, np.floor(v / LOT_STEP) * LOT_STEP)


def simulate_account(feat, thr, cut, risk_pct, sl_mult=1.5, tp_mult=1.5,
                     start=100.0, leverage=500) -> dict:
    close = feat["close"].to_numpy(float)
    high = feat["high"].to_numpy(float)
    low = feat["low"].to_numpy(float)
    atr = feat["atr14"].to_numpy(float)
    pred = feat["pred_ret"].to_numpy(float)
    n = len(close)

    balance = start
    peak = start
    max_dd = 0.0
    trades = wins = 0
    blown = False
    i = max(cut, 1)

    while i < n - 1:
        p = pred[i]
        if not np.isfinite(p) or abs(p) <= thr or not np.isfinite(atr[i]) or atr[i] <= 0:
            i += 1
            continue
        direction = 1 if p > 0 else -1
        entry = close[i]
        sl_d = max(atr[i] * sl_mult, SPREAD_PIPS * PIP)
        tp_d = max(atr[i] * tp_mult, SPREAD_PIPS * PIP)
        sl = entry - sl_d if direction > 0 else entry + sl_d
        tp = entry + tp_d if direction > 0 else entry - tp_d

        # --- Sizing sobre balance vivo ---
        risk_amount = balance * risk_pct / 100.0
        loss_per_lot = (sl_d / PIP) * PIP_VALUE_PER_LOT
        lot = _round_lot(risk_amount / loss_per_lot) if loss_per_lot > 0 else MIN_LOT
        lot = min(lot, MAX_LOT)

        # --- Margen: ¿alcanza? ---
        margin_min = MIN_LOT * CONTRACT * entry / leverage
        if balance <= margin_min:
            blown = True
            break
        margin = lot * CONTRACT * entry / leverage
        if margin > balance:  # reducir al máximo que permita el margen
            lot = _round_lot(balance * leverage / (CONTRACT * entry))
            if lot < MIN_LOT:
                blown = True
                break

        # --- Salida por SL/TP ---
        exit_price = None
        j = i + 1
        end = min(n, i + 1 + MAX_HOLD)
        while j < end:
            if direction > 0:
                if low[j] <= sl: exit_price = sl; break
                if high[j] >= tp: exit_price = tp; break
            else:
                if high[j] >= sl: exit_price = sl; break
                if low[j] <= tp: exit_price = tp; break
            j += 1
        if exit_price is None:
            exit_price = close[min(j, n - 1)]

        pnl_pips = direction * (exit_price - entry) / PIP
        pnl = lot * PIP_VALUE_PER_LOT * (pnl_pips - SPREAD_PIPS)
        balance += pnl
        trades += 1
        wins += pnl > 0
        peak = max(peak, balance)
        max_dd = max(max_dd, (peak - balance) / peak * 100)
        if balance <= margin_min:
            blown = True
            break
        i = j + 1

    return {
        "risk%": risk_pct,
        "trades": trades,
        "win%": round(wins / trades * 100, 1) if trades else 0.0,
        "final_$": round(balance, 2),
        "return%": round((balance / start - 1) * 100, 1),
        "maxDD%": round(max_dd, 1),
        "blown": "SÍ 💀" if blown else "no",
    }


def main():
    df = pd.read_parquet(DATA_DIR / "EURUSD_H1_6y.parquet")
    model, ckpt = load_model(CHECKPOINT_DIR / "EURUSD_H1_6y_gru.pt")
    thr = ckpt["signal_threshold"]
    feat = _predict_all(df, model, ckpt)
    cut = int(len(feat) * (1 - TrainCfg.val_fraction))
    print(f"Simulación cuenta $100 @ 1:500 | validación desde "
          f"{pd.Timestamp(feat['time'].iloc[cut]).date()} "
          f"({len(feat)-cut:,} velas out-of-sample)\n")

    rows = [simulate_account(feat, thr, cut, r) for r in (1, 2, 3, 5)]
    res = pd.DataFrame(rows)
    print(res.to_string(index=False))
    print("\nInterpretación: 'blown' = la cuenta se quedó sin margen (liquidada).")


if __name__ == "__main__":
    main()
