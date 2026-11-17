"""Datas: tudo em UTC no banco; 'dia' de limites e orçamentos no fuso de São Paulo."""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Sao_Paulo")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def local_day(now: datetime) -> date:
    return now.astimezone(TZ).date()


def local_midnight_utc(now: datetime) -> datetime:
    local = now.astimezone(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(timezone.utc)
