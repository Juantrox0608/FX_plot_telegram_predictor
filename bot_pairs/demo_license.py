"""
Control simple de vigencia para la version demo.

La demo se activa en el primer arranque y vence despues de DEMO_DURATION_MONTHS
o DEMO_DURATION_DAYS.
El estado se guarda en data/demo_activation.json para no depender de editar una
fecha manualmente en el codigo.
"""
from __future__ import annotations

import calendar
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from config import CONFIG, DATA_DIR


STATE_FILE = DATA_DIR / "demo_activation.json"


@dataclass(frozen=True)
class DemoStatus:
    activated_at: datetime
    expires_at: datetime
    now: datetime

    @property
    def is_expired(self) -> bool:
        return self.now >= self.expires_at

    @property
    def days_left(self) -> int:
        if self.is_expired:
            return 0
        remaining = self.expires_at - self.now
        return max(1, remaining.days + (1 if remaining.seconds else 0))

    def summary(self) -> str:
        start = self.activated_at.strftime("%Y-%m-%d")
        end = self.expires_at.strftime("%Y-%m-%d")
        if self.is_expired:
            return f"Demo vencida. Activada: {start}. Vencio: {end}."
        return f"Demo activa. Vence: {end}. Dias restantes: {self.days_left}."


def _parse_utc(value: str) -> datetime:
    value = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _read_activation(path: Path) -> datetime | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    activated = data.get("activated_at")
    if not activated:
        raise RuntimeError(f"Archivo de demo invalido: {path}")
    return _parse_utc(str(activated))


def _write_activation(path: Path, activated_at: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "activated_at": activated_at.isoformat().replace("+00:00", "Z"),
        "duration_months": CONFIG.demo_duration_months,
        "duration_days": CONFIG.demo_duration_days,
    }
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def _add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _expires_at(activated_at: datetime) -> datetime:
    expires_at = activated_at
    if CONFIG.demo_duration_months:
        expires_at = _add_months(expires_at, CONFIG.demo_duration_months)
    if CONFIG.demo_duration_days:
        expires_at = expires_at + timedelta(days=CONFIG.demo_duration_days)
    return expires_at


def demo_status(now: datetime | None = None) -> DemoStatus:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    state_path = Path(CONFIG.demo_license_file) if CONFIG.demo_license_file else STATE_FILE
    activated_at = _read_activation(state_path)
    if activated_at is None:
        activated_at = now
        _write_activation(state_path, activated_at)
    expires_at = _expires_at(activated_at)
    return DemoStatus(activated_at=activated_at, expires_at=expires_at, now=now)


def ensure_demo_active() -> DemoStatus:
    status = demo_status()
    if status.is_expired:
        raise SystemExit(status.summary())
    return status
