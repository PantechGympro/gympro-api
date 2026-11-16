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
        # Estos 4 campos tienen DOS vias de escritura con formatos distintos:
        #   - complete_user_profile() (onboarding, user_profile.py) guarda lo
        #     que mande el formulario tal cual -- hoy texto libre desde el
        #     wizard web ("Polen, latex"), pero el campo acepta list[str] en
        #     el payload igual (ver docstring de ese endpoint).
        #   - update_user_medical() (PUT /api/user/health/medical, en este
        #     archivo) SIEMPRE normaliza a string separado por comas antes
        #     de guardar.
        # O sea Mongo puede tener aqui una lista O un string segun por donde
        # se haya guardado la ultima vez -- hacer `.split(",")` a secas
        # revienta con 500 (AttributeError) en cuanto el valor es una lista.
        # _a_lista() acepta cualquiera de las dos formas (y cualquier otra
        # cosa rara) sin tronar.
        def _a_lista(valor):
            if isinstance(valor, list):
                return [str(x).strip() for x in valor if str(x).strip()]
            if isinstance(valor, str):
                return [s.strip() for s in valor.split(",") if s.strip()]
            return []

        conds_medicas = _a_lista(miembro.get("condiciones_medicas", []))
        if "Ninguna" in conds_medicas:
            conds_medicas = []

        alergias_list     = _a_lista(miembro.get("alergias", ""))
        medicamentos_list = _a_lista(miembro.get("medicamentos", ""))
        lesiones_list      = _a_lista(miembro.get("lesiones_previas", ""))

        consentimiento_medico = miembro.get("consentimiento_datos_medicos") or {}

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
            "ultimaActualizacion": str_fecha,
            # SCRUM-102: el bloque médico del indicador de completitud los
            # necesita de vuelta -- antes solo se escribían, nunca se leían.
            "contactoEmergenciaNombre":    miembro.get("contacto_emergencia_nombre", ""),
            "contactoEmergenciaTelefono":  miembro.get("contacto_emergencia_telefono", ""),
            "consentimientoDatosMedicos":  bool(consentimiento_medico.get("aceptado")),
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

        # IMC SIEMPRE CALCULADO CORRECTAMENTE. Se guarda como "imc" (no
        # "bmi"): es el campo que leen los modelos de IA y los reportes de
        # entrenador/dueño; guardarlo como "bmi" los deja sin este dato.
        if nuevo_progreso["peso"] and miembro.get("estatura"):
            nuevo_progreso["imc"] = _calcular_bmi(
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


@user_health_bp.route('/api/user/health/medical', methods=['PUT'])
@jwt_required()
@require_tenant
def update_user_medical():
    """
    Actualiza la información médica del miembro desde la app.

    Body JSON (todos opcionales — se actualiza solo lo enviado):
        condicionesMedicas : list[str] | str  -> miembro.condiciones_medicas (lista)
        alergias           : list[str] | str  -> miembro.alergias            (CSV)
        medicamentos       : list[str] | str  -> miembro.medicamentos        (CSV)
        lesiones           : list[str] | str  -> miembro.lesiones_previas     (CSV)
        contactoEmergenciaNombre    : str      -> miembro.contacto_emergencia_nombre
        contactoEmergenciaTelefono  : str      -> miembro.contacto_emergencia_telefono
        consentimiento : bool (SCRUM-102, requerido para guardar lo anterior)
            La información médica es dato sensible bajo la LFPDPPP: su captura
            es opcional y requiere consentimiento explícito. Si se envía algún
            dato médico (condicionesMedicas/alergias/medicamentos/lesiones/
            contacto de emergencia) sin consentimiento=true, se rechaza con 400
            en vez de guardarlo silenciosamente.

    Se guarda alergias/medicamentos/lesiones como texto separado por comas para
    ser consistente con el parseo del GET (que hace split por comas).
    """
    try:
        db         = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        miembro    = db.miembros.find_one({"id_usuario_pg": user_pg_id, "id_gimnasio_pg": gym_id})
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404

        data = request.json or {}

        def _to_list(v):
            if isinstance(v, list):
                return [str(x).strip() for x in v if str(x).strip()]
            return [s.strip() for s in str(v or "").split(",") if s.strip()]

        CAMPOS_MEDICOS = ("condicionesMedicas", "alergias", "medicamentos", "lesiones",
                          "contactoEmergenciaNombre", "contactoEmergenciaTelefono")
        envia_dato_medico = any(campo in data for campo in CAMPOS_MEDICOS)
        if envia_dato_medico and not data.get("consentimiento"):
            return jsonify({
                "error": "Se requiere tu consentimiento explícito para guardar información médica.",
                "code": "CONSENTIMIENTO_REQUERIDO",
            }), 400

        update = {}
        if "condicionesMedicas" in data:
            update["condiciones_medicas"] = _to_list(data["condicionesMedicas"])
        if "alergias" in data:
            update["alergias"] = ", ".join(_to_list(data["alergias"]))
        if "medicamentos" in data:
            update["medicamentos"] = ", ".join(_to_list(data["medicamentos"]))
        if "lesiones" in data:
            update["lesiones_previas"] = ", ".join(_to_list(data["lesiones"]))
        if "contactoEmergenciaNombre" in data:
            update["contacto_emergencia_nombre"] = data["contactoEmergenciaNombre"]
        if "contactoEmergenciaTelefono" in data:
            update["contacto_emergencia_telefono"] = data["contactoEmergenciaTelefono"]

        if envia_dato_medico and data.get("consentimiento"):
            update["consentimiento_datos_medicos"] = {
                "aceptado": True,
                "fecha": datetime.now(),
            }

        if update:
            db.miembros.update_one({"_id": miembro["_id"]}, {"$set": update})

        return jsonify({"message": "Información médica actualizada correctamente"}), 200

    except Exception as e:
        print(f"Error en update_user_medical: {e}")
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
            "bmi":           progreso.get("imc"),
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
