"""
utils/membresia.py -- Estado de la membresía de un miembro y guarda de servicios.

Fuente de verdad única para "¿este miembro puede usar el gimnasio hoy?", que
antes estaba repetida (check-in manual, panel de vencimientos, recepción) y
no se aplicaba en la API del miembro: con la membresía vencida la app móvil
seguía dejando registrar entrada, abrir rutinas y chatear.

Reglas
------
* Vigente  = existe una membresía en estado "Activa" cuya fecha_fin no pasó.
* Si la fecha_fin está guardada a medianoche (solo fecha), el miembro conserva
  el acceso TODO ese día: no se le corta a las 00:00 del último día pagado.
* Vencida  = tuvo membresía pero ninguna vigente. Sin membresía = nunca tuvo.
* Qué se bloquea (servicios del gimnasio): check-in por QR, rutinas y registro
  de entrenamientos, nutrición, chat con entrenador y solicitud de entrenador
  personal. Qué NO se bloquea: Membresía y Pagos (para renovar), perfil, salud
  y progreso corporal, logros, notificaciones y soporte: son datos del propio
  miembro y el camino para volver; bloquearlos solo castigaría.
* Los bloqueos responden 403 con un `codigo` estable (`membresia_vencida` /
  `sin_membresia`) y un mensaje empático listo para mostrar; web y móvil
  reaccionan al código, no al texto.
"""
import re
from datetime import datetime, timedelta, timezone

from flask import g, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, verify_jwt_in_request

from app.constants.roles import ROL_MIEMBRO, normalizar_rol
from app.mongo import get_db
from app.utils.timezone import APP_TZ

DIAS_POR_VENCER = 7
_MIN_UTC = datetime.min.replace(tzinfo=timezone.utc)


def a_utc(valor):
    """Normaliza fecha (datetime naive/aware o ISO str) a datetime aware UTC."""
    if valor is None:
        return None
    if isinstance(valor, str):
        try:
            valor = datetime.fromisoformat(valor)
        except ValueError:
            return None
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(timezone.utc)


def fecha_local(dt_utc, con_hora=False):
    """'16/11/2026' (o con hora) en la hora local del gimnasio."""
    if dt_utc is None:
        return None
    return dt_utc.astimezone(APP_TZ).strftime("%d/%m/%Y %H:%M" if con_hora else "%d/%m/%Y")


def _fin_efectivo(fin_utc):
    """Si la fecha_fin viene a medianoche (solo fecha), vale hasta el final de ese día."""
    if fin_utc is None:
        return None
    local = fin_utc.astimezone(APP_TZ)
    medianoche_local = local.hour == local.minute == local.second == 0
    medianoche_utc = fin_utc.hour == fin_utc.minute == fin_utc.second == 0
    return fin_utc + timedelta(days=1) if (medianoche_local or medianoche_utc) else fin_utc


def _nombre_plan(doc):
    nombre = doc.get("tipo_membresia")
    if not nombre and doc.get("id_membresia"):
        try:
            from app.models.pg.tipo_membresia import TipoMembresia
            tm = TipoMembresia.query.get(int(doc["id_membresia"]))
            nombre = tm.nombre if tm else None
        except Exception:
            nombre = None
    return nombre or "Membresía"


def evaluar_membresia(db, id_miembro, ahora=None):
    """
    Estado de la membresía de `id_miembro` (_id de Mongo del miembro).

    Devuelve {
      vigente: bool,
      estado:  'activa' | 'por_vencer' | 'vencida' | 'sin_membresia',
      codigo:  None | 'membresia_vencida' | 'sin_membresia',
      doc, fecha_fin (UTC), fecha_fin_local, dias_restantes (negativo si venció),
      vencio_hace_dias, nombre,
    }
    """
    ahora = ahora or datetime.now(timezone.utc)
    docs = list(db.miembro_membresia.find({"id_miembro": id_miembro}))
    if not docs:
        return {"vigente": False, "estado": "sin_membresia", "codigo": "sin_membresia", "doc": None,
                "fecha_fin": None, "fecha_fin_local": None, "dias_restantes": None,
                "vencio_hace_dias": None, "nombre": None}

    def _fin(d):
        return a_utc(d.get("fecha_fin")) or _MIN_UTC

    activas = [d for d in docs if d.get("estado") == "Activa"]
    vigentes = [d for d in activas if _fin(d) != _MIN_UTC and _fin_efectivo(_fin(d)) >= ahora]
    elegida = max(vigentes, key=_fin) if vigentes else max(docs, key=_fin)
    fin = a_utc(elegida.get("fecha_fin"))
    fin_ef = _fin_efectivo(fin) if fin else None
    segundos = (fin_ef - ahora).total_seconds() if fin_ef else None
    base = {
        "doc": elegida, "fecha_fin": fin, "fecha_fin_local": fecha_local(fin),
        "dias_restantes": int(segundos // 86400) if segundos is not None else None,
        "nombre": _nombre_plan(elegida),
    }
    if vigentes:
        por_vencer = segundos <= DIAS_POR_VENCER * 86400
        return {**base, "vigente": True, "estado": "por_vencer" if por_vencer else "activa",
                "codigo": None, "vencio_hace_dias": None}
    hace = max(0, int(-segundos // 86400)) if segundos is not None else None
    return {**base, "vigente": False, "estado": "vencida", "codigo": "membresia_vencida",
            "vencio_hace_dias": hace}


def mensaje_membresia(estado, para="miembro", nombre=None):
    """Texto empático por situación. `para` = 'miembro' | 'personal'."""
    codigo = estado["codigo"]
    fecha = estado.get("fecha_fin_local")
    if para == "personal":
        quien = nombre or "Esta persona"
        if codigo == "membresia_vencida":
            return (f"La membresía de {quien} venció el {fecha}. Para registrar su entrada, primero "
                    "cobra la renovación en Cobrar membresía; en cuanto quede pagada podrá entrar.")
        return (f"{quien} aún no tiene una membresía registrada. Cobra una en Cobrar membresía "
                "y después registra su entrada.")
    if codigo == "membresia_vencida":
        return (f"Tu membresía venció el {fecha}. Renuévala en Membresía y pagos para volver a "
                "registrar tu entrada, entrenar con tus rutinas y usar tu plan de nutrición. "
                "Tu progreso y tus datos siguen guardados.")
    return ("Aún no tienes una membresía activa. Elige un plan en Membresía y pagos para empezar "
            "a entrenar; aquí te esperamos.")


def respuesta_bloqueo(estado, para="miembro", nombre=None):
    msg = mensaje_membresia(estado, para, nombre)
    return jsonify({
        "error": msg, "msg": msg, "codigo": estado["codigo"], "renovar": True,
        "fecha_fin": estado["fecha_fin"].isoformat() if estado["fecha_fin"] else None,
        "fecha_fin_local": estado["fecha_fin_local"],
    }), 403


def estado_del_miembro_actual():
    """Estado de membresía del Miembro autenticado, o None si no aplica (otro rol / sin ficha)."""
    claims = get_jwt()
    if not claims or normalizar_rol(claims.get("role", "")) != ROL_MIEMBRO:
        return None
    gym_id = g.get("tenant_id")
    ident = get_jwt_identity()
    if not gym_id or ident is None:
        return None
    db = get_db()
    miembro = db.miembros.find_one({"id_usuario_pg": int(ident), "id_gimnasio_pg": gym_id}, {"_id": 1})
    if not miembro:
        return None
    return evaluar_membresia(db, miembro["_id"])


def proteger_con_membresia(bp, reglas=None):
    """
    Registra en el blueprint `bp` una guarda que responde 403 al MIEMBRO con
    membresía vencida o inexistente. `reglas` = lista de (regex_ruta, métodos|None)
    para proteger solo ciertas rutas; sin reglas se protege todo el blueprint
    (y las rutas futuras quedan protegidas sin acordarse de decorarlas).
    """
    compiladas = [(re.compile(rx), ms) for rx, ms in (reglas or [])]

    @bp.before_request
    def _guarda_membresia():
        if request.method == "OPTIONS":
            return None
        if compiladas and not any(
            rx.search(request.path) and (ms is None or request.method in ms) for rx, ms in compiladas
        ):
            return None
        try:
            verify_jwt_in_request(optional=True)
        except Exception:
            return None            # token inválido: que responda @jwt_required
        estado = estado_del_miembro_actual()
        if estado is None or estado["vigente"]:
            return None
        return respuesta_bloqueo(estado)
