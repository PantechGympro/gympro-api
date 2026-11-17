"""
routes/compartido/membresias_staff.py

Dos funciones de operación diaria para el personal del gimnasio:

  GET  /api/membresias/por-vencer          — Propietario, Administrador, Recepcionista
  GET  /api/checkins/manual/buscar        — Propietario, Administrador (lista paginada; ?q= filtra)
  POST /api/checkins/manual                — Propietario, Administrador

Decisiones de diseño
--------------------
* Aislamiento por gimnasio: todo se filtra con `g.tenant_id` (middleware de
  tenant); nunca se confía en un id de gimnasio enviado por el cliente.
* "Vigente" = existe una membresía en estado "Activa" cuya `fecha_fin` aún no
  pasó. Misma fuente de verdad que usa Recepción (`miembro_membresia`).
* El check-in manual bloquea a quien no tiene membresía vigente y responde con
  un `codigo` estable (`membresia_vencida`, `sin_membresia`) para que web y
  móvil muestren una alerta amable sin depender del texto.
* Las fechas se guardan en UTC (naive) y se devuelven tanto en ISO como ya
  formateadas en la hora local del gimnasio, para mostrar "día y hora exacta".
"""
import re
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from flask import Blueprint, g, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

from app.constants.roles import (
    ROL_ADMINISTRADOR, ROL_PROPIETARIO, ROL_RECEPCIONISTA, ROL_SUPERADMIN,
)
from app.mongo import get_db
from app.utils.busqueda import patron_insensible
from app.utils.security import require_role
from app.utils.tenant import require_tenant
from app.utils.timezone import APP_TZ, local_now_naive

membresias_staff_bp = Blueprint("membresias_staff", __name__)

# Quién puede ver vencimientos / quién puede registrar entradas a mano.
ROLES_VENCIMIENTOS = (ROL_PROPIETARIO, ROL_ADMINISTRADOR, ROL_RECEPCIONISTA, ROL_SUPERADMIN)
ROLES_CHECKIN_MANUAL = (ROL_PROPIETARIO, ROL_ADMINISTRADOR, ROL_SUPERADMIN)

DIAS_DEFECTO, DIAS_MAX = 7, 60
PER_PAGE_DEFECTO, PER_PAGE_MAX = 10, 50


# ── Helpers ──────────────────────────────────────────────────────────────────

from app.utils.membresia import (  # noqa: E402  (fuente única, ver utils/membresia.py)
    _fin_efectivo, a_utc as _a_utc, evaluar_membresia, evaluar_membresias_lote,
)


def _formato_local(dt_utc):
    """'16/11/2026 21:30' en la hora local del gimnasio."""
    return dt_utc.astimezone(APP_TZ).strftime("%d/%m/%Y %H:%M") if dt_utc else None


def _formato_vence(fin_utc):
    """
    Fecha de vencimiento para mostrar. Si se guardó solo el día (medianoche),
    se muestra solo la fecha: pasarla a hora local la mostraría un día antes
    ("19/11 18:00" para una membresía que vence el 20/11).
    """
    if fin_utc is None:
        return None
    local = fin_utc.astimezone(APP_TZ)
    solo_dia = (local.hour == local.minute == local.second == 0
                or fin_utc.hour == fin_utc.minute == fin_utc.second == 0)
    if solo_dia:
        base = fin_utc if fin_utc.hour == fin_utc.minute == fin_utc.second == 0 else local
        return base.strftime("%d/%m/%Y")
    return _formato_local(fin_utc)


def _estado(fecha_fin_utc, ahora_utc):
    """vencida | por_vencer (<= 7 días) | activa | sin_membresia."""
    if fecha_fin_utc is None:
        return "sin_membresia"
    if fecha_fin_utc < ahora_utc:
        return "vencida"
    return "por_vencer" if fecha_fin_utc - ahora_utc <= timedelta(days=7) else "activa"


def _nombre_tipo(mem):
    """Nombre del plan: el denormalizado o, si falta, el del catálogo."""
    tipo = mem.get("tipo_membresia")
    if not tipo and mem.get("id_membresia"):
        try:
            from app.models.pg.tipo_membresia import TipoMembresia
            tm = TipoMembresia.query.get(int(mem["id_membresia"]))
            tipo = tm.nombre if tm else None
        except Exception:
            tipo = None
    return tipo or "Membresía"


def _membresia_vigente(db, id_miembro):
    """
    Devuelve (documento_membresia | None, motivo | None).
    motivo ∈ {None, "sin_membresia", "membresia_vencida"}. Delegado en
    utils/membresia.py para que staff, recepción y la app del miembro
    apliquen exactamente la misma regla.
    """
    estado = evaluar_membresia(db, id_miembro)
    return estado["doc"], estado["codigo"]


# ── Membresías por vencer ────────────────────────────────────────────────────

@membresias_staff_bp.route("/api/membresias/por-vencer", methods=["GET"])
@jwt_required()
@require_tenant
@require_role(*ROLES_VENCIMIENTOS)
def membresias_por_vencer():
    """
    Miembros activos cuya membresía vence en los próximos `dias` (default 7,
    máx. 60). `incluir_vencidas=true` agrega las que vencieron en los últimos
    `dias`, útil para llamar a quien acaba de caer. Orden: vence primero, arriba.
    """
    db = get_db()
    try:
        dias = min(DIAS_MAX, max(1, int(request.args.get("dias", DIAS_DEFECTO))))
    except ValueError:
        dias = DIAS_DEFECTO
    incluir_vencidas = request.args.get("incluir_vencidas", "false").lower() == "true"

    ahora = datetime.now(timezone.utc)

    # Solo miembros activos de ESTE gimnasio (aislamiento de tenant).
    miembros = {
        m["_id"]: m for m in db.miembros.find(
            {"id_gimnasio_pg": g.tenant_id, "estado": "Activo"},
            {"nombre": 1, "email": 1, "telefono": 1, "id_usuario_pg": 1, "foto_perfil": 1},
        )
    }
    if not miembros:
        return jsonify({"miembros": [], "total": 0, "dias": dias}), 200

    # La vigencia se evalúa con la regla única (utils/membresia.py) y NO con un
    # rango en la consulta: `fecha_fin` está guardada en formatos mezclados
    # (datetime aware/naive y texto "YYYY-MM-DD" de los cobros en recepción y los
    # seeds). Un $gte/$lte con datetime nunca coincide con un texto, y por eso la
    # lista salía vacía aunque el padrón y el dashboard sí contaban esas
    # membresías. Además, con una renovación reciente solo cuenta la vigente más
    # larga, así que nadie aparece dos veces ni como "por vencer" si ya renovó.
    evaluaciones = evaluar_membresias_lote(db, list(miembros), ahora)

    filas = []
    for id_miembro, m in miembros.items():
        ev = evaluaciones.get(id_miembro) or {}
        fin = ev.get("fecha_fin")
        if fin is None:                      # sin membresía: no hay nada que vencer
            continue
        fin_ef = _fin_efectivo(fin)          # fecha "solo día" vale hasta el final de ese día
        segundos = (fin_ef - ahora).total_seconds()
        if ev.get("vigente"):
            if segundos > dias * 86400:
                continue
        elif not incluir_vencidas or segundos < -dias * 86400:
            continue
        filas.append((fin_ef, {
            "id":             str(m["_id"]),
            "id_usuario_pg":  m.get("id_usuario_pg"),
            "nombre":         m.get("nombre", ""),
            "email":          m.get("email", ""),
            "telefono":       m.get("telefono", ""),
            "foto_perfil":    m.get("foto_perfil"),
            "tipo_membresia": ev.get("nombre") or "Membresía",
            "fecha_fin":      fin.isoformat(),
            "fecha_fin_local": _formato_vence(fin),
            "dias_restantes": int(segundos // 86400),   # negativo si ya venció
            "horas_restantes": int(segundos // 3600),
            "estado":         "por_vencer" if ev.get("vigente") else "vencida",
        }))

    filas.sort(key=lambda par: par[0])           # vence primero, arriba
    filas = [f for _, f in filas]
    return jsonify({"miembros": filas, "total": len(filas), "dias": dias}), 200


# ── Check-in manual (Administrador / Propietario) ────────────────────────────

@membresias_staff_bp.route("/api/checkins/manual/buscar", methods=["GET"])
@jwt_required()
@require_tenant
@require_role(*ROLES_CHECKIN_MANUAL)
def buscar_para_checkin():
    """
    Lista de miembros para registrar entradas, con el estado de membresía ya
    calculado para que la pantalla avise ANTES de intentar el check-in.

    Query:
      q         (opcional) nombre, correo, teléfono o número de socio. Con 2+
                caracteres (o un número de socio) filtra; sin él devuelve el
                padrón completo del gimnasio, ordenado por nombre, para que la
                pantalla nunca se vea vacía.
      page      (int)  — default 1.
      per_page  (int)  — default 10, máximo 50.

    Respuesta: { miembros, total, page, pages, per_page }.
    """
    q = (request.args.get("q") or "").strip()
    page = max(1, request.args.get("page", 1, type=int) or 1)
    per_page = min(PER_PAGE_MAX, max(1, request.args.get("per_page", PER_PAGE_DEFECTO, type=int) or PER_PAGE_DEFECTO))

    filtro = {"id_gimnasio_pg": g.tenant_id, "estado": "Activo"}
    if len(q) >= 2 or q.isdigit():
        condiciones = [
            {"nombre":   {"$regex": patron_insensible(q), "$options": "i"}},
            {"email":    {"$regex": re.escape(q), "$options": "i"}},
            {"telefono": {"$regex": re.escape(q)}},
        ]
        if q.isdigit():
            condiciones.append({"id_usuario_pg": int(q)})
        filtro["$or"] = condiciones
    elif q:
        # Una sola letra no es una búsqueda útil: no devolvemos nada y la
        # pantalla pide escribir un poco más.
        return jsonify({"miembros": [], "total": 0, "page": 1, "pages": 0, "per_page": per_page}), 200

    db = get_db()
    total = db.miembros.count_documents(filtro)
    miembros = list(db.miembros.find(
        filtro,
        {"nombre": 1, "email": 1, "telefono": 1, "id_usuario_pg": 1, "foto_perfil": 1},
    ).sort([("nombre", 1), ("_id", 1)]).skip((page - 1) * per_page).limit(per_page))

    ahora = datetime.now(timezone.utc)
    estados = evaluar_membresias_lote(db, [m["_id"] for m in miembros], ahora)
    resultado = []
    for m in miembros:
        est = estados[m["_id"]]
        mem = est["doc"]
        fin = est["fecha_fin"]
        resultado.append({
            "id":              str(m["_id"]),
            "id_usuario_pg":   m.get("id_usuario_pg"),
            "nombre":          m.get("nombre", ""),
            "email":           m.get("email", ""),
            "telefono":        m.get("telefono", ""),
            "foto_perfil":     m.get("foto_perfil"),
            "tipo_membresia":  _nombre_tipo(mem) if mem else None,
            "estado":          est["estado"],   # misma regla que el guard del POST
            "fecha_fin_local": _formato_local(fin),
        })
    return jsonify({
        "miembros": resultado, "total": total, "page": page,
        "pages": -(-total // per_page), "per_page": per_page,
    }), 200


# Textos que verá la persona de staff (el `codigo` es el contrato estable).
_MENSAJES_BLOQUEO = {
    "sin_membresia": "{nombre} no tiene una membresía activa. Ofrécele contratar un plan para poder entrar.",
    "membresia_vencida": "La membresía de {nombre} venció el {fecha}. Sugiérele que la renueve para registrar su entrada.",
}


@membresias_staff_bp.route("/api/checkins/manual", methods=["POST"])
@jwt_required()
@require_tenant
@require_role(*ROLES_CHECKIN_MANUAL)
def checkin_manual():
    """
    Registra la entrada de un miembro. Body: { "id_miembro": "<ObjectId>" }.

    Reglas:
      400 — falta o es inválido el id.
      404 — el miembro no existe en este gimnasio (o está dado de baja).
      403 — sin membresía vigente (`codigo`: sin_membresia | membresia_vencida).
      409 — ya tiene una entrada abierta hoy (sin salida registrada).
      201 — entrada registrada.
    """
    db = get_db()
    gym_id = g.tenant_id
    cuerpo = request.get_json(silent=True) or {}

    try:
        id_miembro = ObjectId(str(cuerpo.get("id_miembro", "")))
    except Exception:
        return jsonify({"error": "Elige a un miembro de la lista para registrar su entrada."}), 400

    miembro = db.miembros.find_one(
        {"_id": id_miembro, "id_gimnasio_pg": gym_id, "estado": "Activo"})
    if not miembro:
        return jsonify({"error": "No encontramos a ese miembro en tu gimnasio."}), 404

    nombre = miembro.get("nombre", "El miembro")
    mem, motivo = _membresia_vigente(db, id_miembro)
    if motivo:
        fin = _a_utc(mem.get("fecha_fin")) if mem else None
        fecha = _formato_local(fin) or "—"
        return jsonify({
            "error":  _MENSAJES_BLOQUEO[motivo].format(nombre=nombre, fecha=fecha),
            "codigo": motivo,
            "fecha_fin_local": fecha if fin else None,
        }), 403

    ahora = local_now_naive()
    inicio_dia = datetime(ahora.year, ahora.month, ahora.day)
    abierta = db.asistencias.find_one({
        "id_miembro": id_miembro,
        "fecha": {"$gte": inicio_dia, "$lt": inicio_dia + timedelta(days=1)},
        "hora_salida": None,
    })
    if abierta:
        return jsonify({
            "error":  f"{nombre} ya registró su entrada hoy a las {abierta.get('hora_entrada', '')[:5]}.",
            "codigo": "checkin_duplicado",
        }), 409

    db.asistencias.insert_one({
        "id_miembro":   id_miembro,
        "fecha":        inicio_dia,
        "hora_entrada": ahora.strftime("%H:%M:%S"),
        "hora_salida":  None,
        "registrado_por": get_jwt_identity(),
        "origen":       "manual_staff",
        # Los mismos campos que filtran las métricas del gimnasio (ver el
        # historial del bug de asistencias invisibles en recepcionista_routes).
        "id_gimnasio":        gym_id,
        "id_gimnasio_visita": gym_id,
    })

    fin = _a_utc(mem.get("fecha_fin"))
    return jsonify({
        "message": f"¡Listo! Registramos la entrada de {nombre}.",
        "nombre":  nombre,
        "hora":    ahora.strftime("%H:%M"),
        "estado":  _estado(fin, datetime.now(timezone.utc)),
        "fecha_fin_local": _formato_local(fin),
    }), 201
