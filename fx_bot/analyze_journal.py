"""
Procesa el diario de operaciones (data/journal.db) para analizar el
comportamiento REAL del bot y preparar datos para reentrenar la IA.

Uso:
    python analyze_journal.py                 # resumen + desgloses
    python analyze_journal.py --export out.csv  # vuelca todo a CSV

Cómo retroalimenta la IA (idea para cuando haya datos de H1 del VPS):
  cada fila cerrada tiene el contexto de la señal (pred_ret, confianza, votos,
  bar_time) y el resultado real (profit, outcome, exit_reason). Con el bar_time
  se reconstruyen las features desde el histórico y se etiquetan con el
  resultado REAL de mercado -> nuevas muestras para afinar el modelo con lo que
  de verdad pasó (fills, spread y salidas reales), no con supuestos de backtest.
"""
from __future__ import annotations

import argparse
import sqlite3

import pandas as pd

from config import DATA_DIR

DB = DATA_DIR / "journal.db"


def load() -> pd.DataFrame:
    with sqlite3.connect(str(DB)) as c:
        df = pd.read_sql_query("SELECT * FROM trades", c)
    if not df.empty:
        df["open_time"] = pd.to_datetime(df["open_time"], errors="coerce", utc=True)
        df["bar_time"] = pd.to_datetime(df["bar_time"], errors="coerce", utc=True)
    return df


def _block(df: pd.DataFrame, by: str) -> pd.DataFrame:
    g = df.groupby(by)
    return pd.DataFrame({
        "trades": g.size(),
        "win%": (g["outcome"].apply(lambda s: (s == "win").mean()) * 100).round(1),
        "profit": g["profit"].sum().round(2),
        "exp": g["profit"].mean().round(4),
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", type=str, default="")
    args = ap.parse_args()

    df = load()
    if df.empty:
        print("El diario está vacío todavía (data/journal.db sin operaciones).")
        return

    closed = df[df["status"] == "closed"].copy()
    print(f"Total registradas: {len(df)} | cerradas: {len(closed)} | abiertas: {len(df)-len(closed)}")
    if closed.empty:
        print("Aún no hay operaciones cerradas para analizar.")
        return

    wins = (closed["outcome"] == "win").mean() * 100
    print(f"\n=== GLOBAL ===")
    print(f"Win rate: {wins:.1f}% | P/L total: {closed['profit'].sum():+.2f} | "
          f"Expectativa: {closed['profit'].mean():+.4f}/op | "
          f"Duración media: {closed['duration_min'].mean():.1f} min")

    closed["dir"] = closed["direction"].map({1: "COMPRA", -1: "VENTA"})
    print("\n=== Por dirección ==="); print(_block(closed, "dir").to_string())
    if closed["exit_reason"].notna().any():
        print("\n=== Por motivo de salida ==="); print(_block(closed, "exit_reason").to_string())

    closed["hour"] = closed["open_time"].dt.hour
    print("\n=== Por hora (UTC) ==="); print(_block(closed, "hour").to_string())

    closed["conf_bucket"] = pd.cut(closed["confidence"], [-0.01, 0.2, 0.4, 0.6, 0.8, 1.0])
    print("\n=== Por confianza de indicadores ==="); print(_block(closed, "conf_bucket").to_string())

    if args.export:
        df.to_csv(args.export, index=False)
        print(f"\nExportado a {args.export} ({len(df)} filas)")


if __name__ == "__main__":
    main()
