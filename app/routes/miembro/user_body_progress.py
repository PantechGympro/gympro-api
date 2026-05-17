from flask import Blueprint, jsonify, request, g
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timedelta
from app.mongo import get_db
from app.utils.tenant import require_tenant

user_body_progress_bp = Blueprint('user_body_progress', __name__)

@user_body_progress_bp.route('/api/user/body-progress', methods=['GET'])
@jwt_required()
@require_tenant
def get_body_progress():
    """
    Obtiene el progreso corporal del usuario con datos reales
    """
    try:
        db         = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        miembro    = db.miembros.find_one({"id_usuario_pg": user_pg_id, "id_gimnasio_pg": gym_id})
        
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404
        
        # Obtener progreso histórico ordenado por fecha
        progresos = list(db.progreso_fisico.find(
            {"id_miembro": miembro["_id"]}
        ).sort("fecha_registro", -1))
        
        # Determinar valores actuales e iniciales
        if progresos:
            progreso_actual = progresos[0]
            progreso_inicial = progresos[-1]
            
            peso_actual = float(progreso_actual.get("peso", 0) or 0)
            peso_inicial = float(progreso_inicial.get("peso", 0) or 0)
        else:
            # Si no hay registros de progreso, usar datos del miembro
            peso_actual = float(miembro.get("peso_inicial", 0) or 0)
            peso_inicial = peso_actual
            progreso_actual = None
            
        # Validar estatura
        estatura = float(miembro.get("estatura", 1.7) or 1.7)
        if estatura == 0:
            estatura = 1.7
            
        # Calcular IMC
        imc = _calcular_imc(peso_actual, estatura)
        
        # Calcular grasa corporal y músculo usando fórmulas estándar
        sexo = miembro.get("sexo", "M")
        grasa_corporal_actual = _calcular_grasa_corporal(peso_actual, imc, sexo)
        grasa_corporal_inicial = _calcular_grasa_corporal(peso_inicial, _calcular_imc(peso_inicial, estatura), sexo)
        
        # Músculo es aproximadamente 100 - grasa - huesos (asumiendo ~15% huesos)
        musculo_actual = round(100 - grasa_corporal_actual - 15, 1)
        musculo_inicial = round(100 - grasa_corporal_inicial - 15, 1)
        
        # Establecer metas realistas
        peso_meta = _calcular_peso_meta(peso_inicial, imc, sexo)
        grasa_meta = _calcular_grasa_meta(sexo)
        musculo_meta = round(100 - grasa_meta - 15, 1)
        
        # 🆕 EXTRAER MEDIDAS CORPORALES DEL ÚLTIMO PROGRESO
        def _get_medida(progreso, campo):
            """Extrae una medida del progreso (diccionario), retorna 0 si no existe"""
            if not progreso:
                return 0
            valor = progreso.get(campo)
            return float(valor) if valor else 0
            
        body_metrics = {
            "peso": {
                "actual": round(peso_actual, 1),
                "inicial": round(peso_inicial, 1),
                "meta": round(peso_meta, 1)
            },
            "grasaCorporal": {
                "actual": round(grasa_corporal_actual, 1),
                "inicial": round(grasa_corporal_inicial, 1),
                "meta": round(grasa_meta, 1)
            },
            "musculo": {
                "actual": round(musculo_actual, 1),
                "inicial": round(musculo_inicial, 1),
                "meta": round(musculo_meta, 1)
            },
            "imc": round(imc, 1),
            "estatura": round(estatura, 2),
            
            # 🆕 AGREGAR TODAS LAS MEDIDAS CORPORALES
            "pecho": round(_get_medida(progreso_actual, 'pecho'), 1),
            "cintura": round(_get_medida(progreso_actual, 'cintura'), 1),
            "cadera": round(_get_medida(progreso_actual, 'cadera'), 1),
            "brazoDerecho": round(_get_medida(progreso_actual, 'brazo_derecho'), 1),
            "brazoIzquierdo": round(_get_medida(progreso_actual, 'brazo_izquierdo'), 1),
            "musloDerecho": round(_get_medida(progreso_actual, 'muslo_derecho'), 1),
            "musloIzquierdo": round(_get_medida(progreso_actual, 'muslo_izquierdo'), 1),
            "pantorrilla": round(_get_medida(progreso_actual, 'pantorrilla'), 1)
        }
        
        # Obtener progreso mensual real
        progreso_mensual = _obtener_progreso_mensual_real(db, miembro["_id"], peso_inicial, peso_meta)
        
        # Determinar género automáticamente
        gender = "male" if sexo == "M" else "female"
        
        return jsonify({
            "bodyMetrics": body_metrics,
            "progressHistory": progreso_mensual,
            "gender": gender,
            "hasDatos": len(progresos) > 0
        }), 200
        
    except Exception as e:
        print(f"Error en get_body_progress: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_body_progress_bp.route('/api/user/body-progress', methods=['POST'])
@jwt_required()
@require_tenant
def add_body_progress():
    """
    Agrega un nuevo registro de progreso corporal
    """
    try:
        db         = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        miembro    = db.miembros.find_one({"id_usuario_pg": user_pg_id, "id_gimnasio_pg": gym_id})
        
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404
            
        data = request.json
        
        # Validar datos requeridos
        if not data.get('peso'):
            return jsonify({"error": "El peso es requerido"}), 400
            
        # Crear nuevo registro
        nuevo_progreso = {
            "id_miembro": miembro["_id"],
            "peso": float(data.get('peso')),
            "cintura": float(data.get('cintura')) if data.get('cintura') else None,
            "cadera": float(data.get('cadera')) if data.get('cadera') else None,
            "fecha_registro": datetime.now()
        }
        
        # 🆕 AGREGAR CAMPOS ADICIONALES
        campos_opcionales = [
            'pecho', 'brazo_derecho', 'brazo_izquierdo',
            'muslo_derecho', 'muslo_izquierdo', 'pantorrilla'
        ]
        
        for campo in campos_opcionales:
            if campo in data and data[campo]:
                nuevo_progreso[campo] = float(data[campo])
                
        # Calcular BMI automáticamente
        estatura = float(miembro.get("estatura") or 0)
        bmi = None
        if estatura > 0 and nuevo_progreso["peso"]:
            bmi = round(_calcular_imc(nuevo_progreso["peso"], estatura), 2)
            nuevo_progreso["bmi"] = bmi
            
        db.progreso_fisico.insert_one(nuevo_progreso)
        
        return jsonify({
            "message": "Progreso registrado correctamente",
            "progreso": {
                "peso": nuevo_progreso["peso"],
                "bmi": bmi,
                "cintura": nuevo_progreso.get("cintura"),
                "cadera": nuevo_progreso.get("cadera"),
                "fecha": nuevo_progreso["fecha_registro"].strftime('%Y-%m-%d')
            }
        }), 201
        
    except Exception as e:
        print(f"Error en add_body_progress: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ============================================
# FUNCIONES AUXILIARES
# ============================================

def _calcular_imc(peso, estatura):
    try:
        if estatura > 0 and peso > 0:
            return peso / (estatura ** 2)
        return 0
    except:
        return 0

def _calcular_grasa_corporal(peso, imc, sexo):
    try:
        edad = 30
        sexo_valor = 1 if sexo == "M" else 0
        grasa = (1.20 * imc) + (0.23 * edad) - (10.8 * sexo_valor) - 5.4
        
        if sexo == "M":
            return max(5, min(35, round(grasa, 1)))
        else:
            return max(10, min(45, round(grasa, 1)))
    except:
        return 22 if sexo == "F" else 18

def _calcular_peso_meta(peso_inicial, imc_actual, sexo):
    if imc_actual >= 18.5 and imc_actual <= 24.9:
        return peso_inicial
    elif imc_actual > 24.9:
        return peso_inicial * 0.90
    else:
        return peso_inicial * 1.10

def _calcular_grasa_meta(sexo):
    if sexo == "M":
        return 15
    else:
        return 23

def _obtener_progreso_mensual_real(db, id_miembro, peso_inicial, peso_meta):
    try:
        now = datetime.now()
        meses = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
        
        # Fecha límite: hace 6 meses
        hace_6_meses = datetime.combine((now - timedelta(days=180)).date(), datetime.min.time())
        
        progresos = list(db.progreso_fisico.find({
            "id_miembro": id_miembro,
            "fecha_registro": {"$gte": hace_6_meses}
        }).sort("fecha_registro", 1))
        
        if not progresos:
            return []
            
        progreso_por_mes = {}
        diferencia_total = abs(peso_inicial - peso_meta) if peso_inicial != peso_meta else 1
        
        for progreso in progresos:
            # Obtener el mes de la fecha registrada
            fecha_reg = progreso.get("fecha_registro")
            if isinstance(fecha_reg, str):
                fecha_reg = datetime.strptime(fecha_reg[:10], "%Y-%m-%d")
                
            mes_num = fecha_reg.month - 1
            mes_nombre = meses[mes_num]
            
            peso_actual = float(progreso.get("peso", peso_inicial) or peso_inicial)
            diferencia_actual = abs(peso_inicial - peso_actual)
            porcentaje = min(100, (diferencia_actual / diferencia_total * 100))
            
            progreso_por_mes[mes_nombre] = {
                "mes": mes_nombre,
                "porcentaje": max(0, round(porcentaje))
            }
            
        resultado = []
        for i in range(6):
            mes_idx = (now.month - 6 + i) % 12
            mes_nombre = meses[mes_idx]
            
            if mes_nombre in progreso_por_mes:
                resultado.append(progreso_por_mes[mes_nombre])
                
        return resultado
        
    except Exception as e:
        print(f"Error en _obtener_progreso_mensual_real: {e}")
        return []