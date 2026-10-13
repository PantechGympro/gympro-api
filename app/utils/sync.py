"""
Utilidades para sincronización diferida (SCRUM-168 / HU-30).

Los endpoints que reciben escrituras que la app móvil pudo haber encolado
sin conexión (serie/sesión de entrenamiento completada, medidas corporales)
aceptan un campo opcional `_capturado_en` con la fecha/hora ISO 8601 en que
el socio realizó la acción EN EL DISPOSITIVO — no cuando el servidor
finalmente la procesa al reconectar.

Sin esto, una serie marcada un lunes por la mañana sin señal y sincronizada
el lunes por la noche se registraría con la hora de la noche: rompería la
racha de asistencia, el "entrenamiento de hoy" y cualquier gráfica agrupada
por fecha.

Resolución:
  - Si no viene `_capturado_en` (cliente conectado, comportamiento actual
    sin cambios) o no es parseable, se usa la hora del servidor exactamente
    como antes.
  - Si viene y es parseable, se usa tal cual SIEMPRE QUE no esté en el
    futuro (con una tolerancia de reloj de 5 minutos) — un timestamp futuro
    normalmente indica un reloj de dispositivo mal configurado, y aceptarlo
    sin validar corrompería el historial con fechas fuera de orden.
  - No hay límite de antigüedad: la cola del móvil puede acumular acciones
    por días si el dispositivo pasó mucho tiempo sin conexión.
"""
from datetime import datetime, timedelta

_TOLERANCIA_FUTURO = timedelta(minutes=5)
_CAMPO_DEFECTO = "_capturado_en"


def resolver_capturado_en(data: dict, campo: str = _CAMPO_DEFECTO, ahora: "datetime | None" = None) -> datetime:
    """
    Devuelve el datetime naive a usar como timestamp del evento: el valor de
    `data[campo]` si viene, es parseable y no está en el futuro; si no,
    `ahora` (o `datetime.now()` si no se pasa).
    """
    ahora = ahora or datetime.now()
    crudo = (data or {}).get(campo)
    if not crudo:
        return ahora

    try:
        valor = str(crudo)
        # Python 3.10 (versión de este proyecto) no acepta el sufijo 'Z' en
        # datetime.fromisoformat — soportado recién desde 3.11.
        if valor.endswith("Z"):
            valor = valor[:-1] + "+00:00"
        parsed = datetime.fromisoformat(valor)
        if parsed.tzinfo is not None:
            # El dispositivo registra en su hora local (igual que
            # local_now_naive en el resto del backend); se descarta tzinfo
            # tras convertir a hora local del sistema para no mezclar
            # datetimes naive y aware en el resto del documento.
            parsed = parsed.astimezone().replace(tzinfo=None)
    except (ValueError, TypeError):
        return ahora

    if parsed > ahora + _TOLERANCIA_FUTURO:
        return ahora
    return parsed
