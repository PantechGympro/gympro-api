from flask import Blueprint, jsonify, request, g
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from bson.objectid import ObjectId
from datetime import datetime, date, timedelta
import traceback

from app.mongo import get_db
from app.extensions import db as pg_db
from app.models.pg.usuario import Usuario
from app.utils.tenant import require_tenant

trainer_bp = Blueprint('trainer', __name__, url_prefix='/api/trainer')


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
                "id":           str(miembro_id),
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

        total_clientes  = mdb.miembros.count_documents(
            {"id_entrenador_pg": trainer_id, "id_gimnasio_pg": gym_id}
        )
        total_sesiones  = mdb.sesiones.count_documents(
            {"id_entrenador_pg": trainer_id, "estado": "completed"}
        )

        ingresos_pipeline = [
            {"$match":  {"id_entrenador_pg": trainer_id}},
            {"$group":  {"_id": None, "total": {"$sum": "$monto"}}}
        ]
        ingresos_result = list(mdb.pagos.aggregate(ingresos_pipeline))
        total_ingresos  = ingresos_result[0]["total"] if ingresos_result else 0

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

        profile_data = {
            'name':           usuario.nombre,
            'email':          usuario.email,
            'phone':          perfil.get("telefono", "")       if perfil else "",
            'address':        perfil.get("direccion", "")      if perfil else "",
            'specialization': perfil.get("especializacion", "") if perfil else "",
            'experience':     f"{anos_activos} años",
            'certifications': ', '.join([c.get("nombre", "") for c in certificaciones]),
            'bio':            perfil.get("biografia", "")      if perfil else "",
            'stats': {
                'totalClients':  total_clientes,
                'totalSessions': total_sesiones,
                'totalEarnings': float(total_ingresos),
                'avgRating':     round(calificacion_promedio, 1),
                'yearsActive':   anos_activos,
                'certifications':len(certificaciones)
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
        if 'phone'          in data: update_perfil['telefono']       = data['phone']
        if 'address'        in data: update_perfil['direccion']      = data['address']
        if 'specialization' in data: update_perfil['especializacion']= data['specialization']
        if 'bio'            in data: update_perfil['biografia']       = data['bio']

        if update_perfil:
            mdb.perfil_entrenador.update_one(
                {"id_entrenador_pg": trainer_id},
                {"$set": update_perfil},
                upsert=True
            )

        return jsonify({'success': True, 'message': 'Perfil actualizado correctamente'}), 200

    except Exception as e:
        print(traceback.format_exc())
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500


# ═══════════════════════════════════════════════════════════════
#  RUTAS — AGENDA Y SESIONES
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

        all_sessions = list(mdb.sesiones.find({"id_entrenador_pg": trainer_id}))
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
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id

        miembros = list(mdb.miembros.find({
            "id_entrenador_pg": trainer_id,
            "id_gimnasio_pg":   gym_id,
            "estado":           "Activo"
        }))

        members = [
            {
                "id_miembro": str(m["_id"]),
                "nombre":     m.get("nombre", f"Miembro {m['_id']}"),
                "email":      m.get("email", ""),
            }
            for m in miembros
        ]
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

            for dia in dias:
                ejercicios = list(mdb.rutina_ejercicios.find({"id_rutina_dia": dia["_id"]}).sort("orden", 1))
                total_ejercicios += len(ejercicios)
                for ej in ejercicios:
                    exercise_list.append({
                        'name':  ej.get("nombre_ejercicio", ""),
                        'sets':  f"{ej.get('series', '')}x{ej.get('repeticiones', '')}",
                        'rest':  ej.get("notas") or '60s',
                        'day':   dia.get("dia_semana") or '',
                        'peso':  ej.get("peso") or ''
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
                    "orden":            order_e
                }
                for order_e, ej in enumerate(day_data.get('exercises', []))
            ]
            if ejercicios_insert:
                mdb.rutina_ejercicios.insert_many(ejercicios_insert)

        return jsonify({
            'success':   True,
            'id_rutina': str(rutina_id),
            'message':   'Rutina creada correctamente'
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
#  REPORTES Y ESTADÍSTICAS
# ═══════════════════════════════════════════════════════════════

@trainer_bp.route('/reports', methods=['GET'])
@jwt_required()
@require_tenant
def get_reports():
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        range_param= request.args.get('range', 'month')

        today = datetime.combine(date.today(), datetime.max.time())
        if range_param == 'week':
            start = datetime.combine(date.today() - timedelta(days=date.today().weekday()), datetime.min.time())
        elif range_param == 'month':
            start = today.replace(day=1, hour=0, minute=0, second=0)
        elif range_param == 'quarter':
            month_start = ((today.month - 1) // 3) * 3 + 1
            start = today.replace(month=month_start, day=1, hour=0, minute=0, second=0)
        else:
            start = today.replace(month=1, day=1, hour=0, minute=0, second=0)

        total_sessions = mdb.sesiones.count_documents({
            "id_entrenador_pg": trainer_id,
            "fecha":            {"$gte": start},
            "estado":           "completed"
        })
        total_clients = mdb.miembros.count_documents({
            "id_entrenador_pg": trainer_id,
            "id_gimnasio_pg":   gym_id,
            "estado":           "Activo"
        })

        rev_pipeline = [
            {"$match": {"id_entrenador_pg": trainer_id, "fecha_pago": {"$gte": start}}},
            {"$group": {"_id": None, "total": {"$sum": "$monto"}}}
        ]
        rev_res       = list(mdb.pagos.aggregate(rev_pipeline))
        total_revenue = rev_res[0]['total'] if rev_res else 0

        prev_start    = start - (today - start)
        prev_sessions = mdb.sesiones.count_documents({
            "id_entrenador_pg": trainer_id,
            "fecha":            {"$gte": prev_start, "$lt": start},
            "estado":           "completed"
        })
        session_growth = _pct_growth(total_sessions, prev_sessions)

        return jsonify({
            'success': True,
            'stats': {
                'revenue':  total_revenue,
                'sessions': total_sessions,
                'clients':  total_clients,
                'growth': {
                    'sessions': session_growth,
                    'revenue':  0,
                    'clients':  0
                }
            }
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
        "id_sesion":      str(s["_id"]),
        "date":           fecha_str.split('T')[0] if 'T' in fecha_str else fecha_str,
        "time":           hora_str,
        "client":         _get_client_name(mdb, s),
        "type":           s.get("tipo"),
        "duration":       f"{s.get('duracion_minutos', 60)} min",
        "duracion_minutos":s.get("duracion_minutos", 60),
        "location":       s.get("ubicacion") or "Sin ubicación",
        "status":         s.get("estado"),
        "notes":          s.get("notas") or "",
        "exercises":      s.get("num_ejercicios") or 0,
        "attendance":     bool(s.get("asistencia")),
        "nombre_sesion":  s.get("nombre_sesion") or "",
        "id_miembro":     str(s["id_miembro"]) if s.get("id_miembro") else None,
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
        if not asistencias: return 0
        racha        = 0
        fecha_actual = datetime.now().date()
        for asistencia in asistencias:
            f_asist = asistencia.get("fecha")
            if isinstance(f_asist, datetime): f_asist = f_asist.date()
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
            "estado":     {"$in": ['completed', 'cancelled']}
        })
        if programadas == 0: return 0
        completadas = mdb.sesiones.count_documents({
            "id_miembro": id_miembro,
            "fecha":      {"$gte": fecha_inicio},
            "estado":     "completed"
        })
        return round((completadas / programadas) * 100)
    except Exception:
        return 0


def determinar_estado_cliente(ultima_sesion, tasa_asistencia):
    try:
        if not ultima_sesion: return 'warning'
        fecha_us = ultima_sesion.get("fecha")
        if isinstance(fecha_us, datetime): fecha_us = fecha_us.date()
        dias = (datetime.now().date() - fecha_us).days
        return 'warning' if dias > 7 or tasa_asistencia < 70 else 'active'
    except Exception:
        return 'active'


def _pct_growth(current, previous):
    if previous == 0: return 100 if current > 0 else 0
    return round(((current - previous) / previous) * 100)


def _format_fecha(ts):
    try:
        if not ts: return 'Nunca'
        if isinstance(ts, str): ts = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
        diff = datetime.now() - ts
        days = diff.days
        if days == 0:   return 'Hoy'
        elif days == 1: return 'Ayer'
        elif days < 7:  return f'Hace {days} días'
        else:           return ts.strftime('%d/%m/%Y')
    except Exception:
        return '-'
