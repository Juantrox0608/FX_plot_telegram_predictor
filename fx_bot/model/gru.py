"""
Modelo GRU para predecir el retorno de la vela futura a partir de una ventana
de features de mercado + indicadores.

Regresión: entrada (B, T, F) -> salida (B, 1) = retorno esperado (en el espacio
estandarizado del target). La capa de estrategia convierte esa predicción en
una señal (comprar/vender/esperar) mediante un umbral.
"""
from __future__ import annotations

import torch
import torch.nn as nn


class GRURegressor(nn.Module):
    def __init__(
        self,
        n_features: int,
        hidden: int = 48,
        num_layers: int = 2,
        dropout: float = 0.2,
    ):
        super().__init__()
        self.gru = nn.GRU(
            input_size=n_features,
            hidden_size=hidden,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.head = nn.Sequential(
            nn.Linear(hidden, hidden // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden // 2, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.gru(x)          # (B, T, H)
        last = out[:, -1, :]          # (B, H) — estado del último paso
        return self.head(last).squeeze(-1)  # (B,)
