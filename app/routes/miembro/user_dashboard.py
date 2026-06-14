from flask import Blueprint, jsonify, request, g
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timedelta
from bson.objectid import ObjectId

from app.mongo import get_db
from app.models.pg.usuario import Usuario
from app.models.pg.tipo_membresia import TipoMembresia
from app.utils.tenant import require_tenant

user_dashboard_bp = Blueprint('user_dashboard', __name__)


@user_dashboard_bp.route('/api/user/dashboard', methods=['GET'])
@jwt_required()
@require_tenant
def get_user_dashboard():
    """Endpoint principal del dashboard del miembro autenticado."""
    try:
        mdb        = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id

        miembro = mdb.miembros.find_one({
            "id_usuario_pg":  user_pg_id,
            "id_gimnasio_pg": gym_id
        })
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404

        # Datos base desde PG (fuente de verdad tras Sprint 2)
        usuario = Usuario.query.get(user_pg_id)

        user_data = {
            "id":         str(miembro["_id"]),
            "nombre":     usuario.nombre     if usuario else miembro.get("nombre", "Usuario"),
            "email":      usuario.email      if usuario else miembro.get("email", ""),
            "role":       "Miembro",
            "foto_perfil":miembro.get("foto_perfil")
        }

        workout_stats       = _get_workout_stats(mdb, miembro["_id"])
        today_workout       = _get_today_workout(mdb, miembro["_id"])
        weekly_progress     = _get_weekly_progress(mdb, miembro["_id"])
        recent_achievements = _get_recent_achievements(mdb, miembro["_id"])
        membership_info     = _get_active_membership(mdb, miembro["_id"])

        return jsonify({
            "user":          user_data,
            "workoutStats":  workout_stats,
            "todayWorkout":  today_workout,
            "weeklyProgress":weekly_progress,
            "achievements":  recent_achievements,
            "membership":    membership_info
        }), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ─── Registrar asistencia (check-in) ───────────────────────────────────────

@user_dashboard_bp.route('/api/user/checkin', methods=['POST'])
@jwt_required()
@require_tenant
def register_checkin():
    try:
        mdb        = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id

        miembro = mdb.miembros.find_one({
            "id_usuario_pg":  user_pg_id,
            "id_gimnasio_pg": gym_id
        })
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404

        # Hora local del gimnasio (no UTC del servidor): evita que un check-in
        # nocturno se registre con la fecha del día siguiente.
        from app.utils.timezone import local_now_naive
        now         = local_now_naive()
        hoy_inicio  = datetime.combine(now.date(), datetime.min.time())
        hoy_fin     = hoy_inicio + timedelta(days=1)

        asistencia_hoy = mdb.asistencias.find_one({
            "id_miembro": miembro["_id"],
            "fecha":      {"$gte": hoy_inicio, "$lt": hoy_fin}
        })
        if asistencia_hoy:
            return jsonify({"message": "Ya registraste tu asistencia hoy"}), 200

        mdb.asistencias.insert_one({
            "id_miembro":  miembro["_id"],
            "id_gimnasio": gym_id,
            "fecha":       now,
            "hora_entrada":now.strftime('%H:%M:%S')
        })
        return jsonify({
            "message": "Asistencia registrada exitosamente",
            "fecha":   now.strftime('%Y-%m-%d')
        }), 201

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ─── Registrar progreso físico ──────────────────────────────────────────────

@user_dashboard_bp.route('/api/user/progress', methods=['POST'])
@jwt_required()
@require_tenant
def register_progress():
    try:
        mdb        = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        data       = request.json or {}

        miembro = mdb.miembros.find_one({
            "id_usuario_pg":  user_pg_id,
            "id_gimnasio_pg": gym_id
        })
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404

        nuevo_progreso = {
            "id_miembro":    miembro["_id"],
            "peso":          float(data.get('peso', 0)),
            "cintura":       float(data['cintura'])  if data.get('cintura')  else None,
            "cadera":        float(data['cadera'])   if data.get('cadera')   else None,
            "fecha_registro":datetime.now()
        }

        estatura = float(miembro.get("estatura") or 0)
        bmi      = data.get('bmi')
        if not bmi and estatura > 0 and nuevo_progreso["peso"]:
            bmi = round(nuevo_progreso["peso"] / (estatura ** 2), 2)
        nuevo_progreso["bmi"] = float(bmi) if bmi else None

        result = mdb.progreso_fisico.insert_one(nuevo_progreso)

        return jsonify({
            "message": "Progreso registrado exitosamente",
            "progreso": {
                "_id":            str(result.inserted_id),
                "id_miembro":     str(miembro["_id"]),
                "peso":           nuevo_progreso["peso"],
                "fecha_registro": nuevo_progreso["fecha_registro"].strftime('%Y-%m-%d')
            }
        }), 201

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ─── Marcar ejercicio completado (stub) ────────────────────────────────────

@user_dashboard_bp.route('/api/user/workout/complete', methods=['POST'])
@jwt_required()
def complete_exercise():
    try:
        data = request.json or {}
        return jsonify({
            "message": "Ejercicio marcado como completado",
            "exercise":data.get("exercise_name")
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Helpers ───────────────────────────────────────────────────────────────

def _get_workout_stats(mdb, id_miembro):
    try:
        now              = datetime.now()
        start_of_month   = datetime(now.year, now.month, 1)
        start_of_next    = datetime(now.year + 1, 1, 1) if now.month == 12 \
                           else datetime(now.year, now.month + 1, 1)

        asistencias_mes = mdb.asistencias.count_documents({
            "id_miembro": id_miembro,
            "fecha":      {"$gte": start_of_month, "$lt": start_of_next}
        })
        racha            = _calcular_racha(mdb, id_miembro)

        progreso = list(mdb.progreso_fisico.find(
            {"id_miembro": id_miembro}
        ).sort("fecha_registro", -1).limit(1))
        if progreso and progreso[0].get("peso"):
            peso_actual = float(progreso[0]["peso"])
        else:
            m = mdb.miembros.find_one({"_id": id_miembro})
            peso_actual = float(m.get("peso_inicial", 0) or 0) if m else 0

        primera = list(mdb.asistencias.find(
            {"id_miembro": id_miembro}
        ).sort("fecha", 1).limit(1))
        semana_actual = 1
        if primera:
            f = primera[0].get("fecha")
            if isinstance(f, str):   f = datetime.strptime(f[:10], "%Y-%m-%d").date()
            elif isinstance(f, datetime): f = f.date()
            semana_actual = ((now.date() - f).days // 7) + 1

        return {
            "currentWeek":    semana_actual,
            "totalWorkouts":  asistencias_mes,
            "caloriesBurned": asistencias_mes * 300,
            "streakDays":     racha,
            "currentWeight":  peso_actual
        }
    except Exception as e:
        print(f"Error _get_workout_stats: {e}")
        return {"currentWeek": 0, "totalWorkouts": 0,
                "caloriesBurned": 0, "streakDays": 0, "currentWeight": 0}


def _calcular_racha(mdb, id_miembro):
    try:
        asistencias = list(mdb.asistencias.find(
            {"id_miembro": id_miembro}
        ).sort("fecha", -1))
        if not asistencias: return 0

        fechas = []
        for a in asistencias:
            f = a.get("fecha")
            if isinstance(f, datetime): f = f.date()
            elif isinstance(f, str):    f = datetime.strptime(f[:10], "%Y-%m-%d").date()
            if f not in fechas: fechas.append(f)
        fechas.sort(reverse=True)

        hoy  = datetime.now().date()
        if (hoy - fechas[0]).days > 1: return 0

        racha          = 0
        fecha_esperada = hoy if (hoy - fechas[0]).days == 0 else hoy - timedelta(days=1)
        for f in fechas:
            if f == fecha_esperada:
                racha         += 1
                fecha_esperada -= timedelta(days=1)
            elif f < fecha_esperada:
                break
        return racha
    except Exception as e:
        print(f"Error _calcular_racha: {e}")
        return 0


def _get_today_workout(mdb, id_miembro):
    """Obtiene la rutina real del miembro para el día de hoy."""
    DIAS_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    dia_hoy = DIAS_ES[datetime.now().weekday()]
    try:
        # Buscar la rutina activa del miembro
        rutina = mdb.rutinas.find_one({
            "id_miembro": id_miembro,
            "activa": True
        })
        if not rutina:
            # Fallback: buscar cualquier rutina del miembro
            rutina = mdb.rutinas.find_one(
                {"id_miembro": id_miembro},
                sort=[("_id", -1)]
            )
        if not rutina:
            return {"type": "Sin rutina asignada", "exercises": []}

        # Buscar el día correspondiente a hoy
        dia_doc = mdb.rutina_dias.find_one({
            "id_rutina": rutina["_id"],
            "dia_semana": dia_hoy
        })
        if not dia_doc:
            return {"type": "Descanso", "exercises": []}

        grupo = dia_doc.get("grupo_muscular", "")
        if grupo == "descanso":
            return {"type": "Descanso", "exercises": []}

        ejercicios = list(mdb.rutina_ejercicios.find(
            {"id_rutina_dia": dia_doc["_id"]}
        ).sort("orden", 1))

        exs = [
            {
                "name":      ej.get("nombre_ejercicio", ""),
                "sets":      f"{ej.get('series', '3')}x{ej.get('repeticiones', '12')}",
                "completed": False
            }
            for ej in ejercicios
            if ej.get("nombre_ejercicio")
        ]

        # Nombre legible del grupo muscular
        GRUPO_LABELS = {
            "pecho": "Pecho", "espalda": "Espalda", "hombros": "Hombros",
            "biceps": "Bíceps", "triceps": "Tríceps", "piernas": "Piernas",
            "gluteos": "Glúteos", "abdomen": "Abdomen / Core",
            "cardio": "Cardio", "descanso": "Descanso",
        }
        tipo = GRUPO_LABELS.get(grupo, grupo.capitalize())
        return {"type": tipo, "exercises": exs}

    except Exception as e:
        print(f"Error _get_today_workout: {e}")
        return {"type": "Descanso", "exercises": []}


def _get_weekly_progress(mdb, id_miembro):
    try:
        now           = datetime.now()
        inicio_semana = datetime.combine(
            now.date() - timedelta(days=now.weekday()), datetime.min.time()
        )
        return [
            100 if mdb.asistencias.count_documents({
                "id_miembro": id_miembro,
                "fecha":      {"$gte": inicio_semana + timedelta(days=i),
                               "$lt":  inicio_semana + timedelta(days=i + 1)}
            }) > 0 else 0
            for i in range(7)
        ]
    except Exception as e:
        print(f"Error _get_weekly_progress: {e}")
        return [0] * 7


def _get_recent_achievements(mdb, id_miembro):
    achievements = []
    try:
        racha = _calcular_racha(mdb, id_miembro)
        if racha >= 7:
            achievements.append({"icon":"FaFire","title":f"Racha de {racha} días",
                                  "description":"Completado hoy","color":"var(--accent)"})
        elif racha >= 3:
            achievements.append({"icon":"FaFire","title":f"Racha de {racha} días",
                                  "description":"¡Sigue así!","color":"var(--warning-color)"})

        progresos = list(mdb.progreso_fisico.find(
            {"id_miembro": id_miembro}
        ).sort("fecha_registro", -1).limit(2))
        if len(progresos) >= 2:
            diff = float(progresos[0].get("peso", 0) or 0) - float(progresos[1].get("peso", 0) or 0)
            if abs(diff) >= 1:
                achievements.append({
                    "icon":"FaDumbbell",
                    "title":f"{'+' if diff>0 else ''}{diff:.1f}kg de progreso",
                    "description":"Última medición","color":"var(--success-color)"
                })

        now = datetime.now()
        inicio = datetime(now.year, now.month, 1)
        fin    = datetime(now.year + 1, 1, 1) if now.month == 12 \
                 else datetime(now.year, now.month + 1, 1)
        asis_mes = mdb.asistencias.count_documents({
            "id_miembro": id_miembro,
            "fecha":      {"$gte": inicio, "$lt": fin}
        })
        if asis_mes >= 20:
            achievements.append({"icon":"FaTrophy","title":f"{asis_mes} entrenamientos",
                                  "description":"¡Campeón del mes!","color":"var(--accent)"})
        elif asis_mes >= 12:
            achievements.append({"icon":"FaBolt","title":f"{asis_mes} entrenamientos",
                                  "description":"Este mes","color":"var(--warning-color)"})
    except Exception as e:
        print(f"Error _get_recent_achievements: {e}")
    return achievements


def _get_active_membership(mdb, id_miembro):
    try:
        membresia_activa = mdb.miembro_membresia.find_one({
            "id_miembro": id_miembro,
            "estado":     "Activa"
        })
        if not membresia_activa: return None

        # Dual lookup: id_membresia puede ser int (PG) u ObjectId (legado Mongo)
        id_mem     = membresia_activa.get("id_membresia")
        nombre_plan = "N/A"
        if isinstance(id_mem, int):
            tm = TipoMembresia.query.get(id_mem)
            if tm: nombre_plan = tm.nombre
        elif id_mem:
            plan_doc = mdb.membresias.find_one({"_id": id_mem})
            if plan_doc: nombre_plan = plan_doc.get("nombre", "N/A")

        fecha_fin = membresia_activa.get("fecha_fin")
        if isinstance(fecha_fin, str):
            fecha_fin = datetime.strptime(fecha_fin[:10], "%Y-%m-%d").date()
        elif isinstance(fecha_fin, datetime):
            fecha_fin = fecha_fin.date()

        dias_restantes = (fecha_fin - datetime.now().date()).days
        return {
            "plan":           nombre_plan,
            "fecha_fin":      fecha_fin.strftime('%Y-%m-%d'),
            "dias_restantes": dias_restantes,
            "estado":         "activa" if dias_restantes > 0 else "por_vencer"
        }
    except Exception as e:
        print(f"Error _get_active_membership: {e}")
        return None
