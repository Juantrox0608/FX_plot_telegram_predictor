"""
Filtro de calendario económico: bloquea operar cerca de noticias de alto
impacto para las divisas del símbolo.

Fuente: calendario semanal de ForexFactory en JSON (gratuito). Se cachea y se
refresca cada pocas horas. Si la descarga falla, por seguridad se informa pero
NO se bloquea (fail-open) para no dejar el bot mudo; el trader avisa del fallo.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
REFRESH_HOURS = 6
DEFAULT_BEFORE_MIN = 30   # bloquear desde 30 min antes
DEFAULT_AFTER_MIN = 30    # hasta 30 min después


@dataclass
class Event:
    title: str
    currency: str
    when: datetime
    impact: str


class NewsFilter:
    def __init__(self, before_min: int = DEFAULT_BEFORE_MIN, after_min: int = DEFAULT_AFTER_MIN):
        self.before = timedelta(minutes=before_min)
        self.after = timedelta(minutes=after_min)
        self._events: list[Event] = []
        self._fetched_at: datetime | None = None
        self.last_error: str | None = None

    def _needs_refresh(self) -> bool:
        if self._fetched_at is None:
            return True
        return datetime.now(timezone.utc) - self._fetched_at > timedelta(hours=REFRESH_HOURS)

    def refresh(self) -> bool:
        """Descarga el calendario. Devuelve True si tuvo éxito."""
        try:
            req = urllib.request.Request(FF_URL, headers={"User-Agent": "fxbot/2.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # red caída, formato cambiado, etc.
            self.last_error = str(e)
            return False

        events: list[Event] = []
        for it in data:
            impact = str(it.get("impact", "")).lower()
            if impact != "high":
                continue
            try:
                when = datetime.fromisoformat(it["date"])
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            events.append(Event(
                title=it.get("title", "?"),
                currency=str(it.get("country", "")).upper(),
                when=when.astimezone(timezone.utc),
                impact="High",
            ))
        self._events = events
        self._fetched_at = datetime.now(timezone.utc)
        self.last_error = None
        return True

    def _currencies(self, symbol: str) -> set[str]:
        s = symbol.upper().replace("/", "")
        if len(s) >= 6:
            return {s[:3], s[3:6]}
        return {s}

    def is_blocked(self, symbol: str, now: datetime | None = None) -> tuple[bool, str]:
        """
        Devuelve (bloqueado, motivo). Bloquea si `now` cae en la ventana de una
        noticia de alto impacto de alguna de las divisas del símbolo.
        """
        if self._needs_refresh():
            self.refresh()  # si falla, se opera sin bloqueo (fail-open)

        now = now or datetime.now(timezone.utc)
        curs = self._currencies(symbol)
        for ev in self._events:
            if ev.currency not in curs:
                continue
            if ev.when - self.before <= now <= ev.when + self.after:
                return True, f"{ev.currency} {ev.title} @ {ev.when:%H:%M UTC}"
        return False, ""

    def next_events(self, symbol: str, hours: int = 12) -> list[Event]:
        now = datetime.now(timezone.utc)
        curs = self._currencies(symbol)
        horizon = now + timedelta(hours=hours)
        return sorted(
            [e for e in self._events if e.currency in curs and now <= e.when <= horizon],
            key=lambda e: e.when,
        )
