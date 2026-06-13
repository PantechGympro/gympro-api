from flask import Blueprint, jsonify, request, g
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from bson.objectid import ObjectId
from datetime import datetime, date, timedelta, timezone
import traceback

from app.mongo import get_db
from app.extensions import db as pg_db
from app.models.pg.usuario import Usuario
from app.models.pg.ejercicio import Ejercicio
from app.utils.tenant import require_tenant

trainer_bp = Blueprint('trainer', __name__, url_prefix='/api/trainer')


# ═══════════════════════════════════════════════════════════════
#  RUTA — DASHBOARD RESUMEN
# ═══════════════════════════════════════════════════════════════

@trainer_bp.route('/dashboard', methods=['GET'])
@jwt_required()
@require_tenant
def get_trainer_dashboard():
    """Resumen ejecutivo para el landing del entrenador."""
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id

        today_start = datetime.combine(date.today(), datetime.min.time())
        today_end   = datetime.combine(date.today(), datetime.max.time())
        week_start  = datetime.combine(
            date.today() - timedelta(days=date.today().weekday()),
            datetime.min.time()
        )

        # Datos del entrenador desde PG
        usuario = Usuario.query.get(trainer_id)
        if not usuario:
            return jsonify({"error": "Entrenador no encontrado"}), 404

        # KPIs — clientes PT activos (miembros con solicitud aceptada, sin duplicados)
        total_clients = len(mdb.pt_solicitudes.distinct(
            "id_miembro_pg",
            {
                "id_entrenador_pg": trainer_id,
                "id_gimnasio_pg":   gym_id,
                "estado":           "aceptada",
            },
        ))
        sessions_today_list = list(mdb.sesiones.find({
            "id_entrenador_pg": trainer_id,
            "fecha":            {"$gte": today_start, "$lte": today_end},
        }).sort("hora_inicio", 1))
        sessions_week_total = mdb.sesiones.count_documents({
            "id_entrenador_pg": trainer_id,
            "id_gimnasio_pg":   gym_id,
            "fecha":            {"$gte": week_start},
        })
        sessions_week_done = mdb.sesiones.count_documents({
            "id_entrenador_pg": trainer_id,
            "id_gimnasio_pg":   gym_id,
            "fecha":            {"$gte": week_start},
            "estado":           "completed",
        })

        # Próximas sesiones programadas (sin contar hoy en curso)
        upcoming = list(mdb.sesiones.find({
            "id_entrenador_pg": trainer_id,
            "fecha":            {"$gt": datetime.now()},
            "estado":           {"$in": ["scheduled"]},
        }).sort("fecha", 1).limit(5))

        return jsonify({
            "trainer_name": usuario.nombre,
            "stats": {
                "total_clients":   total_clients,
                "sessions_today":  len(sessions_today_list),
                "sessions_week":   sessions_week_total,
                "completion_rate": round(sessions_week_done / sessions_week_total * 100)
                                   if sessions_week_total else 0,
            },
            "today_sessions":    [_sesion_to_dict(mdb, s) for s in sessions_today_list],
            "upcoming_sessions": [_sesion_to_dict(mdb, s) for s in upcoming],
        }), 200

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
#  RUTAS — CLIENTES DEL ENTRENADOR
# ═══════════════════════════════════════════════════════════════

@trainer_bp.route('/clients', methods=['GET'])
@jwt_required()
@require_tenant
def get_trainer_clients():
    try:
        mdb = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id = g.tenant_id

        page     = int(request.args.get('page', 1))
        per_page = 6
        skip     = (page - 1) * per_page
        search   = request.args.get('search', '')
        status   = request.args.get('status', 'all')

        inicio_mes = datetime.now().replace(day=1, hour=0, minute=0, second=0)

        # Auto-migrar: vincular en mdb.miembros todos los clientes con solicitud
        # aceptada que aún no tengan id_entrenador_pg seteado (fix retroactivo).
        accepted_pg_ids = mdb.pt_solicitudes.distinct(
            "id_miembro_pg",
            {"id_entrenador_pg": trainer_id, "id_gimnasio_pg": gym_id, "estado": "aceptada"},
        )
        if accepted_pg_ids:
            mdb.miembros.update_many(
                {
                    "id_usuario_pg":    {"$in": accepted_pg_ids},
                    "id_gimnasio_pg":   gym_id,
                    "id_entrenador_pg": {"$exists": False},
                },
                {"$set": {"id_entrenador_pg": trainer_id}},
            )

        # Filtro base: entrenador + gimnasio
        match_base = {
            "id_entrenador_pg": trainer_id,
            "id_gimnasio_pg":   gym_id,
        }
        if search:
            match_base["nombre"] = {"$regex": search, "$options": "i"}

        pipeline = [
            {"$match": match_base},
            {
                "$facet": {
                    "data": [
                        {"$skip": skip},
                        {"$limit": per_page},
                        # sesiones completadas del miembro
                        {
                            "$lookup": {
                                "from": "sesiones",
                                "let":  {"mid": "$_id"},
                                "pipeline": [
                                    {"$match": {"$expr": {"$and": [
                                        {"$eq": ["$id_miembro", "$$mid"]},
                                        {"$eq": ["$estado", "completed"]}
                                    ]}}},
                                    {"$count": "total"}
                                ],
                                "as": "sesiones_data"
                            }
                        },
                        # asistencias del mes
                        {
                            "$lookup": {
                                "from": "asistencias",
                                "let":  {"mid": "$_id"},
                                "pipeline": [
                                    {"$match": {"$expr": {"$and": [
                                        {"$eq": ["$id_miembro", "$$mid"]},
                                        {"$gte": ["$fecha", inicio_mes]}
                                    ]}}},
                                    {"$count": "total"}
                                ],
                                "as": "asistencias_mes_data"
                            }
                        },
                        {
                            "$addFields": {
                                "total_sesiones":  {"$ifNull": [{"$arrayElemAt": ["$sesiones_data.total", 0]}, 0]},
                                "asistencias_mes": {"$ifNull": [{"$arrayElemAt": ["$asistencias_mes_data.total", 0]}, 0]}
                            }
                        }
                    ],
                    "totalCount": [{"$count": "total"}]
                }
            }
        ]

        result = list(mdb.miembros.aggregate(pipeline))[0]
        data   = result["data"]
        total  = result["totalCount"][0]["total"] if result["totalCount"] else 0

        clients_data = []
        for r in data:
            miembro_id    = r["_id"]
            racha         = calcular_racha_dias(mdb, miembro_id)
            tasa          = calcular_tasa_asistencia(mdb, miembro_id)
            estado        = determinar_estado_cliente(None, tasa)

            clients_data.append({
                "id":           str(miembro_id),          # MongoDB ObjectId (para lookups internos)
                "pg_id":        r.get("id_usuario_pg"),   # PostgreSQL int (para asignaciones de dieta/rutina)
                # nombre denormalizado en el documento miembro (seed_mongo lo pobla)
                "name":         r.get("nombre", "Sin nombre"),
                "goal":         r.get("objetivo"),
                "sessionsTotal":r.get("total_sesiones", 0),
                "attendance":   tasa,
                "streak":       racha,
                "status":       estado
            })

        if status != "all":
            clients_data = [c for c in clients_data if c["status"] == status]

        return jsonify({
            "success": True,
            "clients": clients_data,
            "pagination": {
                "page":        page,
                "per_page":    per_page,
                "total":       total,
                "total_pages": (total + per_page - 1) // per_page
            }
        }), 200

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"success": False, "message": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
#  RUTAS — PERFIL DEL ENTRENADOR
# ═══════════════════════════════════════════════════════════════

@trainer_bp.route('/profile', methods=['GET'])
@jwt_required()
@require_tenant
def get_trainer_profile():
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id

        # Datos base desde PostgreSQL (fuente de verdad tras Sprint 2)
        usuario = Usuario.query.get(trainer_id)
        if not usuario:
            return jsonify({'success': False, 'message': 'Usuario no encontrado'}), 404

        # Perfil extendido + stats en Mongo
        perfil         = mdb.perfil_entrenador.find_one({"id_entrenador_pg": trainer_id})
        certificaciones= list(mdb.certificaciones_entrenador.find({"id_entrenador_pg": trainer_id}))
        logros         = list(mdb.logros_entrenador.find(
            {"id_entrenador_pg": trainer_id}
        ).sort("fecha", -1).limit(4))

        # Fuente de verdad: pt_solicitudes aceptadas (mdb.miembros puede estar desactualizado)
        total_clientes = len(mdb.pt_solicitudes.distinct(
            "id_miembro_pg",
            {"id_entrenador_pg": trainer_id, "id_gimnasio_pg": gym_id, "estado": "aceptada"},
        ))
        total_sesiones  = mdb.sesiones.count_documents(
            {"id_entrenador_pg": trainer_id, "estado": "completed"}
        )

        eval_pipeline = [
            {"$match": {"id_entrenador_pg": trainer_id}},
            {"$group": {"_id": None, "promedio": {"$avg": "$calificacion"}}}
        ]
        eval_result         = list(mdb.evaluaciones_entrenador.aggregate(eval_pipeline))
        calificacion_promedio = eval_result[0]["promedio"] if eval_result else 0

        # Antigüedad desde PG created_at
        fecha_creacion = usuario.created_at
        if fecha_creacion and fecha_creacion.tzinfo:
            fecha_creacion = fecha_creacion.replace(tzinfo=None)
        anos_activos = (datetime.now() - fecha_creacion).days // 365 if fecha_creacion else 0

        # Certificaciones: devolver como array estructurado
        certs_list = [
            {
                'id':          str(c['_id']),
                'nombre':      c.get('nombre', ''),
                'emisor':      c.get('emisor', ''),
                'anio':        c.get('anio', ''),
                'url_archivo': c.get('url_archivo', ''),
            }
            for c in certificaciones
        ]

        experience_custom = (perfil or {}).get("experiencia_texto", "")

        profile_data = {
            'name':           usuario.nombre,
            'email':          usuario.email,
            'phone':          perfil.get("telefono", "")        if perfil else "",
            'address':        perfil.get("direccion", "")       if perfil else "",
            'specialization': perfil.get("especializacion", "") if perfil else "",
            'experience':     experience_custom or f"{anos_activos} años",
            'certifications': certs_list,
            'bio':            perfil.get("biografia", "")       if perfil else "",
            'stats': {
                'totalClients':   total_clientes,
                'totalSessions':  total_sesiones,
                'totalEarnings':  0,   # reservado — se calculará desde pagos en sprint futuro
                'avgRating':      round(calificacion_promedio, 1),
                'yearsActive':    anos_activos,
                'certifications': len(certificaciones),
            },
            'achievements': [
                {
                    'title':       logro.get("titulo", ""),
                    'date':        logro.get("fecha").strftime('%B %Y')
                                   if isinstance(logro.get("fecha"), datetime)
                                   else str(logro.get("fecha", "")),
                    'description': logro.get("descripcion", "")
                }
                for logro in logros
            ]
        }
        return jsonify({'success': True, 'profile': profile_data}), 200

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500


@trainer_bp.route('/profile', methods=['PUT'])
@jwt_required()
@require_tenant
def update_trainer_profile():
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        data       = request.get_json() or {}

        # Actualizar nombre / email en PostgreSQL
        usuario = Usuario.query.get(trainer_id)
        if not usuario:
            return jsonify({'success': False, 'message': 'Usuario no encontrado'}), 404

        if 'name'  in data: usuario.nombre = data['name']
        if 'email' in data: usuario.email  = data['email']
        pg_db.session.commit()

        # Actualizar perfil extendido en Mongo
        update_perfil = {}
        if 'phone'          in data: update_perfil['telefono']          = data['phone']
        if 'address'        in data: update_perfil['direccion']         = data['address']
        if 'specialization' in data: update_perfil['especializacion']   = data['specialization']
        if 'bio'            in data: update_perfil['biografia']         = data['bio']
        if 'experience'     in data: update_perfil['experiencia_texto'] = data['experience']

        if update_perfil:
            mdb.perfil_entrenador.update_one(
                {"id_entrenador_pg": trainer_id},
                {"$set": update_perfil},
                upsert=True
            )

        # Certificaciones: array de objetos {nombre, emisor, anio, url_archivo}
        if 'certifications' in data:
            incoming = data['certifications']  # list of {id?, nombre, emisor, anio, url_archivo}
            # Remove all existing certs and replace
            mdb.certificaciones_entrenador.delete_many({"id_entrenador_pg": trainer_id})
            if incoming:
                mdb.certificaciones_entrenador.insert_many([
                    {
                        "id_entrenador_pg": trainer_id,
                        "id_gimnasio_pg":   g.tenant_id,
                        "nombre":           c.get("nombre", "").strip(),
                        "emisor":           c.get("emisor", "").strip(),
                        "anio":             str(c.get("anio", "")).strip(),
                        "url_archivo":      c.get("url_archivo", ""),
                    }
                    for c in incoming if c.get("nombre", "").strip()
                ])

        return jsonify({'success': True, 'message': 'Perfil actualizado correctamente'}), 200

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500


@trainer_bp.route('/profile/cert-upload', methods=['POST'])
@jwt_required()
@require_tenant
def upload_cert_file():
    """Sube el archivo de una certificación; devuelve la URL relativa."""
    import os, uuid
    from flask import current_app
    from werkzeug.utils import secure_filename

    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No se recibió archivo'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'success': False, 'message': 'Nombre de archivo vacío'}), 400

    ALLOWED = {'pdf', 'jpg', 'jpeg', 'png', 'webp'}
    ext = file.filename.rsplit('.', 1)[-1].lower()
    if ext not in ALLOWED:
        return jsonify({'success': False, 'message': f'Tipo no permitido: {ext}'}), 400

    trainer_id      = int(get_jwt_identity())
    unique_filename = f"cert_{trainer_id}_{uuid.uuid4().hex[:8]}.{ext}"
    upload_folder   = "/app/storage/uploads/certs"
    os.makedirs(upload_folder, exist_ok=True)
    file.save(os.path.join(upload_folder, unique_filename))

    url = f"/api/uploads/certs/{unique_filename}"
    return jsonify({'success': True, 'url': url}), 200


# ═══════════════════════════════════════════════════════════════
#  RUTAS — AGENDA Y SESIONES
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
#  RUTAS — DIETAS  →  movidas a diet_routes.py (diet_bp)
# ═══════════════════════════════════════════════════════════════


@trainer_bp.route('/schedule', methods=['GET'])
@jwt_required()
@require_tenant
def get_schedule():
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        week_offset= int(request.args.get('week_offset', 0))

        today              = date.today()
        start_of_week_date = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
        end_of_week_date   = start_of_week_date + timedelta(days=6)
        start_dt = datetime.combine(start_of_week_date, datetime.min.time())
        end_dt   = datetime.combine(end_of_week_date,   datetime.max.time())

        sessions = list(mdb.sesiones.find({
            "id_entrenador_pg": trainer_id,
            "fecha": {"$gte": start_dt, "$lte": end_dt}
        }).sort([("fecha", 1), ("hora_inicio", 1)]))

        schedule = {
            str(i): {
                "date":      (start_of_week_date + timedelta(days=i)).isoformat(),
                "day_name":  _nombre_dia(start_of_week_date + timedelta(days=i)),
                "day_number":(start_of_week_date + timedelta(days=i)).day,
                "is_today":  (start_of_week_date + timedelta(days=i)) == today,
                "sessions":  []
            }
            for i in range(7)
        }

        for s in sessions:
            fecha_s = s.get("fecha")
            if isinstance(fecha_s, str):
                fecha_s = datetime.strptime(fecha_s, '%Y-%m-%d').date()
            elif isinstance(fecha_s, datetime):
                fecha_s = fecha_s.date()
            day_index = (fecha_s - start_of_week_date).days
            if 0 <= day_index <= 6:
                schedule[str(day_index)]["sessions"].append(_sesion_to_dict(mdb, s))

        return jsonify({
            "week_start":    start_of_week_date.isoformat(),
            "week_end":      end_of_week_date.isoformat(),
            "schedule":      schedule,
            "total_sessions":len(sessions)
        }), 200

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@trainer_bp.route('/sessions', methods=['GET'])
@jwt_required()
@require_tenant
def get_sessions():
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        status_f   = request.args.get('status', 'all')
        date_range = request.args.get('range', 'week')
        page       = int(request.args.get('page', 1))
        per_page   = int(request.args.get('per_page', 20))

        today = datetime.combine(date.today(), datetime.min.time())
        query = {"id_entrenador_pg": trainer_id}

        if date_range == 'today':
            query["fecha"] = {"$gte": today, "$lt": today + timedelta(days=1)}
        elif date_range == 'week':
            start = today - timedelta(days=today.weekday())
            query["fecha"] = {"$gte": start, "$lt": start + timedelta(days=7)}
        elif date_range == 'month':
            start = today.replace(day=1)
            query["fecha"] = {"$gte": start}

        if status_f != 'all':
            query["estado"] = status_f

        total    = mdb.sesiones.count_documents(query)
        sessions = list(
            mdb.sesiones.find(query)
            .sort([("fecha", -1), ("hora_inicio", -1)])
            .skip((page - 1) * per_page)
            .limit(per_page)
        )

        all_sessions = list(mdb.sesiones.find({
            "id_entrenador_pg": trainer_id,
            "id_gimnasio_pg":   g.tenant_id,
        }))
        stats = _compute_stats(all_sessions)

        return jsonify({
            "sessions": [_sesion_to_dict(mdb, s) for s in sessions],
            "total":    total,
            "page":     page,
            "per_page": per_page,
            "stats":    stats
        }), 200

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@trainer_bp.route('/sessions', methods=['POST'])
@jwt_required()
@require_tenant
def create_session():
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        data       = request.get_json()

        for field in ['fecha', 'hora_inicio']:
            if field not in data:
                return jsonify({"error": f"Campo requerido: {field}"}), 400

        fecha_dt = datetime.strptime(data['fecha'], '%Y-%m-%d')

        nueva = {
            "id_entrenador_pg": trainer_id,
            "id_gimnasio_pg":   gym_id,
            "id_miembro":       ObjectId(data['id_miembro']) if data.get('id_miembro') else None,
            "fecha":            fecha_dt,
            "hora_inicio":      data['hora_inicio'],
            "duracion_minutos": int(data.get('duracion_minutos', 60)),
            "tipo":             data.get('tipo', 'Personal'),
            "ubicacion":        data.get('ubicacion', ''),
            "estado":           'scheduled',
            "nombre_sesion":    data.get('nombre_sesion', ''),
            "notas":            data.get('notas', ''),
            "num_ejercicios":   int(data.get('num_ejercicios', 0)),
            "asistencia":       False,
            "fecha_creacion":   datetime.now()
        }

        result = mdb.sesiones.insert_one(nueva)
        return jsonify({"message": "Sesión creada", "id_sesion": str(result.inserted_id)}), 201

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@trainer_bp.route('/sessions/<session_id>/status', methods=['PATCH'])
@jwt_required()
@require_tenant
def update_session_status(session_id):
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        data       = request.get_json() or {}
        new_status = data.get('status')
        valid      = ['scheduled', 'in-progress', 'completed', 'cancelled']

        if new_status not in valid:
            return jsonify({"error": f"Estado inválido. Opciones: {valid}"}), 400

        update_data = {"estado": new_status}
        if new_status == 'completed':
            update_data["asistencia"] = True

        result = mdb.sesiones.update_one(
            {"_id": ObjectId(session_id), "id_entrenador_pg": trainer_id},
            {"$set": update_data}
        )

        if result.matched_count == 0:
            return jsonify({"error": "Sesión no encontrada"}), 404

        return jsonify({"message": f"Estado actualizado a {new_status}"}), 200

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@trainer_bp.route('/members', methods=['GET'])
@jwt_required()
@require_tenant
def get_trainer_members():
    """
    Devuelve todos los miembros activos del gimnasio.
    - 'is_my_client': True si ya están asignados a este entrenador.
    - Nombres enriquecidos desde PostgreSQL (fuente de verdad).
    Query param opcional: ?my_clients=1  → solo los asignados al entrenador.
    """
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        only_mine  = request.args.get('my_clients', '0') == '1'

        query = {"id_gimnasio_pg": gym_id, "estado": "Activo"}
        if only_mine:
            query["id_entrenador_pg"] = trainer_id

        miembros = list(mdb.miembros.find(query))

        # Batch-enrich nombres desde PostgreSQL (evita stale names de Mongo)
        pg_ids = set()
        for m in miembros:
            uid = m.get("id_usuario_pg")
            if uid is not None:
                try:
                    pg_ids.add(int(uid))
                except (TypeError, ValueError):
                    pass

        user_map = {}
        if pg_ids:
            usuarios = Usuario.query.filter(Usuario.id.in_(pg_ids)).all()
            user_map = {u.id: u.nombre for u in usuarios}

        members = []
        for m in miembros:
            uid      = m.get("id_usuario_pg")
            pg_nombre = None
            if uid is not None:
                try:
                    pg_nombre = user_map.get(int(uid))
                except (TypeError, ValueError):
                    pass

            nombre = pg_nombre or m.get("nombre") or f"Miembro {m['_id']}"
            members.append({
                "id_miembro":    str(m["_id"]),
                "id_miembro_pg": int(uid) if uid is not None else None,
                "nombre":        nombre,
                "email":         m.get("email", ""),
                "is_my_client":  m.get("id_entrenador_pg") == trainer_id,
            })

        # Ordenar: primero los propios, luego el resto; ambos grupos por nombre
        members.sort(key=lambda x: (0 if x["is_my_client"] else 1, x["nombre"].lower()))

        return jsonify({"members": members}), 200

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
#  RUTAS — BIBLIOTECA DE RUTINAS
# ═══════════════════════════════════════════════════════════════

@trainer_bp.route('/routines', methods=['GET'])
@jwt_required()
@require_tenant
def get_routines():
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        category   = request.args.get('category', 'all')
        search     = request.args.get('search', '').strip()

        query = {"id_entrenador_pg": trainer_id}
        if category != 'all': query["categoria"] = category
        if search:            query["nombre"]    = {"$regex": search, "$options": "i"}

        routines = list(mdb.rutinas.find(query).sort("fecha_actualizacion", -1))
        result   = []

        for r in routines:
            clients_count  = mdb.miembro_rutina.count_documents({"id_rutina": r["_id"], "activa": True})
            dias           = list(mdb.rutina_dias.find({"id_rutina": r["_id"]}).sort("orden", 1))
            exercise_list  = []
            total_ejercicios = 0

            # Precargar catálogo PG para enriquecer media (1 query por rutina)
            _ejercicios_pg = {e.nombre.strip().lower(): e
                              for e in Ejercicio.query.filter_by(id_gimnasio=gym_id, activo=True).all()}

            for dia in dias:
                ejercicios = list(mdb.rutina_ejercicios.find({"id_rutina_dia": dia["_id"]}).sort("orden", 1))
                total_ejercicios += len(ejercicios)
                for ej in ejercicios:
                    nombre_ej = ej.get("nombre_ejercicio", "")
                    pg_ej = _ejercicios_pg.get(nombre_ej.strip().lower())
                    # Priorizar media del catálogo PG sobre lo guardado en Mongo
                    imagenes = [img for img in (ej.get("imagenes") or []) if img]
                    if not imagenes and pg_ej:
                        imagenes = pg_ej.imagenes or []
                    video = ej.get("video") or (pg_ej.video if pg_ej else None)
                    instrucciones = ej.get("instrucciones") or (pg_ej.descripcion if pg_ej else "") or ""
                    exercise_list.append({
                        'name':          nombre_ej,
                        'sets':          f"{ej.get('series', '')}x{ej.get('repeticiones', '')}",
                        'rest':          ej.get("notas") or '60s',
                        'day':           dia.get("dia_semana") or '',
                        'peso':          ej.get("peso") or '',
                        'imagenes':      imagenes,
                        'video':         video,
                        'instrucciones': instrucciones,
                    })

            result.append({
                'id':          str(r["_id"]),
                'name':        r.get("nombre", ""),
                'category':    r.get("categoria", "General"),
                'duration':    f"{r.get('duracion_minutos', 60)} min",
                'exercises':   total_ejercicios,
                'difficulty':  r.get("dificultad", "Intermedio"),
                'clients':     clients_count,
                'description': r.get("descripcion", ""),
                'active':      bool(r.get("activa", True)),
                'lastUsed':    _format_fecha(r.get("fecha_actualizacion")),
                'exerciseList':exercise_list
            })

        category_counts = {
            cat: mdb.rutinas.count_documents({"id_entrenador_pg": trainer_id, "categoria": cat})
            for cat in ['Fuerza', 'Hipertrofia', 'Cardio', 'Funcional', 'Movilidad']
        }

        return jsonify({
            'success':        True,
            'routines':       result,
            'total':          len(result),
            'categoryCounts': category_counts
        }), 200

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'success': False, 'message': str(e)}), 500


@trainer_bp.route('/routines', methods=['POST'])
@jwt_required()
@require_tenant
def create_routine():
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        data       = request.get_json()

        if not data or not data.get('name', '').strip():
            return jsonify({'success': False, 'message': 'El campo "name" es requerido'}), 400

        nueva_rutina = {
            "id_entrenador_pg": trainer_id,
            "id_gimnasio_pg":   gym_id,
            "id_miembro":       ObjectId(data['id_miembro']) if data.get('id_miembro') else None,
            "nombre":           data['name'].strip(),
            "categoria":        data.get('category', 'General'),
            "dificultad":       data.get('difficulty', 'Intermedio'),
            "duracion_minutos": int(data.get('duration_minutes', 60)),
            "descripcion":      data.get('description', ''),
            "objetivo":         data.get('objective', ''),
            "activa":           True,
            "fecha_creacion":   datetime.now(),
            "fecha_actualizacion": datetime.now()
        }
        rutina_id = mdb.rutinas.insert_one(nueva_rutina).inserted_id

        for order_d, day_data in enumerate(data.get('days', [])):
            nuevo_dia = {
                "id_rutina":      rutina_id,
                "dia_semana":     day_data.get('day'),
                "grupo_muscular": day_data.get('muscleGroup', ''),
                "orden":          order_d
            }
            dia_id = mdb.rutina_dias.insert_one(nuevo_dia).inserted_id

            ejercicios_insert = [
                {
                    "id_rutina_dia":    dia_id,
                    "nombre_ejercicio": ej.get('name', '').strip(),
                    "series":           str(ej.get('sets', '3')),
                    "repeticiones":     str(ej.get('reps', '12')),
                    "peso":             ej.get('peso', ''),
                    "notas":            ej.get('notes', ''),
                    "imagenes":         [img for img in ej.get('imagenes', []) if img][:3],
                    "video":            ej.get('video') or None,
                    "instrucciones":    ej.get('notes', '') or "",
                    "orden":            order_e
                }
                for order_e, ej in enumerate(day_data.get('exercises', []))
            ]
            if ejercicios_insert:
                mdb.rutina_ejercicios.insert_many(ejercicios_insert)

        # ── Auto-poblar la biblioteca de ejercicios (PostgreSQL) ──────────────
        # Garantiza que los ejercicios usados en la rutina (p. ej. importados
        # por IA) queden disponibles como ejercicios individuales reutilizables.
        try:
            existentes = {
                e.nombre.strip().lower(): e
                for e in Ejercicio.query.filter_by(
                    id_gimnasio=gym_id, id_entrenador=trainer_id
                ).all()
            }
            nuevos_ej = {}
            for day_data in data.get('days', []):
                grupo = day_data.get('muscleGroup', '') or None
                for ej in day_data.get('exercises', []):
                    nombre_ej = (ej.get('name', '') or '').strip()
                    if not nombre_ej:
                        continue
                    clave = nombre_ej.lower()
                    ya = existentes.get(clave)
                    if ya is not None:
                        if not ya.activo:   # reactivar si estaba soft-deleted
                            ya.activo = True
                        continue
                    if clave in nuevos_ej:
                        continue
                    sets = str(ej.get('sets', '')).strip()
                    nuevos_ej[clave] = Ejercicio(
                        id_gimnasio    = gym_id,
                        id_entrenador  = trainer_id,
                        nombre         = nombre_ej,
                        grupo_muscular = grupo,
                        tipo           = ej.get('tipo') or 'Fuerza',
                        series         = int(sets) if sets.isdigit() else None,
                        repeticiones   = str(ej.get('reps')) if ej.get('reps') else None,
                    )
            if nuevos_ej:
                pg_db.session.add_all(list(nuevos_ej.values()))
            pg_db.session.commit()
        except Exception:
            pg_db.session.rollback()
            print('[create_routine] No se pudo poblar la biblioteca de ejercicios:')
            print(traceback.format_exc())

        return jsonify({
            'success':   True,
            'id_rutina': str(rutina_id),
            'message':   'Rutina creada correctamente'
        }), 201

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'success': False, 'message': str(e)}), 500


@trainer_bp.route('/routines/<routine_id>', methods=['GET'])
@jwt_required()
@require_tenant
def get_routine_detail(routine_id):
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id

        rutina = mdb.rutinas.find_one({
            '_id':                ObjectId(routine_id),
            'id_entrenador_pg':   trainer_id,
            'id_gimnasio_pg':     gym_id
        })
        if not rutina:
            return jsonify({'success': False, 'message': 'Rutina no encontrada'}), 404

        dias = list(mdb.rutina_dias.find({'id_rutina': ObjectId(routine_id)}).sort('orden', 1))
        structured_days = []
        for dia in dias:
            ejercicios = list(mdb.rutina_ejercicios.find(
                {'id_rutina_dia': dia['_id']}
            ).sort('orden', 1))
            structured_days.append({
                'day':         dia.get('dia_semana', ''),
                'muscleGroup': dia.get('grupo_muscular', ''),
                'exercises': [
                    {
                        'name':     ej.get('nombre_ejercicio', ''),
                        'sets':     ej.get('series', '3'),
                        'reps':     ej.get('repeticiones', '12'),
                        'peso':     ej.get('peso', ''),
                        'notes':    ej.get('notas', ''),
                        'imagenes': ej.get('imagenes', []),
                        'video':    ej.get('video', ''),
                    }
                    for ej in ejercicios
                ]
            })

        return jsonify({
            'success': True,
            'routine': {
                'id':               str(rutina['_id']),
                'name':             rutina.get('nombre', ''),
                'category':         rutina.get('categoria', ''),
                'difficulty':       rutina.get('dificultad', ''),
                'duration_minutes': rutina.get('duracion_minutos', 60),
                'description':      rutina.get('descripcion', ''),
                'objective':        rutina.get('objetivo', ''),
                'days':             structured_days
            }
        }), 200

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'success': False, 'message': str(e)}), 500


@trainer_bp.route('/routines/<routine_id>', methods=['PUT'])
@jwt_required()
@require_tenant
def update_routine(routine_id):
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        data       = request.get_json()

        rutina = mdb.rutinas.find_one({
            '_id':              ObjectId(routine_id),
            'id_entrenador_pg': trainer_id,
            'id_gimnasio_pg':   gym_id
        })
        if not rutina:
            return jsonify({'success': False, 'message': 'Rutina no encontrada'}), 404

        mdb.rutinas.update_one(
            {'_id': ObjectId(routine_id)},
            {'$set': {
                'nombre':             data.get('name', rutina.get('nombre')).strip(),
                'categoria':          data.get('category', rutina.get('categoria')),
                'dificultad':         data.get('difficulty', rutina.get('dificultad')),
                'duracion_minutos':   int(data.get('duration_minutes', rutina.get('duracion_minutos', 60))),
                'descripcion':        data.get('description', rutina.get('descripcion', '')),
                'objetivo':           data.get('objective', rutina.get('objetivo', '')),
                'fecha_actualizacion': datetime.now()
            }}
        )

        # Delete old days + exercises, recreate from submitted data
        old_dias = list(mdb.rutina_dias.find({'id_rutina': ObjectId(routine_id)}, {'_id': 1}))
        old_dia_ids = [d['_id'] for d in old_dias]
        if old_dia_ids:
            mdb.rutina_ejercicios.delete_many({'id_rutina_dia': {'$in': old_dia_ids}})
        mdb.rutina_dias.delete_many({'id_rutina': ObjectId(routine_id)})

        for order_d, day_data in enumerate(data.get('days', [])):
            nuevo_dia = {
                'id_rutina':      ObjectId(routine_id),
                'dia_semana':     day_data.get('day'),
                'grupo_muscular': day_data.get('muscleGroup', ''),
                'orden':          order_d
            }
            dia_id = mdb.rutina_dias.insert_one(nuevo_dia).inserted_id

            ejercicios_insert = [
                {
                    'id_rutina_dia':    dia_id,
                    'nombre_ejercicio': ej.get('name', '').strip(),
                    'series':           str(ej.get('sets', '3')),
                    'repeticiones':     str(ej.get('reps', '12')),
                    'peso':             ej.get('peso', ''),
                    'notas':            ej.get('notes', ''),
                    'imagenes':         [img for img in ej.get('imagenes', []) if img][:3],
                    'video':            ej.get('video') or None,
                    'instrucciones':    ej.get('notes', '') or '',
                    'orden':            order_e
                }
                for order_e, ej in enumerate(day_data.get('exercises', []))
            ]
            if ejercicios_insert:
                mdb.rutina_ejercicios.insert_many(ejercicios_insert)

        return jsonify({'success': True, 'message': 'Rutina actualizada correctamente'}), 200

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'success': False, 'message': str(e)}), 500


@trainer_bp.route('/routines/<routine_id>', methods=['DELETE'])
@jwt_required()
@require_tenant
def delete_routine(routine_id):
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id

        rutina = mdb.rutinas.find_one({
            '_id':              ObjectId(routine_id),
            'id_entrenador_pg': trainer_id,
            'id_gimnasio_pg':   gym_id
        })
        if not rutina:
            return jsonify({'success': False, 'message': 'Rutina no encontrada'}), 404

        dias = list(mdb.rutina_dias.find({'id_rutina': ObjectId(routine_id)}, {'_id': 1}))
        dia_ids = [d['_id'] for d in dias]
        if dia_ids:
            mdb.rutina_ejercicios.delete_many({'id_rutina_dia': {'$in': dia_ids}})
        mdb.rutina_dias.delete_many({'id_rutina': ObjectId(routine_id)})
        mdb.rutinas.delete_one({'_id': ObjectId(routine_id)})

        return jsonify({'success': True, 'message': 'Rutina eliminada correctamente'}), 200

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'success': False, 'message': str(e)}), 500


@trainer_bp.route('/routines/<routine_id>/duplicate', methods=['POST'])
@jwt_required()
@require_tenant
def duplicate_routine(routine_id):
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id

        rutina = mdb.rutinas.find_one({
            '_id':              ObjectId(routine_id),
            'id_entrenador_pg': trainer_id,
            'id_gimnasio_pg':   gym_id
        })
        if not rutina:
            return jsonify({'success': False, 'message': 'Rutina no encontrada'}), 404

        nueva_rutina = {k: v for k, v in rutina.items() if k != '_id'}
        nueva_rutina['nombre']              = rutina.get('nombre', '') + ' (copia)'
        nueva_rutina['fecha_creacion']      = datetime.now()
        nueva_rutina['fecha_actualizacion'] = datetime.now()
        nueva_rutina['id_miembro']          = None
        new_rutina_id = mdb.rutinas.insert_one(nueva_rutina).inserted_id

        dias = list(mdb.rutina_dias.find({'id_rutina': ObjectId(routine_id)}).sort('orden', 1))
        for dia in dias:
            nuevo_dia = {k: v for k, v in dia.items() if k != '_id'}
            nuevo_dia['id_rutina'] = new_rutina_id
            new_dia_id = mdb.rutina_dias.insert_one(nuevo_dia).inserted_id

            ejercicios = list(mdb.rutina_ejercicios.find({'id_rutina_dia': dia['_id']}).sort('orden', 1))
            for ej in ejercicios:
                nuevo_ej = {k: v for k, v in ej.items() if k != '_id'}
                nuevo_ej['id_rutina_dia'] = new_dia_id
                mdb.rutina_ejercicios.insert_one(nuevo_ej)

        return jsonify({
            'success':   True,
            'id_rutina': str(new_rutina_id),
            'message':   'Rutina duplicada correctamente'
        }), 201

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'success': False, 'message': str(e)}), 500


@trainer_bp.route('/routines/<routine_id>/assign', methods=['POST'])
@jwt_required()
@require_tenant
def assign_routine_to_member(routine_id):
    try:
        mdb           = get_db()
        trainer_id    = int(get_jwt_identity())
        rutina_id_obj = ObjectId(routine_id)

        r = mdb.rutinas.find_one({"_id": rutina_id_obj, "id_entrenador_pg": trainer_id})
        if not r:
            return jsonify({'success': False, 'message': 'Rutina no encontrada'}), 404

        data          = request.get_json() or {}
        id_miembro_str= data.get('id_miembro')
        if not id_miembro_str:
            return jsonify({'success': False, 'message': 'id_miembro es requerido'}), 400

        id_miembro = ObjectId(id_miembro_str)
        miembro    = mdb.miembros.find_one({"_id": id_miembro, "id_entrenador_pg": trainer_id})
        if not miembro:
            return jsonify({'success': False, 'message': 'Miembro no encontrado'}), 404

        mdb.miembro_rutina.update_one(
            {"id_miembro": id_miembro, "id_rutina": rutina_id_obj},
            {"$set": {"fecha_asignacion": datetime.now(), "activa": True, "fecha_fin": None}},
            upsert=True
        )
        return jsonify({'success': True, 'message': 'Rutina asignada al miembro'}), 200

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'success': False, 'message': str(e)}), 500


# ═══════════════════════════════════════════════════════════════
#  BIBLIOTECA DE EJERCICIOS (catálogo PG por gimnasio)
# ═══════════════════════════════════════════════════════════════

@trainer_bp.route('/exercises', methods=['GET'])
@jwt_required()
@require_tenant
def get_exercises():
    """Lista los ejercicios activos creados por el entrenador autenticado."""
    gym_id      = g.tenant_id
    trainer_id  = int(get_jwt_identity())
    search      = request.args.get('search', '').strip()
    grupo       = request.args.get('grupo_muscular', '').strip()

    q = Ejercicio.query.filter_by(id_gimnasio=gym_id, id_entrenador=trainer_id, activo=True)
    if search:
        q = q.filter(Ejercicio.nombre.ilike(f'%{search}%'))
    if grupo:
        q = q.filter_by(grupo_muscular=grupo)

    exercises = q.order_by(Ejercicio.nombre).all()
    return jsonify({'exercises': [e.to_dict() for e in exercises]}), 200


@trainer_bp.route('/exercises', methods=['POST'])
@jwt_required()
@require_tenant
def create_exercise():
    """Crea un ejercicio en la biblioteca personal del entrenador."""
    gym_id      = g.tenant_id
    trainer_id  = int(get_jwt_identity())
    data        = request.get_json() or {}
    nombre      = (data.get('nombre') or '').strip()

    if not nombre:
        return jsonify({'error': 'El nombre es requerido'}), 400
    if Ejercicio.query.filter_by(id_gimnasio=gym_id, id_entrenador=trainer_id, nombre=nombre).first():
        return jsonify({'error': 'Ya tienes un ejercicio con ese nombre'}), 409

    raw_imgs = data.get('imagenes') or []
    ej = Ejercicio(
        id_gimnasio    = gym_id,
        id_entrenador  = trainer_id,
        nombre         = nombre,
        descripcion    = data.get('descripcion') or None,
        grupo_muscular = data.get('grupo_muscular') or None,
        tipo           = data.get('tipo') or None,
        series         = int(data['series']) if data.get('series') else None,
        repeticiones   = data.get('repeticiones') or None,
        duracion_min   = int(data['duracion_min']) if data.get('duracion_min') else None,
        imagenes       = [img for img in raw_imgs if img][:3] or None,
        video          = data.get('video') or None,
    )
    pg_db.session.add(ej)
    pg_db.session.commit()
    return jsonify(ej.to_dict()), 201


@trainer_bp.route('/exercises/<int:exercise_id>', methods=['PUT'])
@jwt_required()
@require_tenant
def update_exercise(exercise_id):
    """Actualiza un ejercicio de la biblioteca personal del entrenador."""
    gym_id     = g.tenant_id
    trainer_id = int(get_jwt_identity())
    ej         = Ejercicio.query.filter_by(
        id=exercise_id, id_gimnasio=gym_id, id_entrenador=trainer_id, activo=True
    ).first()
    if not ej:
        return jsonify({'error': 'Ejercicio no encontrado'}), 404

    data = request.get_json() or {}

    nuevo_nombre = (data.get('nombre') or '').strip()
    if nuevo_nombre and nuevo_nombre != ej.nombre:
        if Ejercicio.query.filter_by(id_gimnasio=gym_id, nombre=nuevo_nombre).first():
            return jsonify({'error': 'Ya existe un ejercicio con ese nombre'}), 409
        ej.nombre = nuevo_nombre

    if 'descripcion'    in data: ej.descripcion    = data['descripcion'] or None
    if 'grupo_muscular' in data: ej.grupo_muscular = data['grupo_muscular'] or None
    if 'tipo'           in data: ej.tipo           = data['tipo'] or None
    if 'series'         in data: ej.series         = int(data['series']) if data['series'] else None
    if 'repeticiones'   in data: ej.repeticiones   = data['repeticiones'] or None
    if 'duracion_min'   in data: ej.duracion_min   = int(data['duracion_min']) if data['duracion_min'] else None
    if 'imagenes'       in data:
        raw = data['imagenes'] or []
        ej.imagenes = [img for img in raw if img][:3] or None
    if 'video'          in data: ej.video          = data['video'] or None

    pg_db.session.commit()
    return jsonify(ej.to_dict()), 200


@trainer_bp.route('/exercises/<int:exercise_id>', methods=['DELETE'])
@jwt_required()
@require_tenant
def delete_exercise(exercise_id):
    """Soft-delete de un ejercicio de la biblioteca personal del entrenador."""
    gym_id     = g.tenant_id
    trainer_id = int(get_jwt_identity())
    ej         = Ejercicio.query.filter_by(
        id=exercise_id, id_gimnasio=gym_id, id_entrenador=trainer_id
    ).first()
    if not ej:
        return jsonify({'error': 'Ejercicio no encontrado'}), 404

    ej.activo = False
    pg_db.session.commit()
    return jsonify({'msg': 'Ejercicio eliminado'}), 200


# ═══════════════════════════════════════════════════════════════
#  REPORTES Y ESTADÍSTICAS
# ═══════════════════════════════════════════════════════════════

@trainer_bp.route('/reports', methods=['GET'])
@jwt_required()
@require_tenant
def get_reports():
    """
    Reportes completos del entrenador.
    Devuelve: stats, monthlyData (últimos 6 meses), sessionTypes,
              clientProgress (top 5), metrics detalladas.
    """
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        range_param= request.args.get('range', 'month')

        today = datetime.combine(date.today(), datetime.max.time())

        if range_param == 'week':
            start = datetime.combine(
                date.today() - timedelta(days=date.today().weekday()),
                datetime.min.time()
            )
        elif range_param == 'month':
            start = datetime.combine(
                date.today().replace(day=1), datetime.min.time()
            )
        elif range_param == 'quarter':
            month_start = ((date.today().month - 1) // 3) * 3 + 1
            start = datetime.combine(
                date.today().replace(month=month_start, day=1), datetime.min.time()
            )
        else:  # year
            start = datetime.combine(
                date.today().replace(month=1, day=1), datetime.min.time()
            )

        base_query = {"id_entrenador_pg": trainer_id, "fecha": {"$gte": start}}

        # ── KPIs del período ──────────────────────────────────────────────────
        total_sessions = mdb.sesiones.count_documents({
            **base_query, "estado": "completed"
        })
        total_scheduled = mdb.sesiones.count_documents(base_query)
        total_cancelled = mdb.sesiones.count_documents({
            **base_query, "estado": "cancelled"
        })
        total_clients = mdb.miembros.count_documents({
            "id_entrenador_pg": trainer_id,
            "id_gimnasio_pg":   gym_id,
            "estado":           "Activo",
        })

        # Período anterior para crecimiento
        period_len = today - start
        prev_start = start - period_len
        prev_sessions = mdb.sesiones.count_documents({
            "id_entrenador_pg": trainer_id,
            "fecha": {"$gte": prev_start, "$lt": start},
            "estado": "completed",
        })
        session_growth = _pct_growth(total_sessions, prev_sessions)

        # Calificación promedio
        eval_res = list(mdb.evaluaciones_entrenador.aggregate([
            {"$match": {"id_entrenador_pg": trainer_id}},
            {"$group": {"_id": None, "avg": {"$avg": "$calificacion"}}},
        ]))
        avg_rating = round(eval_res[0]["avg"], 1) if eval_res else 0

        # ── Evolución mensual (últimos 6 meses) ───────────────────────────────
        six_months_ago = datetime.combine(
            (date.today().replace(day=1) - timedelta(days=5 * 30)).replace(day=1),
            datetime.min.time()
        )
        monthly_raw = list(mdb.sesiones.aggregate([
            {"$match": {
                "id_entrenador_pg": trainer_id,
                "fecha": {"$gte": six_months_ago},
            }},
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-%m", "date": "$fecha"}},
                "completadas": {"$sum": {"$cond": [{"$eq": ["$estado", "completed"]}, 1, 0]}},
                "canceladas":  {"$sum": {"$cond": [{"$eq": ["$estado", "cancelled"]}, 1, 0]}},
                "total":       {"$sum": 1},
            }},
            {"$sort": {"_id": 1}},
        ]))

        _MES = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
        monthly_data = []
        for m in monthly_raw:
            yr, mo = m["_id"].split("-")
            monthly_data.append({
                "month":      f"{_MES[int(mo)-1]} {yr[-2:]}",
                "sessions":   m["completadas"],
                "cancelled":  m["canceladas"],
                "total":      m["total"],
            })

        # ── Tipos de sesión en el período ─────────────────────────────────────
        type_raw = list(mdb.sesiones.aggregate([
            {"$match": {**base_query, "estado": "completed"}},
            {"$group": {"_id": "$tipo", "count": {"$sum": 1}}},
        ]))
        session_types = [
            {"tipo": t.get("_id") or "Sin tipo", "count": t["count"]}
            for t in type_raw
        ]

        # ── Top 5 clientes por sesiones completadas ───────────────────────────
        top_raw = list(mdb.sesiones.aggregate([
            {"$match": {**base_query, "estado": "completed", "id_miembro": {"$ne": None}}},
            {"$group": {"_id": "$id_miembro", "sessions": {"$sum": 1}}},
            {"$sort": {"sessions": -1}},
            {"$limit": 5},
        ]))

        top_clients = []
        for c in top_raw:
            nombre = "Sin nombre"
            miembro = mdb.miembros.find_one({"_id": c["_id"]}, {"nombre": 1, "id_usuario_pg": 1})
            if miembro:
                uid = miembro.get("id_usuario_pg")
                if uid:
                    try:
                        u = Usuario.query.get(int(uid))
                        if u:
                            nombre = u.nombre
                    except Exception:
                        pass
                if nombre == "Sin nombre":
                    nombre = miembro.get("nombre", "Sin nombre")
            top_clients.append({"name": nombre, "sessions": c["sessions"]})

        max_sessions = max((c["sessions"] for c in top_clients), default=1)
        for c in top_clients:
            c["improvement"] = round(c["sessions"] / max_sessions * 100)

        # ── Métricas derivadas ─────────────────────────────────────────────────
        attendance_rate    = round(total_sessions / total_scheduled * 100) if total_scheduled else 0
        cancellation_rate  = round(total_cancelled / total_scheduled * 100) if total_scheduled else 0
        sessions_per_client= round(total_sessions / total_clients, 1) if total_clients else 0

        return jsonify({
            'success': True,
            'range':   range_param,
            'stats': {
                'revenue':   0,          # reservado para integración con pagos
                'sessions':  total_sessions,
                'clients':   total_clients,
                'avgRating': avg_rating,
                'growth': {
                    'sessions': session_growth,
                    'revenue':  0,
                    'clients':  0,
                },
            },
            'monthlyData':    monthly_data,
            'sessionTypes':   session_types,
            'clientProgress': top_clients,
            'metrics': {
                'attendanceRate':    attendance_rate,
                'cancellationRate':  cancellation_rate,
                'sessionsPerClient': sessions_per_client,
                'totalScheduled':    total_scheduled,
                'totalCancelled':    total_cancelled,
                'satisfaction':      avg_rating,
                'retentionRate':     0,   # requiere historial multi-período
                'newClients':        0,   # requiere campo fecha_asignacion en miembros
            },
        }), 200

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'success': False, 'message': str(e)}), 500


# ═══════════════════════════════════════════════════════════════
#  HELPERS PRIVADOS
# ═══════════════════════════════════════════════════════════════

_DIAS_ES = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']


def _nombre_dia(d: date) -> str:
    return _DIAS_ES[d.weekday()]


def _get_client_name(mdb, s: dict) -> str:
    """Obtiene el nombre del cliente de una sesión sin tocar la colección usuarios."""
    if s.get("nombre_sesion"):
        return s["nombre_sesion"]
    if s.get("id_miembro"):
        m = mdb.miembros.find_one({"_id": s["id_miembro"]})
        if m:
            return m.get("nombre", "Sin nombre")
    return "Cliente sin asignar"


def _sesion_to_dict(mdb, s: dict) -> dict:
    fecha     = s.get("fecha")
    fecha_str = fecha.isoformat() if isinstance(fecha, datetime) else str(fecha)
    hora      = s.get("hora_inicio")
    hora_str  = hora if isinstance(hora, str) else "00:00"

    return {
        "id_sesion":       str(s["_id"]),
        "date":            fecha_str.split('T')[0] if 'T' in fecha_str else fecha_str,
        "time":            hora_str,
        "client":          _get_client_name(mdb, s),
        "type":            s.get("tipo"),
        "duration":        f"{s.get('duracion_minutos', 60)} min",
        "duracion_minutos": s.get("duracion_minutos", 60),
        "location":        s.get("ubicacion") or "Sin ubicación",
        "status":          s.get("estado"),
        "notes":           s.get("notas") or "",
        "exercises":       s.get("num_ejercicios") or 0,
        "attendance":      bool(s.get("asistencia")),
        "nombre_sesion":   s.get("nombre_sesion") or "",
        "id_miembro":      str(s["id_miembro"]) if s.get("id_miembro") else None,
    }


def _compute_stats(sessions: list) -> dict:
    total = len(sessions)
    if total == 0:
        return {"total": 0, "completed": 0, "scheduled": 0,
                "cancelled": 0, "in_progress": 0, "attendance_rate": 0}
    completed   = sum(1 for s in sessions if s.get("estado") == 'completed')
    scheduled   = sum(1 for s in sessions if s.get("estado") == 'scheduled')
    cancelled   = sum(1 for s in sessions if s.get("estado") == 'cancelled')
    in_progress = sum(1 for s in sessions if s.get("estado") == 'in-progress')
    attended    = sum(1 for s in sessions if s.get("asistencia"))
    return {
        "total":           total,
        "completed":       completed,
        "scheduled":       scheduled,
        "cancelled":       cancelled,
        "in_progress":     in_progress,
        "attendance_rate": round((attended / total) * 100) if total else 0,
    }


def calcular_racha_dias(mdb, id_miembro):
    try:
        asistencias = list(mdb.asistencias.find({"id_miembro": id_miembro}).sort("fecha", -1))
        if not asistencias:
            return 0
        racha        = 0
        fecha_actual = datetime.now().date()
        for asistencia in asistencias:
            f_asist = asistencia.get("fecha")
            if isinstance(f_asist, datetime):
                f_asist = f_asist.date()
            if f_asist == fecha_actual or f_asist == fecha_actual - timedelta(days=racha):
                racha += 1
                fecha_actual = f_asist
            else:
                break
        return racha
    except Exception:
        return 0


def calcular_tasa_asistencia(mdb, id_miembro):
    try:
        fecha_inicio = datetime.now() - timedelta(days=30)
        programadas  = mdb.sesiones.count_documents({
            "id_miembro": id_miembro,
            "fecha":      {"$gte": fecha_inicio},
            "estado":     {"$in": ["completed", "cancelled"]}
        })
        if programadas == 0:
            return 0
        completadas = mdb.sesiones.count_documents({
            "id_miembro": id_miembro,
            "fecha":      {"$gte": fecha_inicio},
            "estado":     "completed"
        })
        return round((completadas / programadas) * 100)
    except Exception:
        return 0


def determinar_estado_cliente(ultima_sesion, tasa_asistencia):
    """Retorna 'active' | 'risk' | 'inactive' — alineado con filtros del frontend."""
    try:
        if tasa_asistencia == 0:
            return 'inactive'
        if tasa_asistencia < 70:
            return 'risk'
        return 'active'
    except Exception:
        return 'active'


def _ser_doc(doc: dict) -> dict:
    """Serializa ObjectId y datetime para JSON."""
    out = {}
    for k, v in doc.items():
        if k == "_id":
            out["id"] = str(v)
        elif isinstance(v, ObjectId):
            out[k] = str(v)
        elif isinstance(v, datetime):
            # Mongo devuelve datetimes naive en UTC; marcarlos como UTC para
            # que el frontend los convierta a la hora local correcta.
            if v.tzinfo is None:
                v = v.replace(tzinfo=timezone.utc)
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


# ═══════════════════════════════════════════════════════════════
#  SOLICITUDES DE ENTRENAMIENTO PERSONAL (PT) — VISTA ENTRENADOR
# ═══════════════════════════════════════════════════════════════

@trainer_bp.route('/pt-requests', methods=['GET'])
@jwt_required()
@require_tenant
def listar_solicitudes_pt():
    """Lista todas las solicitudes PT dirigidas a este entrenador."""
    mdb        = get_db()
    trainer_id = int(get_jwt_identity())
    gym_id     = g.tenant_id
    estado     = request.args.get('estado', 'all')   # pendiente | aceptada | rechazada | all

    filtro = {"id_entrenador_pg": trainer_id, "id_gimnasio_pg": gym_id}
    if estado != 'all':
        filtro["estado"] = estado

    docs = list(mdb.pt_solicitudes.find(filtro).sort("fecha_solicitud", -1))
    return jsonify({"solicitudes": [_ser_doc(d) for d in docs]}), 200


@trainer_bp.route('/pt-requests/<sol_id>', methods=['PATCH'])
@jwt_required()
@require_tenant
def responder_solicitud_pt(sol_id):
    """
    Acepta o rechaza una solicitud PT.
    Body: { accion: 'aceptar'|'rechazar', notas_entrenador: str }
    """
    mdb        = get_db()
    trainer_id = int(get_jwt_identity())
    data       = request.get_json() or {}
    accion     = data.get('accion')

    if accion not in ('aceptar', 'rechazar'):
        return jsonify({"error": "accion debe ser 'aceptar' o 'rechazar'"}), 400

    try:
        oid = ObjectId(sol_id)
    except Exception:
        return jsonify({"error": "ID inválido"}), 400

    nuevo_estado = "aceptada" if accion == "aceptar" else "rechazada"
    gym_id       = g.tenant_id

    # Recuperar la solicitud antes de actualizar para obtener id_miembro_pg
    solicitud = mdb.pt_solicitudes.find_one(
        {"_id": oid, "id_entrenador_pg": trainer_id, "estado": "pendiente"}
    )
    if not solicitud:
        return jsonify({"error": "Solicitud no encontrada o ya respondida"}), 404

    mdb.pt_solicitudes.update_one(
        {"_id": oid},
        {
            "$set": {
                "estado":           nuevo_estado,
                "notas_entrenador": data.get("notas_entrenador", ""),
                "fecha_respuesta":  datetime.now(timezone.utc),
            }
        },
    )

    # Si se acepta: vincular el miembro con el entrenador en mdb.miembros
    if accion == "aceptar":
        id_miembro_pg = solicitud.get("id_miembro_pg")
        if id_miembro_pg:
            mdb.miembros.update_one(
                {"id_usuario_pg": id_miembro_pg, "id_gimnasio_pg": gym_id},
                {"$set": {"id_entrenador_pg": trainer_id}},
            )

    return jsonify({"message": f"Solicitud {nuevo_estado}"}), 200


# ═══════════════════════════════════════════════════════════════
#  CHAT ENTRENADOR ↔ MIEMBRO
# ═══════════════════════════════════════════════════════════════

@trainer_bp.route('/chat/<int:miembro_pg_id>', methods=['GET'])
@jwt_required()
@require_tenant
def chat_trainer_historial(miembro_pg_id):
    """Historial de mensajes con un miembro específico."""
    mdb        = get_db()
    trainer_id = int(get_jwt_identity())
    gym_id     = g.tenant_id

    msgs = list(mdb.mensajes_chat.find({
        "id_miembro_pg":    miembro_pg_id,
        "id_entrenador_pg": trainer_id,
        "id_gimnasio_pg":   gym_id,
    }).sort("fecha", 1).limit(100))

    # Marcar mensajes del miembro como leídos
    mdb.mensajes_chat.update_many(
        {
            "id_miembro_pg":    miembro_pg_id,
            "id_entrenador_pg": trainer_id,
            "id_gimnasio_pg":   gym_id,
            "remitente":        "miembro",
            "leido":            False,
        },
        {"$set": {"leido": True}},
    )

    return jsonify({"mensajes": [_ser_doc(m) for m in msgs]}), 200


@trainer_bp.route('/chat/<int:miembro_pg_id>', methods=['POST'])
@jwt_required()
@require_tenant
def chat_trainer_enviar(miembro_pg_id):
    """Envía un mensaje al miembro."""
    mdb        = get_db()
    trainer_id = int(get_jwt_identity())
    gym_id     = g.tenant_id
    data       = request.get_json() or {}
    texto      = (data.get("texto") or "").strip()

    if not texto:
        return jsonify({"error": "Mensaje vacío"}), 400
    if len(texto) > 1000:
        return jsonify({"error": "Mensaje demasiado largo"}), 400

    doc = {
        "id_miembro_pg":    miembro_pg_id,
        "id_entrenador_pg": trainer_id,
        "id_gimnasio_pg":   gym_id,
        "remitente":        "entrenador",
        "texto":            texto,
        "fecha":            datetime.now(timezone.utc),
        "leido":            False,
    }
    result = mdb.mensajes_chat.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    return jsonify({"mensaje": _ser_doc(doc)}), 201


@trainer_bp.route('/chat/unread-summary', methods=['GET'])
@jwt_required()
@require_tenant
def chat_unread_summary():
    """Badge: total mensajes sin leer + lista de miembros con mensajes nuevos."""
    mdb        = get_db()
    trainer_id = int(get_jwt_identity())
    gym_id     = g.tenant_id

    pipeline = [
        {
            "$match": {
                "id_entrenador_pg": trainer_id,
                "id_gimnasio_pg":   gym_id,
                "remitente":        "miembro",
                "leido":            False,
            }
        },
        {
            "$group": {
                "_id":   "$id_miembro_pg",
                "count": {"$sum": 1},
            }
        },
    ]
    result = list(mdb.mensajes_chat.aggregate(pipeline))
    total  = sum(r["count"] for r in result)

    # Enriquecer con nombre del miembro
    miembro_ids = [r["_id"] for r in result]
    usuarios    = {u.id: u.nombre for u in Usuario.query.filter(Usuario.id.in_(miembro_ids)).all()} if miembro_ids else {}

    resumen = [
        {
            "id_miembro_pg": r["_id"],
            "nombre":        usuarios.get(r["_id"], f"Miembro #{r['_id']}"),
            "unread":        r["count"],
        }
        for r in result
    ]

    return jsonify({"total_unread": total, "por_miembro": resumen}), 200


# ═══════════════════════════════════════════════════════════════
#  ASIGNAR RUTINA DEL CATÁLOGO A UN MIEMBRO (vista enriquecida)
# ═══════════════════════════════════════════════════════════════

@trainer_bp.route('/assign-routine', methods=['POST'])
@jwt_required()
@require_tenant
def asignar_rutina_miembro():
    """
    Asigna una rutina del catálogo del entrenador a un miembro específico
    y la registra en rutinas_asignadas (visible para el miembro).
    Body: { id_rutina, id_miembro_pg, notas_entrenador? }
    """
    mdb        = get_db()
    trainer_id = int(get_jwt_identity())
    gym_id     = g.tenant_id
    data       = request.get_json() or {}

    id_rutina_str  = data.get("id_rutina")
    id_miembro_pg  = data.get("id_miembro_pg")

    if not id_rutina_str or not id_miembro_pg:
        return jsonify({"error": "id_rutina e id_miembro_pg son requeridos"}), 400

    try:
        rutina_oid = ObjectId(id_rutina_str)
    except Exception:
        return jsonify({"error": "id_rutina inválido"}), 400

    rutina = mdb.rutinas.find_one({"_id": rutina_oid, "id_entrenador_pg": trainer_id})
    if not rutina:
        return jsonify({"error": "Rutina no encontrada en tu catálogo"}), 404

    # Resolver nombre del entrenador
    trainer_pg  = Usuario.query.get(trainer_id)
    nombre_ent  = trainer_pg.nombre if trainer_pg else "Entrenador"

    doc = {
        "id_miembro_pg":    int(id_miembro_pg),
        "id_entrenador_pg": trainer_id,
        "id_gimnasio_pg":   gym_id,
        "id_rutina":        rutina_oid,
        "nombre":           rutina.get("nombre", ""),
        "descripcion":      rutina.get("descripcion", ""),
        "categoria":        rutina.get("categoria", "General"),
        "dificultad":       rutina.get("dificultad", "Intermedio"),
        "duracion_minutos": rutina.get("duracion_minutos", 60),
        "nombre_entrenador":nombre_ent,
        "notas_entrenador": data.get("notas_entrenador", ""),
        "activa":           True,
        "fecha_asignacion": datetime.now(timezone.utc),
    }
    result = mdb.rutinas_asignadas.insert_one(doc)

    return jsonify({
        "message":    "Rutina asignada al miembro",
        "id":         str(result.inserted_id),
    }), 201


def _pct_growth(current, previous):
    if previous == 0:
        return 100 if current > 0 else 0
    return round(((current - previous) / previous) * 100)


def _format_fecha(ts):
    try:
        if not ts:
            return 'Nunca'
        if isinstance(ts, str):
            ts = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
        diff = datetime.now() - ts
        days = diff.days
        if days == 0:
            return 'Hoy'
        elif days == 1:
            return 'Ayer'
        elif days < 7:
            return f'Hace {days} días'
        else:
            return ts.strftime('%d/%m/%Y')
    except Exception:
        return '-'


# ═════════════════════════════════════════════════════════════════════════════
#  AI ETL — Importar rutinas y ejercicios desde PDF / Excel (Ollama)
#
#  Casos de uso:
#    1. Entrenador migra su biblioteca de rutinas desde otro sistema
#    2. Cliente sube historial de entrenamiento de un gimnasio anterior
#
#  Flujo:
#    Extract  — pdfplumber / openpyxl → texto crudo del documento
#    Transform — Ollama LLM local extrae estructura de rutinas y ejercicios
#    Retorna  — JSON para previsualizar y confirmar antes de guardar
# ═════════════════════════════════════════════════════════════════════════════

_ROUTINE_ETL_PROMPT = """
Eres un experto en entrenamiento físico y planificación de rutinas de gimnasio.
Tu tarea es extraer la información del documento y devolver ÚNICAMENTE un objeto JSON
válido, sin explicaciones, sin markdown, sin texto extra.

La estructura JSON que debes devolver es exactamente:
{
  "rutinas": [
    {
      "name": "nombre de la rutina",
      "category": "Fuerza|Hipertrofia|Cardio|Funcional|Movilidad|General",
      "difficulty": "Principiante|Intermedio|Avanzado",
      "duration_minutes": <número entero o 60>,
      "description": "descripción breve de la rutina",
      "days": [
        {
          "day": "Lunes|Martes|Miércoles|Jueves|Viernes|Sábado|Domingo",
          "muscleGroup": "Pecho|Espalda|Piernas|Hombros|Bíceps|Tríceps|Abdomen|Full Body|Cardio",
          "exercises": [
            {
              "name": "nombre del ejercicio",
              "sets": "3",
              "reps": "12",
              "peso": "descripción del peso o intensidad",
              "notes": "notas técnicas o de ejecución"
            }
          ]
        }
      ]
    }
  ],
  "ejercicios": [
    {
      "nombre": "nombre del ejercicio",
      "grupo_muscular": "Pecho|Espalda|Piernas|Hombros|Bíceps|Tríceps|Abdomen|Glúteos|Cuádriceps|Isquiotibiales|Full Body",
      "tipo": "Fuerza|Cardio|Flexibilidad|Funcional|Potencia",
      "series": <número entero o null>,
      "repeticiones": "descripción de repeticiones",
      "descripcion": "descripción o instrucciones del ejercicio"
    }
  ]
}

Reglas importantes:
- Extrae TODAS las rutinas y días que encuentres en el documento
- Si no hay estructura de días, agrupa los ejercicios en un solo día "Lunes"
- Para ejercicios sin número de series usa 3 como default
- Para ejercicios sin repeticiones usa "12" como default
- El array "ejercicios" debe contener ejercicios únicos del documento para la biblioteca
- Si no encuentras rutinas estructuradas, devuelve "rutinas": []
- Si no encuentras ejercicios individuales, devuelve "ejercicios": []
- RESPONDE SOLO CON EL JSON, nada más"""


@trainer_bp.route("/routines/ai-status", methods=["GET"])
@jwt_required()
@require_tenant
def routine_ai_status():
    """Verifica disponibilidad de Ollama para el ETL de rutinas."""
    from app.utils.etl_ollama import get_ollama_status  # noqa: PLC0415
    return jsonify(get_ollama_status()), 200


@trainer_bp.route("/routines/import-ai", methods=["POST"])
@jwt_required()
@require_tenant
def import_routines_ai():
    """
    ETL híbrido para importar rutinas desde PDF o Excel.

    Estrategia en dos pasos:
      1. Parser determinístico — reconoce el formato "Sesión N: Título ⏱ X min"
         con tabla de ejercicios. Instantáneo, sin dependencias externas.
      2. Fallback Ollama — si el PDF no sigue la estructura reconocida,
         se envía al LLM local para extracción flexible.

    El entrenador puede subir:
      - El historial de entrenamiento de un cliente de otro gimnasio/app
      - Su propia biblioteca de rutinas en cualquier formato
    """
    import json as _json  # noqa: PLC0415
    try:
        from app.utils.etl_ollama import (  # noqa: PLC0415
            extract_text, parse_routines_from_text, check_ollama_ready, call_ollama
        )

        archivo = request.files.get("archivo")
        if not archivo:
            return jsonify({"error": "No se recibió archivo"}), 400

        nombre_archivo = archivo.filename or ""
        ext = nombre_archivo.rsplit(".", 1)[-1].lower()
        if ext not in {"pdf", "xlsx", "xls"}:
            return jsonify({
                "error": "Formato no soportado",
                "detalle": "Usa un archivo PDF (.pdf) o Excel (.xlsx / .xls)",
            }), 400

        # ── Extract ──────────────────────────────────────────────────────────
        try:
            contenido = archivo.read()
            raw_text  = extract_text(contenido, ext)
        except Exception as e:
            return jsonify({"error": f"Error leyendo el archivo: {e}"}), 400

        if not raw_text.strip():
            return jsonify({
                "error": "El archivo no contiene texto extraíble",
                "detalle": "Asegúrate de que el PDF no sea una imagen escaneada.",
            }), 422

        # ── Transform: parser determinístico (rápido, sin LLM) ───────────────
        resultado = parse_routines_from_text(raw_text)

        if resultado is None:
            # ── Fallback: LLM local (Ollama) ─────────────────────────────────
            ready, msg = check_ollama_ready()
            if not ready:
                return jsonify({"error": "Servicio de IA no disponible", "detalle": msg}), 503

            respuesta_raw = call_ollama(_ROUTINE_ETL_PROMPT, raw_text[:4_000])

            texto = respuesta_raw.strip()
            if texto.startswith("```"):
                lineas = texto.split("\n")
                texto  = "\n".join(lineas[1:] if len(lineas) > 1 else lineas)
            if texto.endswith("```"):
                texto = texto[: texto.rfind("```")].strip()

            try:
                resultado = _json.loads(texto)
            except _json.JSONDecodeError:
                return jsonify({
                    "error": "La IA no pudo estructurar el documento",
                    "detalle": (
                        "El archivo tiene un formato no reconocido. "
                        "Prueba con un PDF con texto seleccionable o un Excel bien estructurado."
                    ),
                }), 422

            if not isinstance(resultado, dict):
                return jsonify({
                    "error": "La IA devolvió una respuesta vacía",
                    "detalle": (
                        "El modelo no pudo extraer información del documento. "
                        "Intenta con un archivo más simple o con menos páginas."
                    ),
                }), 422

        rutinas    = resultado.get("rutinas",   [])
        ejercicios = resultado.get("ejercicios", [])

        return jsonify({
            "success":    True,
            "rutinas":    rutinas,
            "ejercicios": ejercicios,
            "archivo":    nombre_archivo,
            "resumen": {
                "total_rutinas":    len(rutinas),
                "total_ejercicios": len(ejercicios),
                "total_dias": sum(len(r.get("days", [])) for r in rutinas),
            },
        }), 200

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"error": f"Error en el proceso de IA: {e}"}), 500
