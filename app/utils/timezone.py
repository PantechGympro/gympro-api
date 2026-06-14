"""
utils/timezone.py — Manejo de la zona horaria local del gimnasio.

Problema que resuelve:
    El servidor (Docker) normalmente corre en UTC, así que `datetime.now()` y
    `datetime.now(timezone.utc)` devuelven la fecha/hora UTC. A las 23:30 del
    sábado en México (UTC-6) ya es domingo 05:30 UTC, por lo que un check-in se
    registraba con la fecha del día siguiente.

Solución:
    Usar la zona horaria local configurable (env APP_TIMEZONE, default
    'America/Mexico_City') para calcular "hoy" y la hora de entrada.
"""
import os
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    APP_TZ = ZoneInfo(os.environ.get("APP_TIMEZONE", "America/Mexico_City"))
except Exception:
    # Fallback fijo a UTC-6 si no hay base de datos de zonas horarias (tzdata).
    APP_TZ = timezone(timedelta(hours=-6))


def local_now():
    """Ahora en la zona horaria local del gimnasio (datetime con tz)."""
    return datetime.now(APP_TZ)


def local_now_naive():
    """
    Ahora como datetime *naive* con los valores de reloj locales.
    Útil para colecciones que guardan fechas naive y luego las comparan
    contra rangos también naive (evita el desfase UTC).
    """
    return datetime.now(APP_TZ).replace(tzinfo=None)


def local_today_bounds_naive():
    """(inicio, fin) del día local de hoy, como datetimes naive."""
    start = local_now_naive().replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)
