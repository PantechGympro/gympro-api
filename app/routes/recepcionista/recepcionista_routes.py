"""
routes/recepcionista/recepcionista_routes.py

Endpoints exclusivos del rol Recepcionista:
  GET  /api/recepcionista/dashboard   — KPIs del turno
  GET  /api/recepcionista/checkins    — check-ins de hoy
  POST /api/recepcionista/checkins    — registrar check-in por id_usuario_pg
  GET  /api/recepcionista/members     — lista de miembros con estado de membresía
  GET  /api/recepcionista/payments    — historial de pagos (solo lectura)
  GET  /api/recepcionista/citas       — citas del día / rango
  POST /api/recepcionista/citas       — crear cita (+ notificaciones + email)
  PATCH /api/recepcionista/citas/<id> — actualizar estado de cita
  DELETE /api/recepcionista/citas/<id>— eliminar cita
  GET  /api/recepcionista/trainers    — lista de entrenadores del gimnasio
"""

from flask import Blueprint, jsonify, request, g
from flask_jwt_extended import jwt_required, get_jwt
from datetime import datetime, timezone, timedelta
from bson import ObjectId

from app.mongo import get_db
from app.routes.compartido.notificaciones import crear_notificacion
from app.utils.tenant import require_tenant

recepcionista_bp = Blueprint("recepcionista", __name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _require_receptionist():
    """Verifica que el JWT pertenece a un Recepcionista o owner_gym (acceso amplio)."""
    role = get_jwt().get("role", "")
    allowed = {"Recepcionista", "recepcionista", "owner_gym", "admin", "superadmin"}
    if role not in allowed:
        return jsonify({"error": "Acceso no autorizado"}), 403
    return None


def _today_range():
    """Retorna (inicio, fin) del día LOCAL del gimnasio para filtrar asistencias de hoy."""
    from app.utils.timezone import local_today_bounds_naive
    return local_today_bounds_naive()


def _serialize(doc: dict) -> dict:
    """Convierte ObjectId y datetime a tipos JSON-serializables."""
    out = {}
    for k, v in doc.items():
        if isinstance(v, ObjectId):
            out[k] = str(v)
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard — KPIs del turno
# ─────────────────────────────────────────────────────────────────────────────

@recepcionista_bp.route("/dashboard", methods=["GET"])
@jwt_required()
@require_tenant
def get_dashboard():
    err = _require_receptionist()
    if err:
        return err

    db     = get_db()
    gym_id = g.tenant_id
    start, end = _today_range()

    # ── 1. Checkins de hoy ────────────────────────────────────────────────────
    # Obtener _ids de miembros del gimnasio
    member_ids = [
        m["_id"]
        for m in db.miembros.find(
            {"id_gimnasio_pg": gym_id, "estado": "Activo"},
            {"_id": 1},
        )
    ]
    today_checkins = db.asistencias.count_documents({
        "id_miembro": {"$in": member_ids},
        "fecha":      {"$gte": start, "$lt": end},
    })

    # ── 2. Miembros activos totales ───────────────────────────────────────────
    active_members = db.miembros.count_documents(
        {"id_gimnasio_pg": gym_id, "estado": "Activo"}
    )

    # ── 3. Pagos pendientes ───────────────────────────────────────────────────
    pending_payments = db.pagos.count_documents(
        {"id_gimnasio": gym_id, "estado": "pendiente"}
    )

    # ── 4. Membresías por vencer (próximos 7 días) ────────────────────────────
    now = datetime.now(timezone.utc)
    in_7 = now + timedelta(days=7)
    gym_member_oids = [
        m["_id"] for m in db.miembros.find({"id_gimnasio_pg": gym_id}, {"_id": 1})
    ]
    in_7_str = in_7.strftime("%Y-%m-%d")
    now_str  = now.strftime("%Y-%m-%d")
    expiring_soon = db.miembro_membresia.count_documents({
        "id_miembro": {"$in": gym_member_oids},
        "estado":     "Activa",
        "$or": [
            {"fecha_fin": {"$gte": now,     "$lte": in_7}},
            {"fecha_fin": {"$gte": now_str, "$lte": in_7_str}},
        ],
    })

    # ── 5. Citas hoy ──────────────────────────────────────────────────────────
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_citas = db.citas.count_documents(
        {"id_gimnasio_pg": gym_id, "date": today_str}
    )

    return jsonify({
        "today_checkins":  today_checkins,
        "active_members":  active_members,
        "pending_payments": pending_payments,
        "expiring_soon":   expiring_soon,
        "today_citas":     today_citas,
    }), 200


# ─────────────────────────────────────────────────────────────────────────────
# Check-ins
# ─────────────────────────────────────────────────────────────────────────────

@recepcionista_bp.route("/checkins", methods=["GET"])
@jwt_required()
@require_tenant
def get_checkins():
    """Devuelve los check-ins de hoy enriquecidos con datos del miembro."""
    err = _require_receptionist()
    if err:
        return err

    db     = get_db()
    gym_id = g.tenant_id
    start, end = _today_range()

    # Mapa id_miembro → doc miembro para enriquecer
    members_map = {
        m["_id"]: m
        for m in db.miembros.find(
            {"id_gimnasio_pg": gym_id},
            {"_id": 1, "nombre": 1, "email": 1, "id_usuario_pg": 1},
        )
    }

    asistencias = list(
        db.asistencias.find({
            "id_miembro": {"$in": list(members_map.keys())},
            "fecha":      {"$gte": start, "$lt": end},
        }).sort("hora_entrada", -1)
    )

    # Enriquecer con estado de membresía vigente
    now = datetime.now(timezone.utc)
    result = []
    for a in asistencias:
        m = members_map.get(a["id_miembro"], {})
        mem = db.miembro_membresia.find_one({
            "id_miembro": a["id_miembro"],
            "estado": "Activa",
        })
        mem_status = "Activa"
        if mem:
            fecha_fin = mem.get("fecha_fin")
            if fecha_fin:
                if isinstance(fecha_fin, str):
                    fecha_fin = datetime.fromisoformat(fecha_fin)
                if fecha_fin.tzinfo is None:
                    fecha_fin = fecha_fin.replace(tzinfo=timezone.utc)
                days_left = (fecha_fin - now).days
                if days_left < 0:
                    mem_status = "Vencida"
                elif days_left <= 7:
                    mem_status = "Por vencer"
        else:
            mem_status = "Sin membresía"

        result.append({
            "id":               str(a["_id"]),
            "nombre":           m.get("nombre", "Desconocido"),
            "hora_entrada":     a.get("hora_entrada", ""),
            "hora_salida":      a.get("hora_salida"),
            "membership_status": mem_status,
        })

    return jsonify({"checkins": result, "total": len(result)}), 200


@recepcionista_bp.route("/checkins", methods=["POST"])
@jwt_required()
@require_tenant
def register_checkin():
    """
    Registra un check-in para un miembro.
    Body: { "id_usuario_pg": int }
    """
    err = _require_receptionist()
    if err:
        return err

    db     = get_db()
    gym_id = g.tenant_id
    data   = request.get_json() or {}

    id_usuario_pg = data.get("id_usuario_pg")
    if not id_usuario_pg:
        return jsonify({"error": "id_usuario_pg requerido"}), 400

    miembro = db.miembros.find_one({
        "id_usuario_pg": int(id_usuario_pg),
        "id_gimnasio_pg": gym_id,
    })
    if not miembro:
        return jsonify({"error": "Miembro no encontrado en este gimnasio"}), 404

    from app.utils.timezone import local_now_naive
    start, end = _today_range()
    now = local_now_naive()

    # Evitar checkin duplicado en el mismo día
    existing = db.asistencias.find_one({
        "id_miembro": miembro["_id"],
        "fecha":      {"$gte": start, "$lt": end},
        "hora_salida": None,
    })
    if existing:
        return jsonify({"error": "El miembro ya tiene un check-in abierto hoy"}), 409

    db.asistencias.insert_one({
        "id_miembro":   miembro["_id"],
        "fecha":        datetime(now.year, now.month, now.day),
        "hora_entrada": now.strftime("%H:%M:%S"),
        "hora_salida":  None,
        "registrado_por": get_jwt().get("sub"),
    })

    return jsonify({
        "message": f"Check-in registrado para {miembro.get('nombre', '')}",
        "nombre":  miembro.get("nombre", ""),
    }), 201


# ─────────────────────────────────────────────────────────────────────────────
# Members — lista con estado de membresía
# ─────────────────────────────────────────────────────────────────────────────

@recepcionista_bp.route("/members", methods=["GET"])
@jwt_required()
@require_tenant
def get_members():
    err = _require_receptionist()
    if err:
        return err

    db     = get_db()
    gym_id = g.tenant_id
    search = request.args.get("q", "").strip()
    now    = datetime.now(timezone.utc)

    query = {"id_gimnasio_pg": gym_id, "estado": "Activo"}
    if search:
        query["$or"] = [
            {"nombre": {"$regex": search, "$options": "i"}},
            {"email":  {"$regex": search, "$options": "i"}},
        ]

    miembros = list(db.miembros.find(query, {
        "_id": 1, "nombre": 1, "email": 1, "telefono": 1,
        "id_usuario_pg": 1, "fecha_registro": 1,
    }).limit(100))

    result = []
    for m in miembros:
        mem = db.miembro_membresia.find_one({
            "id_miembro": m["_id"],
            "estado": "Activa",
        }, {"fecha_fin": 1, "tipo_membresia": 1, "id_membresia": 1})

        mem_status = "sin_membresia"
        fecha_fin  = None
        tipo       = None
        if mem:
            fecha_fin = mem.get("fecha_fin")
            tipo = mem.get("tipo_membresia", "")
            if not tipo and mem.get("id_membresia"):
                try:
                    from app.models.pg.tipo_membresia import TipoMembresia
                    tm_obj = TipoMembresia.query.get(int(mem["id_membresia"]))
                    if tm_obj: tipo = tm_obj.nombre
                except Exception:
                    pass
            if fecha_fin:
                if isinstance(fecha_fin, str):
                    fecha_fin = datetime.fromisoformat(fecha_fin)
                if fecha_fin.tzinfo is None:
                    fecha_fin = fecha_fin.replace(tzinfo=timezone.utc)
                days_left = (fecha_fin - now).days
                if days_left < 0:
                    mem_status = "vencida"
                elif days_left <= 7:
                    mem_status = "por_vencer"
                else:
                    mem_status = "activa"

        result.append({
            "id":             str(m["_id"]),
            "id_usuario_pg":  m.get("id_usuario_pg"),
            "nombre":         m.get("nombre", ""),
            "email":          m.get("email", ""),
            "telefono":       m.get("telefono", ""),
            "mem_status":     mem_status,
            "tipo_membresia": tipo,
            "fecha_fin":      fecha_fin.isoformat() if isinstance(fecha_fin, datetime) else fecha_fin,
        })

    return jsonify({"miembros": result, "total": len(result)}), 200


# ─────────────────────────────────────────────────────────────────────────────
# Payments — solo lectura
# ─────────────────────────────────────────────────────────────────────────────

@recepcionista_bp.route("/payments", methods=["GET"])
@jwt_required()
@require_tenant
def get_payments():
    err = _require_receptionist()
    if err:
        return err

    db     = get_db()
    gym_id = g.tenant_id
    status = request.args.get("estado")          # completado | pendiente | fallido
    q      = request.args.get("q", "").strip()   # búsqueda por concepto
    limit  = min(int(request.args.get("limit", 15)), 200)

    page  = max(1, int(request.args.get("page", 1)))
    skip  = (page - 1) * limit

    # Normalización de valores heredados del modelo (guarda "Pagado" en lugar de "completado")
    _STATUS_NORM = {
        "pagado":     "completado",
        "Pagado":     "completado",
        "completado": "completado",
        "pendiente":  "pendiente",
        "Pendiente":  "pendiente",
        "fallido":    "fallido",
        "Fallido":    "fallido",
    }
    # El filtro de estado debe buscar tanto el valor normalizado como el legacy
    _STATUS_REVERSE = {
        "completado": ["completado", "Pagado", "pagado"],
        "pendiente":  ["pendiente",  "Pendiente"],
        "fallido":    ["fallido",    "Fallido"],
    }

    query = {"id_gimnasio": gym_id}
    if status and status != "todos":
        variants = _STATUS_REVERSE.get(status, [status])
        query["estado"] = {"$in": variants} if len(variants) > 1 else variants[0]
    if q:
        # Buscar por concepto O por nombre de miembro (lookup inverso)
        miembro_ids = [
            m["_id"] for m in db.miembros.find(
                {"nombre": {"$regex": q, "$options": "i"}, "id_gimnasio_pg": gym_id},
                {"_id": 1},
            )
        ]
        q_conditions = [{"concepto": {"$regex": q, "$options": "i"}}]
        if miembro_ids:
            q_conditions.append({"id_miembro": {"$in": miembro_ids}})
        query["$or"] = q_conditions

    total_count = db.pagos.count_documents(query)

    pagos = list(
        db.pagos.find(query, {
            "_id": 1, "id_miembro": 1, "nombre_miembro": 1, "monto": 1,
            "fecha_pago": 1, "estado": 1, "concepto": 1, "metodo_pago": 1,
        }).sort("fecha_pago", -1).skip(skip).limit(limit)
    )

    # Batch-lookup: nombre_miembro no se desnormaliza al guardar, se resuelve aquí
    ids_sin_nombre = set()
    for p in pagos:
        if not (p.get("nombre_miembro") or "").strip() and p.get("id_miembro"):
            try:
                ids_sin_nombre.add(ObjectId(str(p["id_miembro"])))
            except Exception:
                pass

    nombre_cache: dict = {}
    if ids_sin_nombre:
        for m in db.miembros.find(
            {"_id": {"$in": list(ids_sin_nombre)}},
            {"nombre": 1, "apellido": 1},
        ):
            full = f"{m.get('nombre', '')} {m.get('apellido', '')}".strip()
            nombre_cache[str(m["_id"])] = full or "—"

    result = []
    for p in pagos:
        nombre = (p.get("nombre_miembro") or "").strip()
        if not nombre and p.get("id_miembro"):
            nombre = nombre_cache.get(str(p["id_miembro"]), "—")
        d = _serialize(p)
        d["nombre_miembro"] = nombre or "—"
        # Normalizar estado al valor canónico del frontend
        d["estado"] = _STATUS_NORM.get(d.get("estado", ""), d.get("estado", ""))
        result.append(d)

    return jsonify({
        "pagos":       result,
        "total":       total_count,
        "page":        page,
        "limit":       limit,
        "total_pages": max(1, -(-total_count // limit)),  # ceil division
    }), 200


# ─────────────────────────────────────────────────────────────────────────────
# Citas — CRUD
# ─────────────────────────────────────────────────────────────────────────────

@recepcionista_bp.route("/citas", methods=["GET"])
@jwt_required()
@require_tenant
def get_citas():
    """
    Retorna citas filtradas por fecha.
    Query params: date=YYYY-MM-DD (default: hoy)
    """
    err = _require_receptionist()
    if err:
        return err

    db     = get_db()
    gym_id = g.tenant_id
    date   = request.args.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    citas = list(
        db.citas.find(
            {"id_gimnasio_pg": gym_id, "date": date},
            {"_id": 1, "time": 1, "client": 1, "type": 1, "trainer": 1,
             "notes": 1, "status": 1, "date": 1},
        ).sort("time", 1)
    )

    return jsonify({"citas": [_serialize(c) for c in citas]}), 200


@recepcionista_bp.route("/citas", methods=["POST"])
@jwt_required()
@require_tenant
def create_cita():
    err = _require_receptionist()
    if err:
        return err

    db     = get_db()
    gym_id = g.tenant_id
    data   = request.get_json() or {}

    required = ["time", "client", "type", "date"]
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"Campo requerido: {field}"}), 400

    client_name    = data["client"].strip()
    trainer_name   = data.get("trainer", "Recepción").strip()
    client_id_pg   = data.get("client_id")   # id_usuario_pg si viene del combobox

    doc = {
        "id_gimnasio_pg":  gym_id,
        "date":            data["date"],
        "time":            data["time"],
        "client":          client_name,
        "client_id_pg":    int(client_id_pg) if client_id_pg else None,
        "type":            data["type"].strip(),
        "trainer":         trainer_name,
        "trainer_id_pg":   data.get("trainer_id"),   # opcional
        "notes":           (data.get("notes") or "").strip()[:500],
        "status":          "pendiente",
        "creado_en":       datetime.now(timezone.utc),
    }

    result = db.citas.insert_one(doc)
    doc["_id"] = result.inserted_id
    cita_id_str = str(result.inserted_id)

    # ── Buscar datos de contacto ─────────────────────────────────────────────
    # 1. Miembro: preferir client_id_pg; fallback por nombre
    miembro_doc = None
    if client_id_pg:
        miembro_doc = db.miembros.find_one(
            {"id_usuario_pg": int(client_id_pg), "id_gimnasio_pg": gym_id},
            {"nombre": 1, "email": 1, "id_usuario_pg": 1},
        )
    if not miembro_doc:
        miembro_doc = db.miembros.find_one(
            {"nombre": {"$regex": f"^{client_name}$", "$options": "i"}, "id_gimnasio_pg": gym_id},
            {"nombre": 1, "email": 1, "id_usuario_pg": 1},
        )

    # 2. Entrenador: buscar en PG por nombre
    trainer_pg = None
    if trainer_name and trainer_name.lower() not in ("recepción", "recepcion", ""):
        try:
            from app.models.pg.usuario import Usuario
            from app.models.pg.rol import Rol
            rol = Rol.query.filter(Rol.nombre.ilike("Entrenador")).first()
            if rol:
                trainer_pg = Usuario.query.filter(
                    Usuario.id_gimnasio == gym_id,
                    Usuario.id_rol == rol.id,
                    Usuario.activo == True,
                    Usuario.nombre.ilike(f"%{trainer_name}%"),
                ).first()
        except Exception:
            pass

    # ── Fecha/hora formateada para mensajes ──────────────────────────────────
    try:
        fecha_fmt = datetime.strptime(data["date"], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        fecha_fmt = data["date"]
    hora_fmt = data["time"]

    # ── Notificaciones in-app ────────────────────────────────────────────────
    if miembro_doc and miembro_doc.get("id_usuario_pg"):
        crear_notificacion(
            db=db,
            id_usuario_pg=miembro_doc["id_usuario_pg"],
            id_gimnasio_pg=gym_id,
            tipo="cita_nueva",
            titulo="Nueva cita agendada",
            mensaje=(
                f"Se agendó una cita para el {fecha_fmt} a las {hora_fmt}. "
                f"Tipo: {data['type']}."
                + (f" Responsable: {trainer_name}." if trainer_pg else "")
            ),
            referencia_tipo="cita",
            referencia_id=cita_id_str,
        )

    if trainer_pg:
        crear_notificacion(
            db=db,
            id_usuario_pg=trainer_pg.id,
            id_gimnasio_pg=gym_id,
            tipo="cita_nueva",
            titulo="Nueva cita asignada",
            mensaje=(
                f"Se te asignó una cita con {client_name} "
                f"el {fecha_fmt} a las {hora_fmt}. "
                f"Tipo: {data['type']}."
            ),
            referencia_tipo="cita",
            referencia_id=cita_id_str,
        )

    # ── Notificación de confirmación para el recepcionista ─────────────────
    rec_id = get_jwt().get("id")
    if rec_id:
        crear_notificacion(
            db=db,
            id_usuario_pg=int(rec_id),
            id_gimnasio_pg=gym_id,
            tipo="cita_nueva",
            titulo="Cita registrada",
            mensaje=(
                f"Cita agendada para {client_name} el {fecha_fmt} a las {hora_fmt}. "
                f"Tipo: {data['type']}. "
                + (f"Entrenador: {trainer_name}." if trainer_name else "")
            ),
            referencia_tipo="cita",
            referencia_id=cita_id_str,
        )

    # ── Emails ───────────────────────────────────────────────────────────────
    try:
        from flask_mail import Message
        from app.extensions import mail

        notas_html = f"<p style='color:#9AA4B8;font-size:13px;'><em>{data.get('notes','')}</em></p>" if data.get("notes") else ""

        def _base_email(titulo, saludo, cuerpo_html):
            return f"""
            <div style="font-family:sans-serif;max-width:580px;margin:0 auto;
                        background:#0f1117;color:#F3F5F9;padding:32px;border-radius:14px;
                        border:1px solid rgba(255,255,255,.08);">
              <div style="display:flex;align-items:center;gap:10px;margin-bottom:24px;">
                <div style="width:36px;height:36px;border-radius:8px;background:#3377FF;
                            display:flex;align-items:center;justify-content:center;
                            font-weight:800;font-size:16px;color:#fff;">G</div>
                <span style="font-weight:800;font-size:18px;letter-spacing:.5px;">GYM PRO</span>
              </div>
              <h2 style="color:#3377FF;margin:0 0 8px;">{titulo}</h2>
              <p style="margin:0 0 20px;color:#9AA4B8;">Hola <strong style="color:#F3F5F9;">{saludo}</strong>,</p>
              {cuerpo_html}
              <div style="margin-top:28px;padding:16px;background:#121520;border-radius:10px;
                          border:1px solid rgba(255,255,255,.07);">
                <p style="margin:0 0 6px;font-size:13px;color:#9AA4B8;">📅 Fecha</p>
                <p style="margin:0 0 12px;font-weight:700;font-size:15px;">{fecha_fmt}</p>
                <p style="margin:0 0 6px;font-size:13px;color:#9AA4B8;">🕐 Hora</p>
                <p style="margin:0 0 12px;font-weight:700;font-size:15px;">{hora_fmt}</p>
                <p style="margin:0 0 6px;font-size:13px;color:#9AA4B8;">📋 Tipo</p>
                <p style="margin:0;font-weight:700;font-size:15px;">{data['type']}</p>
              </div>
              {notas_html}
              <p style="margin-top:28px;font-size:11px;color:#555;">
                GymPro — Este mensaje es automático, por favor no respondas.
              </p>
            </div>"""

        # Email al miembro
        if miembro_doc and miembro_doc.get("email") and "@" in miembro_doc["email"]:
            cuerpo_miembro = f"""
            <p>Tu cita ha sido agendada exitosamente.</p>
            {'<p style="color:#9AA4B8;">Tu cita será atendida por <strong style="color:#F3F5F9;">' + trainer_name + '</strong>.</p>' if trainer_pg else ''}
            """
            msg_miembro = Message(
                subject=f"GymPro — Cita agendada para el {fecha_fmt}",
                recipients=[miembro_doc["email"]],
                html=_base_email("Cita confirmada", miembro_doc.get("nombre", client_name), cuerpo_miembro),
            )
            mail.send(msg_miembro)

        # Email al entrenador
        if trainer_pg and trainer_pg.email:
            cuerpo_trainer = f"""
            <p>Se te ha asignado una nueva cita.</p>
            <p style="color:#9AA4B8;">Cliente: <strong style="color:#F3F5F9;">{client_name}</strong></p>
            """
            msg_trainer = Message(
                subject=f"GymPro — Nueva cita asignada: {client_name} el {fecha_fmt}",
                recipients=[trainer_pg.email],
                html=_base_email("Nueva cita asignada", trainer_pg.nombre, cuerpo_trainer),
            )
            mail.send(msg_trainer)

    except Exception as mail_err:
        # Los emails son best-effort — no fallan la creación de la cita
        import logging
        logging.getLogger(__name__).warning("Email de cita no enviado: %s", mail_err)

    return jsonify({"message": "Cita creada", "cita": _serialize(doc)}), 201


@recepcionista_bp.route("/citas/<cita_id>", methods=["PATCH"])
@jwt_required()
@require_tenant
def update_cita(cita_id: str):
    err = _require_receptionist()
    if err:
        return err

    db     = get_db()
    gym_id = g.tenant_id
    data   = request.get_json() or {}

    try:
        oid = ObjectId(cita_id)
    except Exception:
        return jsonify({"error": "ID de cita inválido"}), 400

    allowed_updates = {"status", "time", "client", "type", "trainer", "notes", "date"}
    update_fields = {k: v for k, v in data.items() if k in allowed_updates}
    if not update_fields:
        return jsonify({"error": "Nada que actualizar"}), 400

    update_fields["actualizado_en"] = datetime.now(timezone.utc)

    res = db.citas.update_one(
        {"_id": oid, "id_gimnasio_pg": gym_id},
        {"$set": update_fields},
    )

    if res.matched_count == 0:
        return jsonify({"error": "Cita no encontrada"}), 404

    return jsonify({"message": "Cita actualizada"}), 200


@recepcionista_bp.route("/citas/<cita_id>", methods=["DELETE"])
@jwt_required()
@require_tenant
def delete_cita(cita_id: str):
    err = _require_receptionist()
    if err:
        return err

    db     = get_db()
    gym_id = g.tenant_id

    try:
        oid = ObjectId(cita_id)
    except Exception:
        return jsonify({"error": "ID inválido"}), 400

    res = db.citas.delete_one({"_id": oid, "id_gimnasio_pg": gym_id})
    if res.deleted_count == 0:
        return jsonify({"error": "Cita no encontrada"}), 404

    return jsonify({"message": "Cita eliminada"}), 200


# ─────────────────────────────────────────────────────────────────────────────
# Trainers — lista para autocompletar en citas
# ─────────────────────────────────────────────────────────────────────────────

@recepcionista_bp.route("/trainers", methods=["GET"])
@jwt_required()
@require_tenant
def get_trainers():
    """Devuelve lista de entrenadores activos del gimnasio para el selector de citas."""
    from app.extensions import db as pg_db
    from app.models.pg.usuario import Usuario
    from app.models.pg.rol import Rol

    err = _require_receptionist()
    if err:
        return err

    gym_id = g.tenant_id
    rol = Rol.query.filter(Rol.nombre.ilike("Entrenador")).first()
    if not rol:
        return jsonify({"trainers": []}), 200

    trainers = Usuario.query.filter_by(
        id_gimnasio=gym_id,
        id_rol=rol.id,
        activo=True,
    ).all()

    return jsonify({
        "trainers": [{"id": t.id, "nombre": t.nombre} for t in trainers]
    }), 200
