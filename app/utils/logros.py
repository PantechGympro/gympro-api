"""
app/utils/logros.py -- Motor de logros, rangos y medallas (HU-26 / SCRUM-206).

Resumen de diseño
------------------
- Catálogo fijo (ACHIEVEMENT_CATALOG): cada entrada es una medalla concreta,
  agrupada en "familias" (racha, rutinas, superacion...) que forman niveles
  progresivos (bronce/plata/oro) -- misma semántica de rango que ya usa
  components/progress/MuscleMap2D.tsx en el móvil (mismo lenguaje visual en
  toda la gamificación, no una paleta nueva inventada aparte).
- Persistencia (colección Mongo `logros_miembro`): una medalla, una vez
  otorgada, NUNCA se revoca ni recalcula su fecha, aunque la métrica que la
  originó cambie después (p.ej. una racha de 30 días que luego se rompe --
  el miembro se queda con la medalla "Disciplinado" para siempre, igual que
  cualquier sistema real de logros). `calcular_logros_miembro` primero
  revisa qué ya está persistido; solo evalúa en vivo, y otorga, lo que
  todavía NO existe en `logros_miembro`. El upsert usa $setOnInsert, así que
  llamar esta función muchas veces es seguro (idempotente): nunca pisa una
  fecha de obtención ya guardada.
- "Progreso visible hacia el siguiente logro": para cada familia con niveles
  (racha, rutinas, superacion) se calcula un único valor de progreso (la
  métrica cruda, p.ej. racha actual en días) y se compara contra el umbral
  de cada nivel -- el nivel aún no conseguido más bajo es "el siguiente".
- Criterio de aceptación "ningún logro se otorga mientras las métricas de
  las que depende estén en revisión": si la recolección de datos crudos
  (ctx) falla, NINGÚN logro nuevo se evalúa ni se otorga en esa llamada --
  se devuelven únicamente los ya persistidos, y el resto queda marcado como
  "en_revision" en vez de con un progreso (posiblemente incorrecto).

Índice recomendado en Mongo (crear una vez, a mano):
    db.logros_miembro.create_index([("id_miembro", 1), ("tipo", 1)], unique=True)
"""
from datetime import datetime, timedelta

# Reutiliza el cálculo de racha ya validado (misma fuente de verdad que ya ve
# el miembro en "Racha" del home) en vez de duplicar la lógica de días
# consecutivos. Vive en un módulo compartido (no en user_dashboard.py) para
# no crear un import circular entre ese blueprint y este motor.
from app.utils.racha import calcular_racha


ACHIEVEMENT_CATALOG = [
    # ── Primera vez ──────────────────────────────────────────────────────
    {"id": "primera_sesion", "familia": "primera_vez", "orden": 1,
     "nombre": "Primer paso", "icono": "flag",
     "descripcion": "Registra tu primera asistencia al gimnasio.",
     "nivel": "especial", "color": "#22C55E", "objetivo": 1,
     "metrica": "total_asistencias"},

    # ── Racha de asistencia (días consecutivos) ─────────────────────────
    {"id": "racha_7", "familia": "racha", "orden": 1,
     "nombre": "Constante", "icono": "flame",
     "descripcion": "Asiste 7 días consecutivos.",
     "nivel": "bronce", "color": "#D97706", "objetivo": 7,
     "metrica": "racha"},
    {"id": "racha_30", "familia": "racha", "orden": 2,
     "nombre": "Disciplinado", "icono": "flame",
     "descripcion": "Asiste 30 días consecutivos.",
     "nivel": "plata", "color": "#CBD5E1", "objetivo": 30,
     "metrica": "racha"},
    {"id": "racha_90", "familia": "racha", "orden": 3,
     "nombre": "Inquebrantable", "icono": "flame",
     "descripcion": "Asiste 90 días consecutivos.",
     "nivel": "oro", "color": "#FBBF24", "objetivo": 90,
     "metrica": "racha"},

    # ── Rutinas completadas (total histórico de sesiones registradas) ───
    {"id": "rutinas_10", "familia": "rutinas", "orden": 1,
     "nombre": "En marcha", "icono": "barbell",
     "descripcion": "Completa 10 entrenamientos.",
     "nivel": "bronce", "color": "#D97706", "objetivo": 10,
     "metrica": "total_entrenamientos"},
    {"id": "rutinas_50", "familia": "rutinas", "orden": 2,
     "nombre": "Veterano", "icono": "barbell",
     "descripcion": "Completa 50 entrenamientos.",
     "nivel": "plata", "color": "#CBD5E1", "objetivo": 50,
     "metrica": "total_entrenamientos"},
    {"id": "rutinas_100", "familia": "rutinas", "orden": 3,
     "nombre": "Imparable", "icono": "barbell",
     "descripcion": "Completa 100 entrenamientos.",
     "nivel": "oro", "color": "#FBBF24", "objetivo": 100,
     "metrica": "total_entrenamientos"},

    # ── Constancia mensual (mejor mes histórico por asistencias) ────────
    {"id": "constancia_mensual", "familia": "constancia", "orden": 1,
     "nombre": "Mes consistente", "icono": "calendar",
     "descripcion": "Entrena al menos 12 veces en un mismo mes calendario.",
     "nivel": "especial", "color": "#22C55E", "objetivo": 12,
     "metrica": "max_asistencias_mes"},

    # ── Superación de carga previa (marcas personales de volumen) ───────
    {"id": "pr_1", "familia": "superacion", "orden": 1,
     "nombre": "Nuevo límite", "icono": "trending-up",
     "descripcion": "Supera por primera vez la carga de una sesión anterior.",
     "nivel": "bronce", "color": "#D97706", "objetivo": 1,
     "metrica": "total_prs"},
    {"id": "pr_5", "familia": "superacion", "orden": 2,
     "nombre": "En ascenso", "icono": "trending-up",
     "descripcion": "Logra 5 nuevas marcas personales de volumen.",
     "nivel": "plata", "color": "#CBD5E1", "objetivo": 5,
     "metrica": "total_prs"},
    {"id": "pr_15", "familia": "superacion", "orden": 3,
     "nombre": "Leyenda personal", "icono": "trending-up",
     "descripcion": "Logra 15 nuevas marcas personales de volumen.",
     "nivel": "oro", "color": "#FBBF24", "objetivo": 15,
     "metrica": "total_prs"},
]


def _contar_prs(mdb, id_miembro):
    """
    Número de veces que una sesión superó el volumen máximo de TODAS las
    sesiones anteriores a ella (marca personal real, en orden cronológico --
    no basta comparar contra la sesión inmediatamente anterior).
    """
    sesiones = list(
        mdb.entrenamientos_realizados.find(
            {"id_miembro": id_miembro}, {"volumen_total": 1, "fecha": 1}
        ).sort("fecha", 1)
    )
    maximo = 0.0
    prs = 0
    for s in sesiones:
        vol = float(s.get("volumen_total") or 0)
        if vol > maximo and maximo > 0:
            prs += 1
        maximo = max(maximo, vol)
    return prs


def _max_asistencias_mes(mdb, id_miembro):
    """Mayor número de asistencias registradas en un mismo mes calendario, histórico."""
    fechas = mdb.asistencias.find({"id_miembro": id_miembro}, {"fecha": 1})
    por_mes = {}
    for a in fechas:
        f = a.get("fecha")
        if isinstance(f, str):
            f = datetime.strptime(f[:10], "%Y-%m-%d")
        if not isinstance(f, datetime):
            continue
        clave = (f.year, f.month)
        por_mes[clave] = por_mes.get(clave, 0) + 1
    return max(por_mes.values(), default=0)


def _recolectar_contexto(mdb, id_miembro):
    """
    Lee las métricas crudas de las que dependen todos los logros. Si algo
    falla aquí, ninguna medalla nueva se evalúa esta llamada (ver criterio
    de aceptación de "métricas en revisión" en el docstring del módulo).
    """
    return {
        "total_asistencias":     mdb.asistencias.count_documents({"id_miembro": id_miembro}),
        "racha":                 calcular_racha(mdb, id_miembro),
        "total_entrenamientos":  mdb.entrenamientos_realizados.count_documents({"id_miembro": id_miembro}),
        "max_asistencias_mes":   _max_asistencias_mes(mdb, id_miembro),
        "total_prs":             _contar_prs(mdb, id_miembro),
    }


def calcular_logros_miembro(mdb, id_miembro, id_gimnasio_pg=None):
    """
    Devuelve el catálogo completo de logros para un miembro, cada uno con su
    estado real: conseguido (con fecha persistida) o pendiente (con progreso
    actual/objetivo). Otorga automáticamente -- y persiste en
    `logros_miembro` -- cualquier logro recién cumplido.
    """
    ya_otorgados = {
        doc["tipo"]: doc
        for doc in mdb.logros_miembro.find({"id_miembro": id_miembro})
    }

    try:
        ctx = _recolectar_contexto(mdb, id_miembro)
        metricas_en_revision = False
    except Exception as e:
        print(f"Error recolectando contexto de logros: {e}")
        ctx = {}
        metricas_en_revision = True

    resultado = []
    for logro in ACHIEVEMENT_CATALOG:
        tipo = logro["id"]
        base = {
            "id":          tipo,
            "familia":     logro["familia"],
            "nombre":      logro["nombre"],
            "descripcion": logro["descripcion"],
            "icono":       logro["icono"],
            "nivel":       logro["nivel"],
            "color":       logro["color"],
            "objetivo":    logro["objetivo"],
        }

        otorgado = ya_otorgados.get(tipo)
        if otorgado:
            resultado.append({
                **base,
                "conseguido":      True,
                "fecha_obtencion": otorgado["fecha_obtencion"].strftime("%Y-%m-%d"),
                "progreso":        logro["objetivo"],
            })
            continue

        if metricas_en_revision:
            resultado.append({**base, "conseguido": False, "fecha_obtencion": None,
                               "progreso": None, "en_revision": True})
            continue

        progreso_actual = ctx.get(logro["metrica"], 0)
        conseguido_ahora = progreso_actual >= logro["objetivo"]

        if conseguido_ahora:
            ahora = datetime.now()
            # $setOnInsert: si dos requests concurrentes otorgan el mismo
            # logro, solo el primer insert fija la fecha -- idempotente.
            mdb.logros_miembro.update_one(
                {"id_miembro": id_miembro, "tipo": tipo},
                {"$setOnInsert": {
                    "id_miembro":      id_miembro,
                    "id_gimnasio_pg":  id_gimnasio_pg,
                    "tipo":            tipo,
                    "familia":         logro["familia"],
                    "fecha_obtencion": ahora,
                }},
                upsert=True,
            )
            resultado.append({**base, "conseguido": True,
                               "fecha_obtencion": ahora.strftime("%Y-%m-%d"),
                               "progreso": logro["objetivo"]})
        else:
            resultado.append({**base, "conseguido": False, "fecha_obtencion": None,
                               "progreso": min(progreso_actual, logro["objetivo"])})

    return resultado


def resumen_recientes(logros, limite=3):
    """
    Los N logros conseguidos más recientes, en el shape {icon,title,description,color}
    que ya consume el widget "Logros recientes" del dashboard (ver
    app/routes/miembro/user_dashboard.py) -- se mantiene compatible para no
    tener que tocar esa pantalla del home.
    """
    conseguidos = [l for l in logros if l["conseguido"]]
    conseguidos.sort(key=lambda l: l["fecha_obtencion"], reverse=True)
    return [{
        "icon":        l["icono"],
        "title":       l["nombre"],
        "description": l["descripcion"],
        "color":       l["color"],
    } for l in conseguidos[:limite]]
