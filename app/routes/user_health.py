from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime
from bson.objectid import ObjectId
from app.mongo import get_db

user_health_bp = Blueprint('user_health', __name__)

# =========================
# UTILIDADES
# =========================

def _normalizar_estatura(estatura):
    estatura = float(estatura)
    if estatura > 3:  # viene en cm
        return estatura / 100
    return estatura  # ya está en metros

def _calcular_bmi(peso, estatura):
    try:
        peso = float(peso)
        estatura = _normalizar_estatura(estatura)
        if peso <= 0 or estatura <= 0:
            return 0
        return round(peso / (estatura ** 2), 2)
    except Exception:
        return 0

def _calcular_imc(peso, estatura):
    try:
        if estatura > 0 and peso > 0:
            return peso / (estatura ** 2)
        return 0
    except:
        return 0


# =========================
# GET HEALTH
# =========================

@user_health_bp.route('/api/user/health', methods=['GET'])
@jwt_required()
def get_user_health():
    try:
        db = get_db()
        user_id = ObjectId(get_jwt_identity())
        miembro = db.miembros.find_one({"id_usuario": user_id})

        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404

        # Obtener último progreso
        progreso_cursor = db.progreso_fisico.find({"id_miembro": miembro["_id"]}).sort("fecha_registro", -1).limit(1)
        progresos_list = list(progreso_cursor)
        ultimo_progreso = progresos_list[0] if progresos_list else None

        # PESO
        if ultimo_progreso and ultimo_progreso.get("peso"):
            peso_actual = float(ultimo_progreso["peso"])
        else:
            peso_actual = float(miembro.get("peso_inicial") or 0)

        # ESTATURA
        estatura = float(miembro.get("estatura") or 1.7)
        if estatura <= 0:
            estatura = 1.7

        # IMC
        imc = _calcular_imc(peso_actual, estatura)

        condiciones = []

        # ESTATURA
        condiciones.append({
            "nombre": "Estatura",
            "valor": f"{estatura:.2f} m",
            "estado": "normal",
            "icon": "FiMaximize2"
        })

        # IMC
        if imc < 18.5:
            estado = "bajo"
            icon = "FiAlertCircle"
        elif imc < 25:
            estado = "normal"
            icon = "FiCheckCircle"
        elif imc < 30:
            estado = "alto"
            icon = "FiAlertCircle"
        else:
            estado = "muy_alto"
            icon = "FiAlertCircle"

        condiciones.append({
            "nombre": "IMC (Índice de Masa Corporal)",
            "valor": f"{round(imc, 1)}",
            "estado": estado,
            "icon": icon
        })

        # PESO
        if peso_actual > 0:
            condiciones.append({
                "nombre": "Peso Actual",
                "valor": f"{peso_actual:.1f} kg",
                "estado": "normal",
                "icon": "FiActivity"
            })

        # MEDIDAS
        if ultimo_progreso:
            medidas = {
                "cintura": "Circunferencia de Cintura",
                "cadera": "Circunferencia de Cadera",
                "pecho": "Circunferencia de Pecho",
                "brazo_derecho": "Brazo Derecho",
                "brazo_izquierdo": "Brazo Izquierdo",
                "muslo_derecho": "Muslo Derecho",
                "muslo_izquierdo": "Muslo Izquierdo",
                "pantorrilla": "Pantorrilla"
            }

            for campo, nombre in medidas.items():
                valor = ultimo_progreso.get(campo)
                if valor:
                    condiciones.append({
                        "nombre": nombre,
                        "valor": f"{float(valor):.1f} cm",
                        "estado": "normal",
                        "icon": "FiActivity"
                    })

        # Formatear fecha
        fecha_act = ultimo_progreso.get("fecha_registro") if ultimo_progreso else None
        str_fecha = fecha_act.strftime('%Y-%m-%d') if isinstance(fecha_act, datetime) else (str(fecha_act) if fecha_act else None)

        return jsonify({
            "condiciones": condiciones,
            "alergias": [],
            "medicamentos": [],
            "lesiones": [],
            "notas": ultimo_progreso.get("notas") if ultimo_progreso else None,
            "ultimaActualizacion": str_fecha
        }), 200

    except Exception as e:
        print(f"Error en get_user_health: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# =========================
# POST HEALTH
# =========================

@user_health_bp.route('/api/user/health', methods=['POST'])
@jwt_required()
def update_user_health():
    try:
        db = get_db()
        user_id = ObjectId(get_jwt_identity())
        miembro = db.miembros.find_one({"id_usuario": user_id})
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404

        data = request.json

        nuevo_progreso = {
            "id_miembro": miembro["_id"],
            "peso": float(data.get('peso')) if data.get('peso') else None,
            "cintura": float(data.get('cintura')) if data.get('cintura') else None,
            "cadera": float(data.get('cadera')) if data.get('cadera') else None,
            "fecha_registro": datetime.now()
        }

        # IMC SIEMPRE CALCULADO CORRECTAMENTE
        if nuevo_progreso["peso"] and miembro.get("estatura"):
            nuevo_progreso["bmi"] = _calcular_bmi(
                nuevo_progreso["peso"],
                miembro["estatura"]
            )

        campos_extra = [
            'pecho', 'brazo_derecho', 'brazo_izquierdo',
            'muslo_derecho', 'muslo_izquierdo', 'pantorrilla', 'notas'
        ]

        for campo in campos_extra:
            if campo in data and data.get(campo):
                if campo == 'notas':
                    nuevo_progreso[campo] = str(data.get(campo))
                else:
                    nuevo_progreso[campo] = float(data.get(campo))

        db.progreso_fisico.insert_one(nuevo_progreso)

        return jsonify({
            "message": "Datos de salud actualizados correctamente"
        }), 201

    except Exception as e:
        print(f"Error en update_user_health: {e}")
        return jsonify({"error": str(e)}), 500