"""
Estrategia: convierte la predicción de la IA + la lectura de indicadores en una
señal operable.

Filosofía (según lo pedido): NO ser restrictivo. Siempre que la IA da una señal
fuerte (supera el umbral) se opera. Los indicadores no vetan: MODULAN el riesgo.
  • Poca confirmación de indicadores  -> se arriesga el mínimo (base_risk).
  • Confirmación total                 -> se arriesga hasta el máximo (max_risk).
Así se aprovechan todas las oportunidades y se pone más capital donde hay más
convicción.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import torch

import indicators as ind
from model.dataset import FEATURE_COLS, make_features
from model.gru import GRURegressor


@dataclass
class Signal:
    direction: int          # +1 compra, -1 venta, 0 esperar
    pred_ret: float         # retorno esperado por la IA (horizonte del modelo)
    threshold: float        # umbral usado
    confidence: float       # 0..1 (acuerdo de indicadores)
    risk_pct: float         # % de riesgo resultante para esta operación
    atr: float              # ATR actual (para SL/TP)
    entry_ref: float        # precio de referencia (último close)
    votes: dict = field(default_factory=dict)
    reason: str = ""


def load_model(ckpt_path: str | Path, device: str = "cpu"):
    ckpt = torch.load(ckpt_path, map_location=device, weights_only=False)
    model = GRURegressor(n_features=len(ckpt["feature_cols"])).to(device)
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    return model, ckpt


def _indicator_votes(df: pd.DataFrame) -> dict:
    """Votos de indicadores en la última vela: +1 alcista, -1 bajista."""
    ind_df = ind.add_all(df).iloc[-1]
    close = ind_df["close"]
    votes = {
        "tma": 1 if close > ind_df["tma20"] else -1,
        "sma50": 1 if close > ind_df["sma50"] else -1,
        "macd": 1 if ind_df["macd_hist"] > 0 else -1,
        "bollinger": 1 if close > ind_df["bb_mid"] else -1,
        "rsi": 1 if ind_df["rsi14"] > 50 else -1,
    }
    return votes


def compute_signal(
    df: pd.DataFrame,
    model,
    ckpt: dict,
    base_risk: float = 1.0,
    max_risk: float = 5.0,
    device: str = "cpu",
) -> Signal:
    """
    df: DataFrame OHLC reciente (al menos T + calentamiento de indicadores; ~120
    velas es de sobra). Devuelve una Signal lista para dimensionar y ejecutar.
    """
    T = ckpt["T"]
    f_mean, f_std = ckpt["f_mean"], ckpt["f_std"]
    t_mean, t_std = ckpt["t_mean"], ckpt["t_std"]
    threshold = ckpt["signal_threshold"]

    feat = make_features(df)
    if len(feat) < T:
        raise ValueError(f"Pocas velas para la ventana: {len(feat)} < T={T}")

    window = feat[FEATURE_COLS].to_numpy(dtype=np.float32)[-T:]
    window = (window - f_mean) / f_std
    x = torch.tensor(window[None, :, :], device=device)  # (1, T, F)
    with torch.no_grad():
        pred_ret = float(model(x).cpu().numpy()[0]) * t_std + t_mean

    atr = float(ind.atr(df, 14).iloc[-1])
    entry_ref = float(df["close"].iloc[-1])

    if pred_ret > threshold:
        direction = 1
    elif pred_ret < -threshold:
        direction = -1
    else:
        return Signal(0, pred_ret, threshold, 0.0, 0.0, atr, entry_ref,
                      reason="IA sin señal fuerte (dentro del umbral)")

    votes = _indicator_votes(df)
    agree = sum(1 for v in votes.values() if v == direction) / len(votes)
    risk_pct = round(base_risk + (max_risk - base_risk) * agree, 2)

    lado = "COMPRA" if direction > 0 else "VENTA"
    reason = (f"IA predice {pred_ret*10000:+.1f} bp ({lado}); "
              f"indicadores confirman {int(agree*100)}% -> riesgo {risk_pct}%")

    return Signal(direction, pred_ret, threshold, round(agree, 2), risk_pct,
                  atr, entry_ref, votes, reason)
