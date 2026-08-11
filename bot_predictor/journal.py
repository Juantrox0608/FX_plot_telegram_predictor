"""
Diario de operaciones (SQLite): registra cada apertura y cierre con todo el
contexto de la señal (predicción IA, confianza, votos de indicadores, ATR,
niveles) y el resultado real (precio de cierre, profit, duración).

Objetivo: acumular datos REALES de mercado (fills, spreads y salidas reales) para
después retroalimentar/reentrenar la IA. La base es un único archivo portable
(`data/journal.db`) que se copia del VPS y se procesa con analyze_journal.py.

Conexión por operación (seguro entre hilos, ya que el loop corre en threads).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    ticket        INTEGER PRIMARY KEY,
    symbol        TEXT,
    timeframe     TEXT,
    direction     INTEGER,
    open_time     TEXT,
    bar_time      TEXT,
    pred_ret      REAL,
    threshold     REAL,
    confidence    REAL,
    votes         TEXT,
    risk_pct      REAL,
    atr           REAL,
    entry         REAL,
    sl            REAL,
    tp            REAL,
    lot           REAL,
    close_time    TEXT,
    close_price   REAL,
    profit        REAL,
    exit_reason   TEXT,
    duration_min  REAL,
    outcome       TEXT,
    status        TEXT
);
"""


class Journal:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self):
        return sqlite3.connect(self.db_path, timeout=10)

    # ---------- escritura ----------
    def log_open(self, *, ticket: int, symbol: str, timeframe: str, direction: int,
                 bar_time, pred_ret: float, threshold: float, confidence: float,
                 votes: dict, risk_pct: float, atr: float, entry: float,
                 sl: float, tp: float, lot: float) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT OR REPLACE INTO trades
                (ticket, symbol, timeframe, direction, open_time, bar_time,
                 pred_ret, threshold, confidence, votes, risk_pct, atr,
                 entry, sl, tp, lot, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'open')""",
                (ticket, symbol, timeframe, direction,
                 datetime.now(timezone.utc).isoformat(), str(bar_time),
                 pred_ret, threshold, confidence, json.dumps(votes), risk_pct, atr,
                 entry, sl, tp, lot),
            )

    def log_close(self, *, ticket: int, close_price: float | None, profit: float | None,
                  exit_reason: str = "") -> None:
        now = datetime.now(timezone.utc)
        with self._conn() as c:
            row = c.execute("SELECT open_time FROM trades WHERE ticket=?", (ticket,)).fetchone()
            duration = None
            if row and row[0]:
                try:
                    duration = (now - datetime.fromisoformat(row[0])).total_seconds() / 60.0
                except Exception:
                    duration = None
            outcome = None if profit is None else ("win" if profit >= 0 else "loss")
            c.execute(
                """UPDATE trades SET close_time=?, close_price=?, profit=?,
                   exit_reason=?, duration_min=?, outcome=?, status='closed'
                   WHERE ticket=?""",
                (now.isoformat(), close_price, profit, exit_reason, duration, outcome, ticket),
            )

    # ---------- lectura ----------
    def stats(self) -> dict:
        with self._conn() as c:
            n_open = c.execute("SELECT COUNT(*) FROM trades WHERE status='open'").fetchone()[0]
            closed = c.execute(
                "SELECT profit, outcome FROM trades WHERE status='closed' AND profit IS NOT NULL"
            ).fetchall()
        n = len(closed)
        if n == 0:
            return {"open": n_open, "closed": 0, "win_rate": 0.0,
                    "total_profit": 0.0, "expectancy": 0.0}
        profits = [r[0] for r in closed]
        wins = [p for p in profits if p >= 0]
        return {
            "open": n_open,
            "closed": n,
            "win_rate": round(len(wins) / n * 100, 1),
            "total_profit": round(sum(profits), 2),
            "expectancy": round(sum(profits) / n, 4),
        }

    def export_csv(self, path: str | Path) -> int:
        import csv
        with self._conn() as c:
            cur = c.execute("SELECT * FROM trades ORDER BY open_time")
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(rows)
        return len(rows)
