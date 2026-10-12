from flask import Blueprint, request, jsonify, current_app, g
from flask_jwt_extended import jwt_required, get_jwt_identity
from werkzeug.utils import secure_filename
from datetime import datetime
import os

from app.mongo import get_db
from app.models.pg.usuario import Usuario
from app.extensions import db as pg_db
from app.utils.tenant import require_tenant

user_profile_bp = Blueprint('user_profile', __name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@user_profile_bp.route('/api/user/profile', methods=['GET'])
@jwt_required()
@require_tenant
def get_user_profile():
    try:
        mdb        = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id

        # Datos base desde PG
        usuario = Usuario.query.get(user_pg_id)
        if not usuario:
            return jsonify({"error": "Usuario no encontrado"}), 404

        miembro = mdb.miembros.find_one({
            "id_usuario_pg":  user_pg_id,
            "id_gimnasio_pg": gym_id
        })
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404

        total_entrenamientos = mdb.asistencias.count_documents({"id_miembro": miembro["_id"]})

        fecha_registro = miembro.get("fecha_registro")
        if isinstance(fecha_registro, str):
            try:   fecha_registro = datetime.strptime(fecha_registro[:10], "%Y-%m-%d").date()
            except: fecha_registro = None
        elif isinstance(fecha_registro, datetime):
            fecha_registro = fecha_registro.date()

        meses_activo = ((datetime.now().date() - fecha_registro).days // 30) if fecha_registro else 0

        fn = miembro.get("fecha_nacimiento")
        if isinstance(fn, datetime):     fn_str = fn.strftime('%d/%m/%Y')
        elif isinstance(fn, str) and fn:
            try:    fn_str = datetime.strptime(fn[:10], "%Y-%m-%d").strftime('%d/%m/%Y')
            except: fn_str = fn
        else:   fn_str = ""

        sexo = miembro.get("sexo", "")
        genero_str = {"M": "Masculino", "F": "Femenino", "Masculino": "Masculino",
                      "Femenino": "Femenino"}.get(sexo, sexo or "")

        # Peso "actual": último registro de progreso_fisico; si no hay, peso_inicial.
        ultimo = mdb.progreso_fisico.find_one(
            {"id_miembro": miembro["_id"], "peso": {"$ne": None}},
            sort=[("fecha_registro", -1)],
        )
        peso     = (ultimo or {}).get("peso") or miembro.get("peso_inicial")
        estatura = miembro.get("estatura")

        return jsonify({
            "nombre":              usuario.nombre,
            "email":               usuario.email,
            "telefono":            miembro.get("telefono", ""),
            "fechaNacimiento":     fn_str,
            "direccion":           miembro.get("direccion", ""),
            "genero":              genero_str,
            "peso":                f"{peso} kg" if peso else "No registrado",
            "altura":              f"{estatura} m" if estatura else "No registrado",
            "objetivo":            miembro.get("objetivo", "Tonificación muscular"),
            "nivelExperiencia":    miembro.get("nivel_experiencia", "Intermedio"),
            "fotoPerfil":          miembro.get("foto_perfil"),
            "mesesActivo":         meses_activo,
            "totalEntrenamientos": total_entrenamientos,
            # Campos de onboarding extendido
            "condicionesMedicas":  miembro.get("condiciones_medicas", []),
            "medicamentos":        miembro.get("medicamentos", ""),
            "alergias":            miembro.get("alergias", ""),
            "lesiones":            miembro.get("lesiones_previas", ""),
            "nivelActividad":      miembro.get("nivel_actividad", ""),
            "diasDisponibles":     miembro.get("dias_disponibles", ""),
            "horasSueno":          miembro.get("horas_sueno", ""),
            "fuma":                miembro.get("fuma", False),
            "alcohol":             miembro.get("alcohol", ""),
            "onboardingCompletado": miembro.get("onboarding_completado", False),
            # SCRUM-102: se escribian desde complete-profile/PUT pero nunca se
            # devolvian -- el indicador de completitud necesita leerlos de vuelta.
            "contactoEmergenciaNombre":    miembro.get("contacto_emergencia_nombre", ""),
            "contactoEmergenciaTelefono":  miembro.get("contacto_emergencia_telefono", ""),
            "unidadMedida":                miembro.get("unidad_medida", ""),
        }), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_profile_bp.route('/api/user/profile-completion', methods=['GET'])
@jwt_required()
@require_tenant
def get_profile_completion():
    """
    SCRUM-102: indicador de completitud de perfil.

    El calculo vive UNA sola vez aqui; web y movil consumen este mismo
    endpoint, asi que el porcentaje es idéntico en ambas plataformas por
    construccion (no por disciplina de mantener dos copias sincronizadas).

    El bloque de información médica es OPCIONAL -dato sensible bajo la
    LFPDPPP, requiere consentimiento explicito antes de guardar (ver
    PUT /api/user/health/medical)- y NO cuenta para el porcentaje: el
    perfil puede llegar a 100% sin el.
    """
    try:
        mdb        = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id

        usuario = Usuario.query.get(user_pg_id)
        miembro = mdb.miembros.find_one({
            "id_usuario_pg":  user_pg_id,
            "id_gimnasio_pg": gym_id,
        })
        if not usuario or not miembro:
            return jsonify({"error": "Usuario/Miembro no encontrado"}), 404

        progreso_actual = mdb.progreso_fisico.find_one(
            {"id_miembro": miembro["_id"]}, sort=[("fecha_registro", -1)],
        ) or {}
        medidas_iniciales = miembro.get("medidas_iniciales") or {}

        def _tiene_medida(campo):
            """Misma estrategia de búsqueda que user_body_progress._get_medida:
            campo directo del último registro > anidado en 'medidas' > inicial."""
            return bool(
                progreso_actual.get(campo)
                or (progreso_actual.get("medidas") or {}).get(campo)
                or medidas_iniciales.get(campo)
            )

        def _pend(campo, label):
            return {"campo": campo, "label": label}

        # ── Bloque 1: información personal ──────────────────────────────
        p_personal = []
        if not (usuario.nombre or "").strip():
            p_personal.append(_pend("nombre", "Nombre completo"))
        if not (miembro.get("telefono") or "").strip():
            p_personal.append(_pend("telefono", "Teléfono"))
        if not miembro.get("fecha_nacimiento"):
            p_personal.append(_pend("fecha_nacimiento", "Fecha de nacimiento"))
        if not miembro.get("foto_perfil"):
            p_personal.append(_pend("foto", "Fotografía de perfil"))

        # ── Bloque 2: métricas corporales ───────────────────────────────
        p_metricas = []
        if not (progreso_actual.get("peso") or miembro.get("peso_inicial")):
            p_metricas.append(_pend("peso", "Peso"))
        if not miembro.get("estatura"):
            p_metricas.append(_pend("estatura", "Talla / estatura"))
        if not (_tiene_medida("cintura") and _tiene_medida("cadera") and _tiene_medida("pecho")):
            p_metricas.append(_pend("medidas", "Medidas corporales (cintura, cadera, pecho)"))

        # ── Bloque 3: información médica (OPCIONAL, con consentimiento) ──
        # El consentimiento es la puerta de TODO el bloque, no campo por
        # campo: si no se ha otorgado, los tres pendientes aparecen juntos
        # (coincide con que PUT /api/user/health/medical lo exige una sola
        # vez, antes de guardar cualquier dato médico).
        consentimiento = bool((miembro.get("consentimiento_datos_medicos") or {}).get("aceptado"))
        p_medico = []
        if not consentimiento:
            p_medico = [
                _pend("condiciones", "Condiciones médicas relevantes"),
                _pend("lesiones", "Lesiones previas"),
                _pend("contacto_emergencia", "Contacto de emergencia"),
            ]

        # ── Bloque 4: preferencias de entrenamiento ─────────────────────
        p_prefs = []
        if not (miembro.get("objetivo") or "").strip():
            p_prefs.append(_pend("objetivo", "Objetivo de entrenamiento"))
        if not (miembro.get("dias_disponibles") or ""):
            p_prefs.append(_pend("disponibilidad", "Disponibilidad"))
        if not (miembro.get("unidad_medida") or "").strip():
            p_prefs.append(_pend("unidad_medida", "Unidad de medida"))

        bloques = [
            {"id": "personal", "nombre": "Información personal", "obligatorio": True,
             "pendientes": p_personal},
            {"id": "metricas", "nombre": "Métricas corporales", "obligatorio": True,
             "pendientes": p_metricas},
            {"id": "medico", "nombre": "Información médica (opcional)", "obligatorio": False,
             "requiereConsentimiento": True, "consentimientoOtorgado": consentimiento,
             "pendientes": p_medico},
            {"id": "preferencias", "nombre": "Preferencias de entrenamiento", "obligatorio": True,
             "pendientes": p_prefs},
        ]

        # Solo los bloques obligatorios entran al porcentaje (4 + 3 + 3
        # campos): el médico puede quedar vacío y el perfil llega a 100%.
        total_obligatorios      = 4 + 3 + 3
        pendientes_obligatorios = len(p_personal) + len(p_metricas) + len(p_prefs)
        porcentaje = round((total_obligatorios - pendientes_obligatorios) / total_obligatorios * 100)

        return jsonify({
            "porcentaje": porcentaje,
            "completo":   pendientes_obligatorios == 0,
            "bloques":    bloques,
        }), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_profile_bp.route('/api/user/profile', methods=['PUT'])
@jwt_required()
@require_tenant
def update_user_profile():
    try:
        mdb        = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id

        usuario = Usuario.query.get(user_pg_id)
        miembro = mdb.miembros.find_one({
            "id_usuario_pg":  user_pg_id,
            "id_gimnasio_pg": gym_id
        })
        if not usuario or not miembro:
            return jsonify({"error": "Usuario/Miembro no encontrado"}), 404

        if request.is_json:
            data = request.json or {}
            file = None
        else:
            data = request.form
            file = request.files.get('foto')

        # Actualizar PG (nombre / email)
        if data.get('nombre'): usuario.nombre = data['nombre']
        if data.get('email'):
            existing = Usuario.query.filter_by(email=data['email']).first()
            if existing and existing.id != user_pg_id:
                return jsonify({"error": "El email ya está en uso"}), 400
            usuario.email = data['email']
        pg_db.session.commit()

        # Actualizar miembro en Mongo
        update_miembro = {}
        if data.get('telefono'):       update_miembro['telefono'] = data['telefono']
        if 'direccion' in data:        update_miembro['direccion'] = data.get('direccion') or ""
        if data.get('fechaNacimiento'):
            # Acepta dd/mm/aaaa (web/móvil) o aaaa-mm-dd (input date nativo).
            fn_raw = str(data['fechaNacimiento']).strip()
            parsed = None
            for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
                try:
                    parsed = datetime.strptime(fn_raw, fmt)
                    break
                except ValueError:
                    continue
            if parsed:
                update_miembro['fecha_nacimiento'] = parsed
        if data.get('genero'):
            gmap = {"Masculino": "Masculino", "Femenino": "Femenino", "Otro": "Otro",
                    "Prefiero no decir": "Otro"}
            if data['genero'] in gmap: update_miembro['sexo'] = gmap[data['genero']]
        if data.get('peso'):
            try:    update_miembro['peso_inicial'] = float(str(data['peso']).replace('kg', '').strip())
            except: pass
        if data.get('altura'):
            try:    update_miembro['estatura'] = float(str(data['altura']).replace('m', '').strip())
            except: pass
        # Objetivos y nivel (editables desde el perfil de la app)
        if data.get('objetivo'):         update_miembro['objetivo']          = data['objetivo']
        if data.get('nivelExperiencia'): update_miembro['nivel_experiencia'] = data['nivelExperiencia']
        if data.get('nivelActividad'):   update_miembro['nivel_actividad']   = data['nivelActividad']
        # Contacto de emergencia (from CompleteProfile)
        if data.get('contacto_emergencia_nombre'):
            update_miembro['contacto_emergencia_nombre'] = data['contacto_emergencia_nombre']
        if data.get('contacto_emergencia_telefono'):
            update_miembro['contacto_emergencia_telefono'] = data['contacto_emergencia_telefono']
        # Preferencia de unidad de medida (SCRUM-102): 'metrico' (kg/cm) o
        # 'imperial' (lb/in). Es una eleccion explicita del miembro, por eso
        # no se asume un valor por defecto en el calculo de completitud.
        if data.get('unidadMedida'):
            update_miembro['unidad_medida'] = data['unidadMedida']
        # Disponibilidad (SCRUM-102): antes solo se podia fijar una vez, en
        # el wizard de complete-profile (ver mas abajo). El indicador de
        # completitud exige poder resolver este pendiente sin repetir el
        # onboarding completo.
        if data.get('diasDisponibles'):
            update_miembro['dias_disponibles'] = data['diasDisponibles']
        # Desnormalizar nombre en Mongo también
        if data.get('nombre'):
            update_miembro['nombre'] = data['nombre']
            update_miembro['email']  = usuario.email

        # Procesar foto
        foto_final = miembro.get("foto_perfil")
        if file and allowed_file(file.filename):
            filename        = secure_filename(file.filename)
            unique_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
            upload_folder   = "/app/storage/uploads"
            os.makedirs(upload_folder, exist_ok=True)
            file.save(os.path.join(upload_folder, unique_filename))
            update_miembro['foto_perfil'] = unique_filename
            foto_final = unique_filename

        if update_miembro:
            mdb.miembros.update_one({"_id": miembro["_id"]}, {"$set": update_miembro})

        return jsonify({
            "message": "Perfil actualizado correctamente",
            "profile": {
                "nombre":    usuario.nombre,
                "email":     usuario.email,
                "fotoPerfil":foto_final
            }
        }), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_profile_bp.route('/api/user/complete-profile', methods=['POST'])
@jwt_required()
@require_tenant
def complete_user_profile():
    """
    Onboarding inicial del miembro (wizard 4 pasos).
    Persiste salud, objetivos, hábitos y medidas en el doc miembro de MongoDB.
    Al terminar registra la primera entrada en progreso_fisico.
    """
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

        update = {}

        # ── Paso 1: Datos personales ─────────────────────────────────────────
        if data.get("sexo"):         update["sexo"] = data["sexo"]
        if data.get("telefono"):     update["telefono"] = data["telefono"]
        if data.get("fechaNacimiento"):
            try:
                update["fecha_nacimiento"] = datetime.strptime(data["fechaNacimiento"], "%Y-%m-%d")
            except Exception:
                pass
        if data.get("estatura"):
            try:
                val = float(data["estatura"])
                update["estatura"] = val / 100 if val > 3 else val  # normalizar a metros
            except Exception:
                pass
        if data.get("contactoEmergenciaNombre"):
            update["contacto_emergencia_nombre"] = data["contactoEmergenciaNombre"]
        if data.get("contactoEmergenciaTelefono"):
            update["contacto_emergencia_telefono"] = data["contactoEmergenciaTelefono"]

        # ── Paso 2: Salud ────────────────────────────────────────────────────
        if "condicionesMedicas" in data:
            update["condiciones_medicas"] = data["condicionesMedicas"]   # list[str]
        if data.get("medicamentos") is not None:
            update["medicamentos"] = data["medicamentos"]
        if data.get("alergias") is not None:
            update["alergias"] = data["alergias"]
        if data.get("lesiones") is not None:
            update["lesiones_previas"] = data["lesiones"]
        if data.get("embarazada") is not None:
            update["embarazada"] = bool(data["embarazada"])
        if data.get("notas") is not None:
            update["notas_medicas"] = data["notas"]

        # ── Paso 3: Objetivos y hábitos ──────────────────────────────────────
        if data.get("objetivo"):          update["objetivo"]          = data["objetivo"]
        if data.get("nivelExperiencia"):  update["nivel_experiencia"] = data["nivelExperiencia"]
        if data.get("diasSemana"):        update["dias_disponibles"]  = data["diasSemana"]
        if data.get("horasSueno"):        update["horas_sueno"]       = data["horasSueno"]
        if data.get("nivelActividad"):    update["nivel_actividad"]   = data["nivelActividad"]
        if data.get("fuma") is not None:  update["fuma"]              = bool(data["fuma"])
        if data.get("alcohol"):           update["alcohol"]           = data["alcohol"]

        # ── Paso 4: Medidas corporales ───────────────────────────────────────
        if data.get("peso"):
            try:
                update["peso_inicial"] = float(data["peso"])
            except Exception:
                pass

        campos_medidas = {
            "pecho":          "pecho",
            "cintura":        "cintura",
            "cadera":         "cadera",
            "brazoDerecho":   "brazo_derecho",
            "brazoIzquierdo": "brazo_izquierdo",
            "musloDerecho":   "muslo_derecho",
            "musloIzquierdo": "muslo_izquierdo",
            "pantorrilla":    "pantorrilla",
        }
        medidas_final = {}
        for campo_js, campo_db in campos_medidas.items():
            if data.get(campo_js):
                try:
                    medidas_final[campo_db] = float(data[campo_js])
                except Exception:
                    pass

        if medidas_final:
            update["medidas_iniciales"] = medidas_final
            # Crear primera entrada en progreso_fisico
            mdb.progreso_fisico.insert_one({
                "id_miembro":     miembro["_id"],
                "id_gimnasio_pg": gym_id,
                "fecha_registro": datetime.utcnow(),
                "peso":           update.get("peso_inicial"),
                "medidas":        medidas_final,
                "tipo":           "inicial",
                "notas":          "Registro inicial de onboarding",
            })

        update["onboarding_completado"] = True
        update["fecha_onboarding"]      = datetime.utcnow()

        mdb.miembros.update_one({"_id": miembro["_id"]}, {"$set": update})

        return jsonify({"message": "Perfil completado exitosamente", "ok": True}), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_profile_bp.route('/api/user/profile/photo', methods=['POST'])
@jwt_required()
@require_tenant
def upload_profile_photo():
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

        file = request.files.get('foto')
        if not file or not allowed_file(file.filename):
            return jsonify({"error": "Archivo inválido"}), 400

        filename        = secure_filename(file.filename)
        unique_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
        # Debe guardarse en el MISMO directorio que sirve /api/uploads/<archivo>
        # (/app/storage/uploads). Antes se guardaba en static/uploads y la foto
        # nunca se mostraba (404) en perfil, sidebar ni fichas del entrenador.
        upload_folder   = "/app/storage/uploads"
        os.makedirs(upload_folder, exist_ok=True)
        file.save(os.path.join(upload_folder, unique_filename))

        mdb.miembros.update_one({"_id": miembro["_id"]},
                                {"$set": {"foto_perfil": unique_filename}})
        return jsonify({
            "message":   "Foto actualizada correctamente",
            "fotoPerfil":unique_filename
        }), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500
