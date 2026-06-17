from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime
from app.mongo import get_db
from app.utils.tenant import require_tenant

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
@require_tenant
def get_user_health():
    try:
        db         = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        miembro    = db.miembros.find_one({"id_usuario_pg": user_pg_id, "id_gimnasio_pg": gym_id})

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

        # MEDIDAS (buscar en campos directos, dict anidado y medidas_iniciales del miembro)
        campos_medidas = {
            "cintura":        "Circunferencia de Cintura",
            "cadera":         "Circunferencia de Cadera",
            "pecho":          "Circunferencia de Pecho",
            "brazo_derecho":  "Brazo Derecho",
            "brazo_izquierdo":"Brazo Izquierdo",
            "muslo_derecho":  "Muslo Derecho",
            "muslo_izquierdo":"Muslo Izquierdo",
            "pantorrilla_derecha":   "Pantorrilla Derecha",
            "pantorrilla_izquierda": "Pantorrilla Izquierda",
            "pantorrilla":    "Pantorrilla",  # legado (registros antiguos con una sola)
        }

        def _val_medida(campo):
            """Lee medida desde progreso directo → anidado → medidas_iniciales del miembro."""
            valor = (ultimo_progreso or {}).get(campo)
            if not valor:
                valor = ((ultimo_progreso or {}).get("medidas") or {}).get(campo)
            if not valor:
                valor = (miembro.get("medidas_iniciales") or {}).get(campo)
            return float(valor) if valor else None

        for campo, nombre in campos_medidas.items():
            v = _val_medida(campo)
            if v:
                condiciones.append({
                    "nombre": nombre,
                    "valor": f"{v:.1f} cm",
                    "estado": "normal",
                    "icon": "FiActivity"
                })

        # Formatear fecha
        fecha_act = (ultimo_progreso or {}).get("fecha_registro")
        str_fecha = fecha_act.strftime('%Y-%m-%d') if isinstance(fecha_act, datetime) else (str(fecha_act) if fecha_act else None)

        # ── Datos del onboarding (condiciones_medicas, alergias, etc.) ─────────
        conds_medicas = miembro.get("condiciones_medicas", [])
        if "Ninguna" in conds_medicas:
            conds_medicas = []

        alergias_raw = miembro.get("alergias", "")
        alergias_list = [a.strip() for a in alergias_raw.split(",") if a.strip()] if alergias_raw else []

        medicamentos_raw = miembro.get("medicamentos", "")
        medicamentos_list = [m.strip() for m in medicamentos_raw.split(",") if m.strip()] if medicamentos_raw else []

        lesiones_raw = miembro.get("lesiones_previas", "")
        lesiones_list = [l.strip() for l in lesiones_raw.split(",") if l.strip()] if lesiones_raw else []

        return jsonify({
            "condiciones":          condiciones,
            "condicionesMedicas":   conds_medicas,
            "alergias":             alergias_list,
            "medicamentos":         medicamentos_list,
            "lesiones":             lesiones_list,
            "nivelActividad":       miembro.get("nivel_actividad", ""),
            "objetivo":             miembro.get("objetivo", ""),
            "nivelExperiencia":     miembro.get("nivel_experiencia", ""),
            "diasDisponibles":      miembro.get("dias_disponibles", ""),
            "horasSueno":           miembro.get("horas_sueno", ""),
            "fuma":                 miembro.get("fuma", False),
            "alcohol":              miembro.get("alcohol", ""),
            "notas": (ultimo_progreso or {}).get("notas"),
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
@require_tenant
def update_user_health():
    try:
        db         = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        miembro    = db.miembros.find_one({"id_usuario_pg": user_pg_id, "id_gimnasio_pg": gym_id})
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
            'muslo_derecho', 'muslo_izquierdo',
            'pantorrilla_derecha', 'pantorrilla_izquierda', 'pantorrilla', 'notas'
        ]

        for campo in campos_extra:
            if campo in data and data.get(campo):
                if campo == 'notas':
                    nuevo_progreso[campo] = str(data.get(campo))
                else:
                    nuevo_progreso[campo] = float(data.get(campo))

        db.progreso_fisico.insert_one(nuevo_progreso)

        # ── Snapshot de analytics: historial_metricas ──────────────────────────
        # Colección denormalizada para modelos ML y dashboards de gym.
        # Incluye contexto de gimnasio y valores calculados para evitar joins.
        _append_historial_metricas(db, miembro, nuevo_progreso)

        return jsonify({
            "message": "Datos de salud actualizados correctamente"
        }), 201

    except Exception as e:
        print(f"Error en update_user_health: {e}")
        return jsonify({"error": str(e)}), 500


def _append_historial_metricas(db, miembro, progreso):
    """
    Escribe un snapshot de métricas en la colección 'historial_metricas'.
    Esta colección es append-only y está diseñada para analytics / ML.
    No bloquea si falla — el save principal ya fue exitoso.
    """
    try:
        sexo = miembro.get("sexo", "M")
        snapshot = {
            "id_miembro":    miembro["_id"],
            "id_gimnasio_pg": miembro.get("id_gimnasio_pg"),
            "timestamp":     progreso.get("fecha_registro") or datetime.now(),
            "genero":        sexo,
            # Métricas numéricas — None si no fue registrado en esta sesión
            "peso":          progreso.get("peso"),
            "bmi":           progreso.get("bmi"),
            "cintura":       progreso.get("cintura"),
            "cadera":        progreso.get("cadera"),
            "pecho":         progreso.get("pecho"),
            "brazo_derecho": progreso.get("brazo_derecho"),
            "brazo_izquierdo": progreso.get("brazo_izquierdo"),
            "muslo_izquierdo": progreso.get("muslo_izquierdo"),
            "pantorrilla":   progreso.get("pantorrilla"),
        }
        db.historial_metricas.insert_one(snapshot)
    except Exception as ex:
        print(f"[historial_metricas] No-bloqueante: {ex}")
