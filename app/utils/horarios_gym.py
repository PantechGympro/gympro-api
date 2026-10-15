"""
horarios_gym.py — Horario de apertura por día de la semana para un gimnasio.

SCRUM-224 (item B): antes Gimnasio.configuracion solo guardaba un par global
horario_apertura/horario_cierre -- un mismo horario para los 7 días. Este
módulo centraliza la forma nueva (un dict `horarios` con una entrada por
día, cada una con su propio cerrado/apertura/cierre) y la conversión desde
el formato legacy, para no duplicar esta lógica entre el wizard de
onboarding (owner_gym/onboarding.py) y el perfil del gimnasio
(owner_gym/owner_profile.py, y de ahí la app móvil).
"""
from datetime import datetime

DIAS_SEMANA = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]

HORARIO_DIA_DEFAULT = {"cerrado": False, "apertura": "06:00", "cierre": "22:00"}


def normalizar_horarios(horarios_in, legacy_apertura=None, legacy_cierre=None):
    """
    Normaliza el dict `horarios` que manda el frontend a la forma completa
    de 7 días, saneando cada entrada.

    Si no viene `horarios` (gimnasio aún en el formato legacy de un solo
    par global), se construyen los 7 días a partir de ese par -- así un
    gimnasio que todavía no definió horarios por día sigue mostrando algo
    razonable en lugar de "cerrado todos los días".
    """
    if not isinstance(horarios_in, dict) or not horarios_in:
        base = {
            "cerrado":  False,
            "apertura": legacy_apertura or HORARIO_DIA_DEFAULT["apertura"],
            "cierre":   legacy_cierre or HORARIO_DIA_DEFAULT["cierre"],
        }
        return {dia: dict(base) for dia in DIAS_SEMANA}

    resultado = {}
    for dia in DIAS_SEMANA:
        entrada = horarios_in.get(dia) or {}
        resultado[dia] = {
            "cerrado":  bool(entrada.get("cerrado", False)),
            "apertura": entrada.get("apertura") or HORARIO_DIA_DEFAULT["apertura"],
            "cierre":   entrada.get("cierre") or HORARIO_DIA_DEFAULT["cierre"],
        }
    return resultado


def horarios_desde_configuracion(configuracion: dict):
    """
    Lee el dict `horarios` de `configuracion`, con fallback al par legacy
    global (o al default) para gimnasios que aún no lo tienen -- usado al
    LEER (get_perfil, app móvil), nunca pisa lo guardado.
    """
    configuracion = configuracion or {}
    horarios = configuracion.get("horarios")
    if isinstance(horarios, dict) and horarios:
        return normalizar_horarios(horarios)
    return normalizar_horarios(
        None,
        legacy_apertura=configuracion.get("horario_apertura"),
        legacy_cierre=configuracion.get("horario_cierre"),
    )


def horario_de_hoy(configuracion: dict):
    """
    Horario del día de hoy, para un resumen rápido (p. ej. en el perfil del
    gimnasio o la app móvil) sin que el cliente tenga que conocer la forma
    completa de `horarios`.
    """
    horarios = horarios_desde_configuracion(configuracion)
    dia = DIAS_SEMANA[datetime.now().weekday()]
    return {"dia": dia, **horarios[dia]}
