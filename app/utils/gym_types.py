"""
utils/gym_types.py — Catálogo de tipos de gimnasio y sus configuraciones por defecto.

Cada tipo define:
  - label / description / icon: datos para el UI de onboarding
  - etiqueta_sesion: cómo se llama una asistencia en este tipo ("WOD", "Clase", "Visita"…)
  - modulos: features habilitadas por defecto en el dashboard
  - default_membresias: planes base que se crean automáticamente al registrarse
"""

GYM_TYPES: dict = {
    "gimnasio_tradicional": {
        "label":       "Gimnasio Tradicional",
        "description": "Pesas, cardio y musculación libre",
        "icon":        "💪",
        "etiqueta_sesion": "Visita",
        "modulos": ["asistencia", "pagos", "rutinas", "progreso", "analytics"],
        "default_membresias": [
            {"nombre": "Mensual",     "precio": 500,  "duracion_meses": 1},
            {"nombre": "Trimestral",  "precio": 1350, "duracion_meses": 3},
            {"nombre": "Anual",       "precio": 4800, "duracion_meses": 12},
        ],
    },
    "crossfit_functional": {
        "label":       "CrossFit / Funcional",
        "description": "WODs, clases y entrenamiento en grupo",
        "icon":        "🔥",
        "etiqueta_sesion": "WOD",
        "modulos": ["asistencia", "pagos", "clases", "reservas", "analytics"],
        "default_membresias": [
            {"nombre": "12 clases / mes", "precio": 800,  "duracion_meses": 1},
            {"nombre": "Ilimitado",        "precio": 1200, "duracion_meses": 1},
        ],
    },
    "yoga_pilates": {
        "label":       "Yoga / Pilates",
        "description": "Clases grupales y sesiones privadas",
        "icon":        "🧘",
        "etiqueta_sesion": "Clase",
        "modulos": ["asistencia", "pagos", "reservas", "instructores"],
        "default_membresias": [
            {"nombre": "8 clases / mes", "precio": 600, "duracion_meses": 1},
            {"nombre": "Ilimitado",      "precio": 950, "duracion_meses": 1},
        ],
    },
    "artes_marciales": {
        "label":       "Artes Marciales",
        "description": "BJJ, MMA, Karate, Boxeo y más",
        "icon":        "🥋",
        "etiqueta_sesion": "Entrenamiento",
        "modulos": ["asistencia", "pagos", "cinturones", "competencias", "rutinas"],
        "default_membresias": [
            {"nombre": "Mensual",    "precio": 600,  "duracion_meses": 1},
            {"nombre": "Semestral",  "precio": 3000, "duracion_meses": 6},
        ],
    },
    "spinning_cycling": {
        "label":       "Spinning / Ciclismo",
        "description": "Clases de spinning y ciclismo indoor",
        "icon":        "🚴",
        "etiqueta_sesion": "Clase",
        "modulos": ["asistencia", "pagos", "reservas", "bicicletas"],
        "default_membresias": [
            {"nombre": "8 clases / mes", "precio": 500, "duracion_meses": 1},
            {"nombre": "Ilimitado",      "precio": 800, "duracion_meses": 1},
        ],
    },
    "natacion": {
        "label":       "Natación / Acuático",
        "description": "Carriles, cursos y competencias",
        "icon":        "🏊",
        "etiqueta_sesion": "Sesión",
        "modulos": ["asistencia", "pagos", "carriles", "niveles", "cursos"],
        "default_membresias": [
            {"nombre": "Mensual",    "precio": 700,  "duracion_meses": 1},
            {"nombre": "Trimestral", "precio": 1900, "duracion_meses": 3},
        ],
    },
    "boutique_studio": {
        "label":       "Estudio Boutique",
        "description": "Clases premium con cupo limitado",
        "icon":        "💎",
        "etiqueta_sesion": "Clase",
        "modulos": ["asistencia", "pagos", "reservas", "waitlist", "analytics"],
        "default_membresias": [
            {"nombre": "5 clases",          "precio": 750,  "duracion_meses": 1},
            {"nombre": "Ilimitado mensual", "precio": 1500, "duracion_meses": 1},
        ],
    },
    "otro": {
        "label":       "Otro / Personalizado",
        "description": "Configura la plataforma desde cero",
        "icon":        "⚙️",
        "etiqueta_sesion": "Sesión",
        "modulos": ["asistencia", "pagos"],
        "default_membresias": [
            {"nombre": "Mensual", "precio": 500, "duracion_meses": 1},
        ],
    },
}


def get_gym_type_config(tipo: str) -> dict:
    """Devuelve la config del tipo solicitado; fallback a 'gimnasio_tradicional'."""
    return GYM_TYPES.get(tipo, GYM_TYPES["gimnasio_tradicional"])


def seed_default_memberships(db_session, id_gimnasio: int, tipo: str) -> list:
    """
    Crea los TipoMembresia por defecto para un gimnasio nuevo según su tipo.
    Debe llamarse DENTRO de una transacción activa (onboarding).
    Retorna la lista de objetos creados.
    """
    from app.models.pg.tipo_membresia import TipoMembresia

    config   = get_gym_type_config(tipo)
    creados  = []

    for tm_def in config["default_membresias"]:
        tm = TipoMembresia(
            id_gimnasio     = id_gimnasio,
            nombre          = tm_def["nombre"],
            precio          = tm_def["precio"],
            duracion_meses  = tm_def["duracion_meses"],
            activo          = True,
        )
        db_session.add(tm)
        creados.append(tm)

    return creados
