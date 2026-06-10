"""
miembro/training.py — Endpoints de entrenamiento para el miembro autenticado.

Colecciones MongoDB:
  pt_solicitudes      — solicitudes de entrenamiento personal
  mensajes_chat       — mensajes miembro ↔ entrenador
  alertas_entrenamiento — recordatorios de rutina del miembro

Todos los endpoints requieren JWT de rol Miembro/user y aislamiento por gimnasio.
"""
from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from bson.objectid import ObjectId
from datetime import datetime, timezone

from app.mongo import get_db
from app.utils.tenant import require_tenant
from app.models.pg.usuario import Usuario
from app.models.pg.ejercicio import Ejercicio

training_bp = Blueprint("member_training", __name__)


# ── helpers ────────────────────────────────────────────────────────────────────

def _pg_id() -> int:
    return int(get_jwt_identity())

def _gym_id() -> int:
    return g.tenant_id

def _oid(raw) -> ObjectId | None:
    try:
        return ObjectId(str(raw))
    except Exception:
        return None

def _ser(doc: dict) -> dict:
    """Serializa un documento Mongo: _id → id (str), fechas → ISO."""
    out = {}
    for k, v in doc.items():
        if k == "_id":
            out["id"] = str(v)
        elif isinstance(v, ObjectId):
            out[k] = str(v)
        elif isinstance(v, datetime):
            # MongoDB devuelve datetimes naive en UTC. Los marcamos como UTC
            # para que isoformat() incluya el offset (+00:00) y el frontend
            # los convierta correctamente a la zona horaria local del usuario.
            if v.tzinfo is None:
                v = v.replace(tzinfo=timezone.utc)
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRENADORES DISPONIBLES
# ══════════════════════════════════════════════════════════════════════════════

@training_bp.route("/trainers", methods=["GET"])
@jwt_required()
@require_tenant
def listar_entrenadores():
    """Lista los entrenadores activos del gimnasio para que el miembro pueda elegir."""
    from app.models.pg.rol import Rol
    gym_id = _gym_id()

    rol = Rol.query.filter_by(nombre="Entrenador").first()
    if not rol:
        return jsonify({"trainers": []}), 200

    trainers = Usuario.query.filter_by(
        id_rol=rol.id,
        id_gimnasio=gym_id,
        activo=True,
    ).all()

    return jsonify({
        "trainers": [
            {
                "id":           t.id,
                "nombre":       t.nombre,
                "email":        t.email,
                "especialidad": getattr(t, "especialidad", None) or "Entrenamiento General",
                "foto":         getattr(t, "foto_perfil", None),
            }
            for t in trainers
        ]
    }), 200


# ══════════════════════════════════════════════════════════════════════════════
#  SOLICITUDES DE ENTRENAMIENTO PERSONAL (PT)
# ══════════════════════════════════════════════════════════════════════════════

@training_bp.route("/pt-request", methods=["POST"])
@jwt_required()
@require_tenant
def crear_solicitud_pt():
    """
    Crea una solicitud de entrenamiento personal.
    Body: { id_entrenador_pg, notas, tipo_sesion }
    """
    db     = get_db()
    data   = request.get_json() or {}
    mid    = _pg_id()
    gym_id = _gym_id()

    id_ent = data.get("id_entrenador_pg")
    if not id_ent:
        return jsonify({"error": "id_entrenador_pg requerido"}), 400

    # Verificar que no haya ya una solicitud pendiente/activa con ese entrenador
    existente = db.pt_solicitudes.find_one({
        "id_miembro_pg":   mid,
        "id_entrenador_pg": int(id_ent),
        "id_gimnasio_pg":  gym_id,
        "estado":          {"$in": ["pendiente", "aceptada"]},
    })
    if existente:
        return jsonify({"error": "Ya tienes una solicitud activa con ese entrenador"}), 409

    # Resolver nombre del miembro desde PG
    usuario_pg = Usuario.query.get(mid)
    nombre_m   = usuario_pg.nombre if usuario_pg else "Miembro"

    # Resolver nombre del entrenador
    trainer_pg  = Usuario.query.get(int(id_ent))
    nombre_ent  = trainer_pg.nombre if trainer_pg else "Entrenador"

    doc = {
        "id_miembro_pg":    mid,
        "nombre_miembro":   nombre_m,
        "id_entrenador_pg": int(id_ent),
        "nombre_entrenador": nombre_ent,
        "id_gimnasio_pg":   gym_id,
        "estado":           "pendiente",           # pendiente | aceptada | rechazada
        "notas_miembro":    data.get("notas", ""),
        "tipo_sesion":      data.get("tipo_sesion", "individual"),
        "notas_entrenador": "",
        "fecha_solicitud":  datetime.now(timezone.utc),
        "fecha_respuesta":  None,
    }
    result = db.pt_solicitudes.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    return jsonify({"message": "Solicitud enviada", "solicitud": _ser(doc)}), 201


@training_bp.route("/pt-request", methods=["GET"])
@jwt_required()
@require_tenant
def mis_solicitudes_pt():
    """Retorna todas las solicitudes PT del miembro autenticado."""
    db     = get_db()
    mid    = _pg_id()
    gym_id = _gym_id()

    docs = list(db.pt_solicitudes.find(
        {"id_miembro_pg": mid, "id_gimnasio_pg": gym_id}
    ).sort("fecha_solicitud", -1))

    return jsonify({"solicitudes": [_ser(d) for d in docs]}), 200


@training_bp.route("/pt-request/<sol_id>", methods=["DELETE"])
@jwt_required()
@require_tenant
def cancelar_solicitud_pt(sol_id):
    """Cancela (elimina) una solicitud pendiente propia."""
    db  = get_db()
    mid = _pg_id()
    oid = _oid(sol_id)
    if not oid:
        return jsonify({"error": "ID inválido"}), 400

    res = db.pt_solicitudes.delete_one(
        {"_id": oid, "id_miembro_pg": mid, "estado": "pendiente"}
    )
    if res.deleted_count == 0:
        return jsonify({"error": "Solicitud no encontrada o no cancelable"}), 404
    return jsonify({"message": "Solicitud cancelada"}), 200


# ══════════════════════════════════════════════════════════════════════════════
#  CHAT MIEMBRO ↔ ENTRENADOR
# ══════════════════════════════════════════════════════════════════════════════

@training_bp.route("/chat/<int:trainer_id>", methods=["GET"])
@jwt_required()
@require_tenant
def chat_historial(trainer_id):
    """Devuelve los últimos 100 mensajes con un entrenador."""
    db     = get_db()
    mid    = _pg_id()
    gym_id = _gym_id()

    msgs = list(db.mensajes_chat.find({
        "id_miembro_pg":    mid,
        "id_entrenador_pg": trainer_id,
        "id_gimnasio_pg":   gym_id,
    }).sort("fecha", 1).limit(100))

    # Marcar como leídos (los del entrenador)
    db.mensajes_chat.update_many(
        {
            "id_miembro_pg":    mid,
            "id_entrenador_pg": trainer_id,
            "id_gimnasio_pg":   gym_id,
            "remitente":        "entrenador",
            "leido":            False,
        },
        {"$set": {"leido": True}},
    )

    return jsonify({"mensajes": [_ser(m) for m in msgs]}), 200


@training_bp.route("/chat/<int:trainer_id>", methods=["POST"])
@jwt_required()
@require_tenant
def chat_enviar(trainer_id):
    """Envía un mensaje al entrenador."""
    db     = get_db()
    data   = request.get_json() or {}
    mid    = _pg_id()
    gym_id = _gym_id()
    texto  = (data.get("texto") or "").strip()

    if not texto:
        return jsonify({"error": "Mensaje vacío"}), 400
    if len(texto) > 1000:
        return jsonify({"error": "Mensaje demasiado largo (máx 1000 chars)"}), 400

    doc = {
        "id_miembro_pg":    mid,
        "id_entrenador_pg": trainer_id,
        "id_gimnasio_pg":   gym_id,
        "remitente":        "miembro",
        "texto":            texto,
        "fecha":            datetime.now(timezone.utc),
        "leido":            False,
    }
    result = db.mensajes_chat.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    return jsonify({"mensaje": _ser(doc)}), 201


@training_bp.route("/chat/unread", methods=["GET"])
@jwt_required()
@require_tenant
def chat_no_leidos():
    """Conteo de mensajes no leídos del entrenador para badge."""
    db     = get_db()
    mid    = _pg_id()
    gym_id = _gym_id()

    count = db.mensajes_chat.count_documents({
        "id_miembro_pg":  mid,
        "id_gimnasio_pg": gym_id,
        "remitente":      "entrenador",
        "leido":          False,
    })
    return jsonify({"unread": count}), 200


# ══════════════════════════════════════════════════════════════════════════════
#  RUTINAS ASIGNADAS POR EL ENTRENADOR
# ══════════════════════════════════════════════════════════════════════════════

@training_bp.route("/assigned-routines", methods=["GET"])
@jwt_required()
@require_tenant
def rutinas_asignadas():
    """Rutinas que el entrenador asignó a este miembro."""
    db     = get_db()
    mid    = _pg_id()
    gym_id = _gym_id()

    docs = list(db.rutinas_asignadas.find(
        {"id_miembro_pg": mid, "id_gimnasio_pg": gym_id, "activa": True}
    ).sort("fecha_asignacion", -1))

    resultado = []
    for doc in docs:
        # Incluir días y ejercicios si la rutina referencia una del catálogo
        rutina_id = doc.get("id_rutina")
        dias = []
        if rutina_id:
            dias_docs = list(db.rutina_dias.find(
                {"id_rutina": rutina_id}
            ).sort("orden", 1))
            for d in dias_docs:
                ejercicios = list(db.rutina_ejercicios.find(
                    {"id_rutina_dia": d["_id"]}
                ).sort("orden", 1))
                # Enriquecer con catálogo PG (imagenes, video, instrucciones)
                _nombres = [e.get("nombre_ejercicio","").strip().lower() for e in ejercicios]
                _pg_map = {e.nombre.strip().lower(): e
                           for e in Ejercicio.query.filter_by(id_gimnasio=gym_id, activo=True).all()
                           if e.nombre.strip().lower() in _nombres} if _nombres else {}
                ej_list = []
                for e in ejercicios:
                    key = e.get("nombre_ejercicio","").strip().lower()
                    pg  = _pg_map.get(key)
                    imgs = [img for img in (e.get("imagenes") or []) if img]
                    if not imgs and pg: imgs = pg.imagenes or []
                    vid  = e.get("video") or (pg.video if pg else None)
                    inst = e.get("instrucciones") or (pg.descripcion if pg else "") or ""
                    ej_list.append({
                        "nombre":          e.get("nombre_ejercicio", ""),
                        "series":          e.get("series", "3"),
                        "reps":            e.get("repeticiones", "12"),
                        "peso":            e.get("peso", ""),
                        "notas":           e.get("notas", ""),
                        "instrucciones":   inst,
                        "imagenes":        imgs,
                        "video":           vid,
                    })
                dias.append({
                    "id":          str(d["_id"]),
                    "dia":         d.get("dia_semana", ""),
                    "grupo":       d.get("grupo_muscular", ""),
                    "ejercicios":  ej_list,
                })

        resultado.append({
            "id":               str(doc["_id"]),
            "nombre":           doc.get("nombre", "Rutina sin nombre"),
            "descripcion":      doc.get("descripcion", ""),
            "categoria":        doc.get("categoria", "General"),
            "dificultad":       doc.get("dificultad", "Intermedio"),
            "duracion_minutos": doc.get("duracion_minutos", 60),
            "nombre_entrenador":doc.get("nombre_entrenador", ""),
            "notas_entrenador": doc.get("notas_entrenador", ""),
            "fecha_asignacion": doc["fecha_asignacion"].isoformat()
                                if isinstance(doc.get("fecha_asignacion"), datetime)
                                else str(doc.get("fecha_asignacion", "")),
            "dias": dias,
        })

    return jsonify({"rutinas": resultado}), 200


# ══════════════════════════════════════════════════════════════════════════════
#  CALIFICACIÓN DE ENTRENADOR
# ══════════════════════════════════════════════════════════════════════════════

@training_bp.route("/trainer-rating", methods=["GET"])
@jwt_required()
@require_tenant
def get_mi_calificacion():
    """
    Devuelve la calificación que el miembro ya tiene registrada para su
    entrenador activo (solicitud aceptada), o null si aún no ha calificado.
    """
    db     = get_db()
    mid    = _pg_id()
    gym_id = _gym_id()

    solicitud = db.pt_solicitudes.find_one({
        "id_miembro_pg": mid,
        "id_gimnasio_pg": gym_id,
        "estado": "aceptada",
    })
    if not solicitud:
        return jsonify({"rating": None, "trainer_id": None, "trainer_name": None}), 200

    trainer_id = solicitud["id_entrenador_pg"]
    ev = db.evaluaciones_entrenador.find_one({
        "id_miembro_pg":    mid,
        "id_entrenador_pg": trainer_id,
    })

    return jsonify({
        "trainer_id":   trainer_id,
        "trainer_name": solicitud.get("nombre_entrenador", ""),
        "rating":       ev.get("calificacion") if ev else None,
        "comentario":   ev.get("comentario", "") if ev else "",
    }), 200


@training_bp.route("/trainer-rating", methods=["POST"])
@jwt_required()
@require_tenant
def calificar_entrenador():
    """
    El miembro califica a su entrenador activo (1–5 estrellas + comentario
    opcional). Si ya existe una evaluación, la actualiza (upsert).
    Body: { calificacion: int, comentario?: str }
    """
    db     = get_db()
    data   = request.get_json() or {}
    mid    = _pg_id()
    gym_id = _gym_id()

    calificacion = data.get("calificacion")
    try:
        calificacion = int(calificacion)
        if not (1 <= calificacion <= 5):
            raise ValueError()
    except (TypeError, ValueError):
        return jsonify({"error": "calificacion debe ser un entero entre 1 y 5"}), 400

    # Verificar que el miembro tenga una solicitud aceptada
    solicitud = db.pt_solicitudes.find_one({
        "id_miembro_pg": mid,
        "id_gimnasio_pg": gym_id,
        "estado": "aceptada",
    })
    if not solicitud:
        return jsonify({"error": "No tienes un entrenador asignado actualmente"}), 403

    trainer_id = solicitud["id_entrenador_pg"]

    db.evaluaciones_entrenador.update_one(
        {"id_miembro_pg": mid, "id_entrenador_pg": trainer_id},
        {
            "$set": {
                "calificacion":  calificacion,
                "comentario":    (data.get("comentario") or "").strip()[:500],
                "id_gimnasio_pg": gym_id,
                "nombre_miembro": solicitud.get("nombre_miembro", ""),
                "fecha":         datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )

    return jsonify({"message": "Calificación guardada correctamente", "calificacion": calificacion}), 200


# ══════════════════════════════════════════════════════════════════════════════
#  ALERTAS / RECORDATORIOS DE ENTRENAMIENTO
# ══════════════════════════════════════════════════════════════════════════════

@training_bp.route("/alerts", methods=["GET"])
@jwt_required()
@require_tenant
def listar_alertas():
    db     = get_db()
    mid    = _pg_id()
    gym_id = _gym_id()

    docs = list(db.alertas_entrenamiento.find(
        {"id_usuario_pg": mid, "id_gimnasio_pg": gym_id}
    ).sort("hora", 1))

    return jsonify({"alertas": [_ser(d) for d in docs]}), 200


@training_bp.route("/alerts", methods=["POST"])
@jwt_required()
@require_tenant
def crear_alerta():
    db     = get_db()
    data   = request.get_json() or {}
    mid    = _pg_id()
    gym_id = _gym_id()

    dias  = data.get("dias", [])
    hora  = data.get("hora", "07:00")
    titulo = (data.get("titulo") or "Entrenamiento").strip()[:80]

    if not dias:
        return jsonify({"error": "Selecciona al menos un día"}), 400
    if not hora:
        return jsonify({"error": "La hora es requerida"}), 400

    doc = {
        "id_usuario_pg":  mid,
        "id_gimnasio_pg": gym_id,
        "titulo":         titulo,
        "dias":           dias,           # ["lunes", "miercoles", ...]
        "hora":           hora,           # "07:00"
        "activa":         True,
        "tipo":           data.get("tipo", "rutina"),  # rutina | sesion_pt
        "fecha_creacion": datetime.now(timezone.utc),
    }
    result = db.alertas_entrenamiento.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    return jsonify({"alerta": _ser(doc)}), 201


@training_bp.route("/alerts/<alert_id>", methods=["PUT"])
@jwt_required()
@require_tenant
def actualizar_alerta(alert_id):
    db  = get_db()
    mid = _pg_id()
    oid = _oid(alert_id)
    if not oid:
        return jsonify({"error": "ID inválido"}), 400

    data = request.get_json() or {}
    upd  = {}
    if "titulo" in data:
        upd["titulo"] = str(data["titulo"])[:80]
    if "dias"   in data:
        upd["dias"]   = data["dias"]
    if "hora"   in data:
        upd["hora"]   = data["hora"]
    if "activa" in data:
        upd["activa"] = bool(data["activa"])

    if not upd:
        return jsonify({"error": "Sin campos a actualizar"}), 400

    res = db.alertas_entrenamiento.update_one(
        {"_id": oid, "id_usuario_pg": mid},
        {"$set": upd},
    )
    if res.matched_count == 0:
        return jsonify({"error": "Alerta no encontrada"}), 404
    return jsonify({"message": "Alerta actualizada"}), 200


@training_bp.route("/alerts/<alert_id>", methods=["DELETE"])
@jwt_required()
@require_tenant
def eliminar_alerta(alert_id):
    db  = get_db()
    mid = _pg_id()
    oid = _oid(alert_id)
    if not oid:
        return jsonify({"error": "ID inválido"}), 400

    res = db.alertas_entrenamiento.delete_one(
        {"_id": oid, "id_usuario_pg": mid}
    )
    if res.deleted_count == 0:
        return jsonify({"error": "Alerta no encontrada"}), 404
    return jsonify({"message": "Alerta eliminada"}), 200
