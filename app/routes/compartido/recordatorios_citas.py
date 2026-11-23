"""
compartido/recordatorios_citas.py -- Recordatorios de "Mis Citas" a 7, 3 y 1
día(s) antes de la fecha agendada.

Corre como tarea diaria de APScheduler (ver owner_gym/notifications.init_scheduler),
igual que el resto de los avisos automáticos (vencimiento de membresía,
asistencia motivacional, auto-renovación). Revisa `citas` (la misma colección
que ya llena recepción al agendar y, desde el fix de hoy, también el
entrenador al crear una sesión -- ver entrenador/trainer_routes.create_session),
nunca `sesiones` directamente, porque `citas` es la única vista que conoce
`client_id_pg` sin importar quién la haya creado.

Idempotente por (cita, horizonte): la clave de la notificación es
tipo=f"recordatorio_cita_{dias}d" + referencia_id=<id de la cita>, así que si
el job se reintenta el mismo día (o el proceso se reinicia) nunca duplica el
aviso, y si la cita se reagenda a otra fecha, sus recordatorios anteriores
quedan igual (no vuelven a dispararse para la fecha vieja) y los nuevos
cortes se calculan sobre la fecha nueva automáticamente.
"""
from datetime import timedelta

from app.mongo import get_db
from app.routes.compartido.notificaciones import crear_notificacion
from app.utils.timezone import local_now_naive

DIAS_AVISO = (7, 3, 1)

_ESTADOS_SIN_AVISO = ("cancelada", "completada")


def chequear_recordatorios_citas(app=None) -> dict:
    """Devuelve un resumen de la ejecución (para el endpoint manual y los tests)."""
    ctx = app.app_context() if app else None
    if ctx:
        ctx.push()

    try:
        db  = get_db()
        hoy = local_now_naive().date()

        revisadas = 0
        enviados  = 0

        for dias in DIAS_AVISO:
            objetivo = (hoy + timedelta(days=dias)).strftime("%Y-%m-%d")
            tipo_aviso = f"recordatorio_cita_{dias}d"

            citas = db.citas.find({
                "date":   objetivo,
                "status": {"$nin": _ESTADOS_SIN_AVISO},
                "client_id_pg": {"$ne": None},
            })

            for cita in citas:
                revisadas += 1
                cita_id = str(cita["_id"])

                ya_avisado = db.notificaciones.find_one({
                    "id_usuario_pg":  int(cita["client_id_pg"]),
                    "tipo":           tipo_aviso,
                    "referencia_id":  cita_id,
                })
                if ya_avisado:
                    continue

                faltan = "mañana" if dias == 1 else f"en {dias} días"
                crear_notificacion(
                    db=db,
                    id_usuario_pg=int(cita["client_id_pg"]),
                    id_gimnasio_pg=cita.get("id_gimnasio_pg"),
                    tipo=tipo_aviso,
                    titulo="Recordatorio de cita",
                    mensaje=(
                        f"Tienes una cita {faltan} ({cita.get('time', '')}). "
                        f"Tipo: {cita.get('type', '')}."
                        + (f" Con {cita['trainer']}." if cita.get("trainer") else "")
                    ),
                    referencia_tipo="cita",
                    referencia_id=cita_id,
                )
                enviados += 1

        return {
            "ejecutado_en":     local_now_naive().isoformat(),
            "citas_revisadas":  revisadas,
            "recordatorios_enviados": enviados,
        }
    finally:
        if ctx:
            ctx.pop()
