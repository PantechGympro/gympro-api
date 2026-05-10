from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timedelta
from bson.objectid import ObjectId
from app.mongo import get_db

user_dashboard_bp = Blueprint('user_dashboard', __name__)

@user_dashboard_bp.route('/api/user/dashboard', methods=['GET'])
@jwt_required()
def get_user_dashboard():
    """
    Endpoint principal para obtener todos los datos del dashboard del usuario
    """
    try:
        db = get_db()
        user_id = ObjectId(get_jwt_identity())
        
        miembro = db.miembros.find_one({"id_usuario": user_id})
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404

        usuario = db.usuarios.find_one({"_id": user_id})
        
        # 1. DATOS BÁSICOS DEL USUARIO
        user_data = {
            "id": str(usuario["_id"]),
            "nombre": usuario.get("nombre", "Usuario"),
            "email": usuario.get("email", ""),
            "role": "Miembro",
            "foto_perfil": miembro.get("foto_perfil")
        }

        # 2. ESTADÍSTICAS DE WORKOUT
        workout_stats = _get_workout_stats(db, miembro["_id"], miembro.get("estatura"))
        
        # 3. RUTINA DE HOY (simulada)
        today_workout = _get_today_workout()
        
        # 4. PROGRESO SEMANAL
        weekly_progress = _get_weekly_progress(db, miembro["_id"])
        
        # 5. LOGROS RECIENTES
        recent_achievements = _get_recent_achievements(db, miembro["_id"])
        
        # 6. INFORMACIÓN DE MEMBRESÍA ACTIVA
        membership_info = _get_active_membership(db, miembro["_id"])

        return jsonify({
            "user": user_data,
            "workoutStats": workout_stats,
            "todayWorkout": today_workout,
            "weeklyProgress": weekly_progress,
            "achievements": recent_achievements,
            "membership": membership_info
        }), 200

    except Exception as e:
        print(f"Error en get_user_dashboard: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


def _get_workout_stats(db, id_miembro, estatura):
    try:
        now = datetime.now()
        start_of_month = datetime(now.year, now.month, 1)
        if now.month == 12:
            start_of_next_month = datetime(now.year + 1, 1, 1)
        else:
            start_of_next_month = datetime(now.year, now.month + 1, 1)
            
        # Asistencias este mes
        asistencias_mes = db.asistencias.count_documents({
            "id_miembro": id_miembro,
            "fecha": {"$gte": start_of_month, "$lt": start_of_next_month}
        })
        
        racha = _calcular_racha(db, id_miembro)
        calorias_estimadas = asistencias_mes * 300
        
        # Progreso reciente
        progreso_reciente = list(db.progreso_fisico.find({"id_miembro": id_miembro}).sort("fecha_registro", -1).limit(1))
        
        if progreso_reciente and progreso_reciente[0].get("peso"):
            peso_actual = float(progreso_reciente[0]["peso"])
        else:
            miembro = db.miembros.find_one({"_id": id_miembro})
            peso_actual = float(miembro.get("peso_inicial", 0) or 0)
            
        # Calcular semana actual (basado en fecha de primera asistencia)
        primera_asistencia = list(db.asistencias.find({"id_miembro": id_miembro}).sort("fecha", 1).limit(1))
        
        if primera_asistencia:
            fecha_pa = primera_asistencia[0].get("fecha")
            if isinstance(fecha_pa, str):
                fecha_pa = datetime.strptime(fecha_pa[:10], "%Y-%m-%d").date()
            elif isinstance(fecha_pa, datetime):
                fecha_pa = fecha_pa.date()
                
            dias_desde_inicio = (now.date() - fecha_pa).days
            semana_actual = (dias_desde_inicio // 7) + 1
        else:
            semana_actual = 1
            
        return {
            "currentWeek": semana_actual,
            "totalWorkouts": asistencias_mes,
            "caloriesBurned": calorias_estimadas,
            "streakDays": racha,
            "currentWeight": peso_actual
        }
    except Exception as e:
        print(f"Error en _get_workout_stats: {e}")
        return {
            "currentWeek": 0, "totalWorkouts": 0, "caloriesBurned": 0,
            "streakDays": 0, "currentWeight": 0
        }


def _calcular_racha(db, id_miembro):
    try:
        asistencias = list(db.asistencias.find({"id_miembro": id_miembro}).sort("fecha", -1))
        if not asistencias: return 0
        
        # Limpiar y ordenar fechas únicas
        fechas_asistencia = []
        for a in asistencias:
            f = a.get("fecha")
            if isinstance(f, datetime): f = f.date()
            elif isinstance(f, str): f = datetime.strptime(f[:10], "%Y-%m-%d").date()
            if f not in fechas_asistencia:
                fechas_asistencia.append(f)
                
        fechas_asistencia.sort(reverse=True)
        
        fecha_actual = datetime.now().date()
        fecha_mas_reciente = fechas_asistencia[0]
        
        dias_desde_ultima = (fecha_actual - fecha_mas_reciente).days
        if dias_desde_ultima > 1: return 0
        
        racha = 0
        fecha_esperada = fecha_actual if dias_desde_ultima == 0 else fecha_actual - timedelta(days=1)
        
        for fecha in fechas_asistencia:
            if fecha == fecha_esperada:
                racha += 1
                fecha_esperada -= timedelta(days=1)
            elif fecha < fecha_esperada:
                break
                
        return racha
    except Exception as e:
        print(f"Error calculando racha: {e}")
        return 0


def _get_today_workout():
    dias = ["Descanso", "Pecho y Tríceps", "Espalda y Bíceps", "Pierna", "Hombro", "Cardio", "Descanso"]
    dia_semana = datetime.now().weekday()
    
    rutinas = {
        "Pecho y Tríceps": [
            {"name": "Press Banca", "sets": "4x10", "completed": False},
            {"name": "Press Inclinado", "sets": "3x12", "completed": False},
            {"name": "Aperturas", "sets": "3x12", "completed": False},
            {"name": "Fondos", "sets": "3x15", "completed": False},
            {"name": "Extensiones Tríceps", "sets": "4x12", "completed": False}
        ],
        "Espalda y Bíceps": [
            {"name": "Dominadas", "sets": "4x8", "completed": False},
            {"name": "Remo con Barra", "sets": "4x10", "completed": False},
            {"name": "Jalones", "sets": "3x12", "completed": False},
            {"name": "Curl con Barra", "sets": "4x12", "completed": False},
            {"name": "Curl Martillo", "sets": "3x12", "completed": False}
        ],
        "Pierna": [
            {"name": "Sentadillas", "sets": "4x12", "completed": False},
            {"name": "Prensa", "sets": "3x15", "completed": False},
            {"name": "Extensiones", "sets": "3x12", "completed": False},
            {"name": "Curl Femoral", "sets": "4x10", "completed": False},
            {"name": "Elevación de Talones", "sets": "5x20", "completed": False}
        ],
        "Hombro": [
            {"name": "Press Militar", "sets": "4x10", "completed": False},
            {"name": "Elevaciones Laterales", "sets": "4x12", "completed": False},
            {"name": "Elevaciones Frontales", "sets": "3x12", "completed": False},
            {"name": "Pájaros", "sets": "3x15", "completed": False},
            {"name": "Encogimientos", "sets": "4x12", "completed": False}
        ],
        "Cardio": [
            {"name": "Caminadora", "sets": "30 min", "completed": False},
            {"name": "Bicicleta", "sets": "20 min", "completed": False},
            {"name": "Elíptica", "sets": "15 min", "completed": False}
        ]
    }
    tipo_rutina = dias[dia_semana]
    return {
        "type": tipo_rutina,
        "exercises": rutinas.get(tipo_rutina, [])
    }


def _get_weekly_progress(db, id_miembro):
    try:
        now = datetime.now()
        inicio_semana = datetime.combine(now.date() - timedelta(days=now.weekday()), datetime.min.time())
        progreso_diario = []
        
        for i in range(7):
            dia_inicio = inicio_semana + timedelta(days=i)
            dia_fin = dia_inicio + timedelta(days=1)
            
            asistencias = db.asistencias.count_documents({
                "id_miembro": id_miembro,
                "fecha": {"$gte": dia_inicio, "$lt": dia_fin}
            })
            
            progreso_diario.append(100 if asistencias > 0 else 0)
            
        return progreso_diario
    except Exception as e:
        print(f"Error en _get_weekly_progress: {e}")
        return [0, 0, 0, 0, 0, 0, 0]


def _get_recent_achievements(db, id_miembro):
    achievements = []
    try:
        racha = _calcular_racha(db, id_miembro)
        if racha >= 7:
            achievements.append({
                "icon": "FaFire",
                "title": f"Racha de {racha} días",
                "description": "Completado hoy",
                "color": "var(--accent)"
            })
        elif racha >= 3:
            achievements.append({
                "icon": "FaFire",
                "title": f"Racha de {racha} días",
                "description": "¡Sigue así!",
                "color": "var(--warning-color)"
            })
            
        progresos = list(db.progreso_fisico.find({"id_miembro": id_miembro}).sort("fecha_registro", -1).limit(2))
        
        if len(progresos) >= 2:
            peso_actual = float(progresos[0].get("peso", 0) or 0)
            peso_anterior = float(progresos[1].get("peso", 0) or 0)
            diferencia = peso_actual - peso_anterior
            
            if abs(diferencia) >= 1:
                signo = "+" if diferencia > 0 else ""
                achievements.append({
                    "icon": "FaDumbbell",
                    "title": f"{signo}{diferencia:.1f}kg de progreso",
                    "description": "Última medición",
                    "color": "var(--success-color)"
                })
                
        now = datetime.now()
        start_of_month = datetime(now.year, now.month, 1)
        if now.month == 12:
            start_of_next_month = datetime(now.year + 1, 1, 1)
        else:
            start_of_next_month = datetime(now.year, now.month + 1, 1)
            
        asistencias_mes = db.asistencias.count_documents({
            "id_miembro": id_miembro,
            "fecha": {"$gte": start_of_month, "$lt": start_of_next_month}
        })
        
        if asistencias_mes >= 20:
            achievements.append({
                "icon": "FaTrophy",
                "title": f"{asistencias_mes} entrenamientos",
                "description": "¡Campeón del mes!",
                "color": "var(--accent)"
            })
        elif asistencias_mes >= 12:
            achievements.append({
                "icon": "FaBolt",
                "title": f"{asistencias_mes} entrenamientos",
                "description": "Este mes",
                "color": "var(--warning-color)"
            })
            
    except Exception as e:
        print(f"Error en _get_recent_achievements: {e}")
    return achievements


def _get_active_membership(db, id_miembro):
    try:
        membresia_activa = db.miembro_membresia.find_one({
            "id_miembro": id_miembro,
            "estado": 'Activa'
        })
        
        if not membresia_activa:
            return None
            
        plan_doc = db.membresias.find_one({"_id": membresia_activa.get("id_membresia")})
        nombre_plan = plan_doc.get("nombre", "N/A") if plan_doc else "N/A"
        
        fecha_fin = membresia_activa.get("fecha_fin")
        if isinstance(fecha_fin, str):
            fecha_fin = datetime.strptime(fecha_fin[:10], "%Y-%m-%d").date()
        elif isinstance(fecha_fin, datetime):
            fecha_fin = fecha_fin.date()
            
        dias_restantes = (fecha_fin - datetime.now().date()).days
        
        return {
            "plan": nombre_plan,
            "fecha_fin": fecha_fin.strftime('%Y-%m-%d'),
            "dias_restantes": dias_restantes,
            "estado": "activa" if dias_restantes > 0 else "por_vencer"
        }
    except Exception as e:
        print(f"Error en _get_active_membership: {e}")
        return None


# ============================================
# ENDPOINT PARA MARCAR EJERCICIOS COMPLETADOS
# ============================================
@user_dashboard_bp.route('/api/user/workout/complete', methods=['POST'])
@jwt_required()
def complete_exercise():
    try:
        from flask import request
        data = request.json
        return jsonify({
            "message": "Ejercicio marcado como completado",
            "exercise": data.get("exercise_name")
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================
# ENDPOINT PARA REGISTRAR ASISTENCIA
# ============================================
@user_dashboard_bp.route('/api/user/checkin', methods=['POST'])
@jwt_required()
def register_checkin():
    try:
        db = get_db()
        user_id = ObjectId(get_jwt_identity())
        miembro = db.miembros.find_one({"id_usuario": user_id})
        
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404
            
        now = datetime.now()
        hoy_inicio = datetime.combine(now.date(), datetime.min.time())
        hoy_fin = hoy_inicio + timedelta(days=1)
        
        asistencia_hoy = db.asistencias.find_one({
            "id_miembro": miembro["_id"],
            "fecha": {"$gte": hoy_inicio, "$lt": hoy_fin}
        })
        
        if asistencia_hoy:
            return jsonify({"message": "Ya registraste tu asistencia hoy"}), 200
            
        nueva_asistencia = {
            "id_miembro": miembro["_id"],
            "fecha": now,
            "hora_entrada": now.strftime('%H:%M:%S')
        }
        
        db.asistencias.insert_one(nueva_asistencia)
        
        return jsonify({
            "message": "Asistencia registrada exitosamente",
            "fecha": now.strftime('%Y-%m-%d')
        }), 201
        
    except Exception as e:
        print(f"Error en register_checkin: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================
# ENDPOINT PARA REGISTRAR PROGRESO FÍSICO
# ============================================
@user_dashboard_bp.route('/api/user/progress', methods=['POST'])
@jwt_required()
def register_progress():
    try:
        from flask import request
        db = get_db()
        user_id = ObjectId(get_jwt_identity())
        miembro = db.miembros.find_one({"id_usuario": user_id})
        
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404
            
        data = request.json
        
        nuevo_progreso = {
            "id_miembro": miembro["_id"],
            "peso": float(data.get('peso', 0)),
            "cintura": float(data.get('cintura', 0)) if data.get('cintura') else None,
            "cadera": float(data.get('cadera', 0)) if data.get('cadera') else None,
            "fecha_registro": datetime.now()
        }
        
        estatura = float(miembro.get("estatura") or 0)
        bmi = data.get('bmi')
        if not bmi and estatura > 0 and nuevo_progreso["peso"]:
            bmi = round(_calcular_imc(nuevo_progreso["peso"], estatura), 2)
            
        nuevo_progreso["bmi"] = float(bmi) if bmi else None
            
        db.progreso_fisico.insert_one(nuevo_progreso)
        
        # Para retornar un id como string sin romper
        nuevo_progreso["_id"] = str(nuevo_progreso["_id"])
        nuevo_progreso["id_miembro"] = str(nuevo_progreso["id_miembro"])
        nuevo_progreso["fecha_registro"] = nuevo_progreso["fecha_registro"].strftime('%Y-%m-%d')
        
        return jsonify({
            "message": "Progreso registrado exitosamente",
            "progreso": nuevo_progreso
        }), 201
        
    except Exception as e:
        print(f"Error en register_progress: {e}")
        return jsonify({"error": str(e)}), 500