"""
Entrenamiento y backtest de la IA (GRU) sobre datos históricos.

Puntos clave para no engañarnos a nosotros mismos:
  • Split TEMPORAL (no aleatorio): train = parte antigua, val = parte reciente.
  • El escalado (media/desv) se ajusta SOLO con train y se aplica a val
    (evita fuga de información del futuro).
  • Las ventanas se generan por separado en cada split (no cruzan el corte).
  • Backtest con coste de spread; se reporta win rate Y expectativa (un win
    rate alto no sirve si las pérdidas son mayores que las ganancias).

Uso:
    python -m model.train                # entrena config por defecto y guarda
    python -m model.experiment           # barre varias configs (ver ese script)
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from config import CHECKPOINT_DIR, DATA_DIR
from model.dataset import FEATURE_COLS, build_target, make_features, make_windows
from model.gru import GRURegressor


@dataclass
class TrainCfg:
    T: int = 32
    horizon: int = 6               # 6 velas adelante: mejor señal que vela-a-vela
    val_fraction: float = 0.2
    epochs: int = 60
    batch: int = 64
    lr: float = 1e-3
    patience: int = 8
    spread_cost: float = 0.00007   # ~0.7 pips por operación
    thr_mult: float = 1.0          # umbral = thr_mult * media|pred| (solo señales fuertes)
    trend_filter: bool = True      # solo operar a favor de la TMA


def _standardize_fit(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std[std < 1e-12] = 1.0
    return mean, std


def _prep(df: pd.DataFrame, cfg: TrainCfg):
    feat = make_features(df)
    feat_values = feat[FEATURE_COLS].to_numpy(dtype=np.float32)
    close = feat["close"].to_numpy(dtype=np.float32)
    times = feat["time"].to_numpy()
    trend = feat["dist_tma20"].to_numpy(dtype=np.float32)  # sin escalar
    target = build_target(close, cfg.horizon)

    n = len(feat_values)
    cut = int(n * (1 - cfg.val_fraction))

    f_mean, f_std = _standardize_fit(feat_values[:cut])
    tgt_train = target[:cut][np.isfinite(target[:cut])]
    t_mean, t_std = float(tgt_train.mean()), float(tgt_train.std() or 1.0)

    feat_scaled = (feat_values - f_mean) / f_std
    target_scaled = (target - t_mean) / t_std

    tr = make_windows(feat_scaled[:cut], target_scaled[:cut], times[:cut], close[:cut], trend[:cut], cfg.T)
    va = make_windows(feat_scaled[cut:], target_scaled[cut:], times[cut:], close[cut:], trend[cut:], cfg.T)
    return tr, va, (f_mean, f_std, t_mean, t_std, n)


def fit_model(tr, va, cfg: TrainCfg, device: str = "cpu"):
    """Entrena un GRU con early stopping y devuelve (model, Xva, best_val_mse)."""
    Xtr = torch.tensor(tr.X, device=device); ytr = torch.tensor(tr.y, device=device)
    Xva = torch.tensor(va.X, device=device); yva = torch.tensor(va.y, device=device)
    loader = DataLoader(TensorDataset(Xtr, ytr), batch_size=cfg.batch, shuffle=True)

    model = GRURegressor(n_features=len(FEATURE_COLS)).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=cfg.lr)
    loss_fn = nn.MSELoss()

    best_val, best_state, bad = float("inf"), None, 0
    for _ in range(1, cfg.epochs + 1):
        model.train()
        for xb, yb in loader:
            opt.zero_grad(); loss_fn(model(xb), yb).backward(); opt.step()
        model.eval()
        with torch.no_grad():
            val_loss = loss_fn(model(Xva), yva).item()
        if val_loss < best_val - 1e-6:
            best_val, best_state, bad = val_loss, {k: v.clone() for k, v in model.state_dict().items()}, 0
        else:
            bad += 1
        if bad >= cfg.patience:
            break
    if best_state is not None:
        model.load_state_dict(best_state)
    return model, Xva, best_val


def train(df: pd.DataFrame, cfg: TrainCfg, device: str = "cpu", verbose: bool = True):
    tr, va, (f_mean, f_std, t_mean, t_std, n) = _prep(df, cfg)
    if verbose:
        print(f"Datos: {n:,} filas | train={len(tr.y):,} | val={len(va.y):,} | "
              f"H={cfg.horizon} T={cfg.T} trend_filter={cfg.trend_filter}")

    model, Xva, best_val = fit_model(tr, va, cfg, device)
    metrics = backtest(model, Xva, va, t_mean, t_std, cfg, device, verbose)
    ckpt = {
        "state_dict": model.state_dict(), "feature_cols": FEATURE_COLS,
        "f_mean": f_mean, "f_std": f_std, "t_mean": t_mean, "t_std": t_std,
        "T": cfg.T, "horizon": cfg.horizon, "thr_mult": cfg.thr_mult,
        "trend_filter": cfg.trend_filter, "val_mse": best_val, "metrics": metrics,
    }
    return model, ckpt, metrics


def backtest(model, Xva, va, t_mean, t_std, cfg: TrainCfg, device, verbose=True) -> dict:
    model.eval()
    with torch.no_grad():
        pred_ret = model(Xva).cpu().numpy() * t_std + t_mean
    real_ret = va.y * t_std + t_mean

    thr = np.mean(np.abs(pred_ret)) * cfg.thr_mult
    signal = np.where(pred_ret > thr, 1.0, np.where(pred_ret < -thr, -1.0, 0.0))

    # Filtro de tendencia: no operar contra la TMA (trend = dist_tma20 sin escalar)
    if cfg.trend_filter:
        against = ((signal > 0) & (va.trend < 0)) | ((signal < 0) & (va.trend > 0))
        signal[against] = 0.0

    traded = signal != 0
    n_trades = int(traded.sum())
    pnl = signal * real_ret - traded * cfg.spread_cost
    wins = pnl[traded] > 0

    dir_ok = (np.sign(pred_ret[traded]) == np.sign(real_ret[traded])) if n_trades else np.array([])
    avg_win = float(pnl[traded][wins].mean()) if wins.any() else 0.0
    avg_loss = float(pnl[traded][~wins].mean()) if (~wins).any() else 0.0

    metrics = {
        "n_val": int(len(real_ret)),
        "n_trades": n_trades,
        "win_rate": round(float(wins.mean()) * 100, 1) if n_trades else 0.0,
        "dir_accuracy": round(float(dir_ok.mean()) * 100, 1) if n_trades else 0.0,
        "cum_return_pct": round(float(pnl.sum()) * 100, 2),
        "avg_win_bp": round(avg_win * 10000, 2),
        "avg_loss_bp": round(avg_loss * 10000, 2),
        "expectancy_bp": round(float(pnl[traded].mean()) * 10000, 3) if n_trades else 0.0,
    }
    if verbose:
        print("=== Backtest (validación, out-of-sample) ===")
        print(f"  Operaciones:   {metrics['n_trades']:,} / {metrics['n_val']:,} ventanas")
        print(f"  WIN RATE:      {metrics['win_rate']:.1f}%   (dirección {metrics['dir_accuracy']:.1f}%)")
        print(f"  Gana media:    {metrics['avg_win_bp']:.2f} bp | Pierde media: {metrics['avg_loss_bp']:.2f} bp")
        print(f"  Expectativa:   {metrics['expectancy_bp']:.3f} bp/op   <-- lo que de verdad importa")
        print(f"  Retorno acum.: {metrics['cum_return_pct']:.2f}% (con spread)")
    return metrics


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=str, default=str(DATA_DIR / "EURUSD_H1_4y.parquet"))
    ap.add_argument("--horizon", type=int, default=TrainCfg.horizon)
    ap.add_argument("--no-trend-filter", action="store_true", help="desactiva el filtro de tendencia")
    args = ap.parse_args()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    torch.manual_seed(42); np.random.seed(42)

    df = pd.read_parquet(args.data)
    cfg = TrainCfg(horizon=args.horizon, trend_filter=not args.no_trend_filter)
    model, ckpt, _ = train(df, cfg, device=device)
    out = CHECKPOINT_DIR / f"{Path(args.data).stem}_gru.pt"
    torch.save(ckpt, out)
    print(f"\nModelo guardado: {out}")


if __name__ == "__main__":
    main()
