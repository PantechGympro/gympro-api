"""
superadmin/usuarios.py — Gestión de usuarios de la plataforma e impersonación.

Endpoints:
    GET  /api/superadmin/usuarios               todos los usuarios con filtros
    GET  /api/superadmin/usuarios/<id>          detalle de usuario
    POST /api/superadmin/usuarios/<id>/impersonate  genera token temporal como ese usuario
    PATCH  /api/superadmin/usuarios/<id>/toggle  activar / desactivar usuario (lógico)
    DELETE /api/superadmin/usuarios/<id>        eliminación PERMANENTE (PG + purga Mongo)
"""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, create_access_token, get_jwt_identity, set_access_cookies
from datetime import timedelta

from app.extensions import db
from app.mongo import get_db
from app.models.pg.usuario import Usuario
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.rol import Rol
from app.utils.security import require_role
from app.utils.alcance import registrar_evento
from app.constants.roles import normalizar_rol, ROL_SUPERADMIN, ROL_PROPIETARIO, ROL_MIEMBRO, ROL_ENTRENADOR

usuarios_admin_bp = Blueprint("usuarios_admin", __name__)

# Duración máxima del token de impersonación — corto por seguridad
_IMPERSONATE_TTL = timedelta(hours=1)


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@usuarios_admin_bp.route("/usuarios", methods=["GET"])
@jwt_required()
@require_role("superadmin")
def listar_usuarios():
    """
    Lista todos los usuarios de la plataforma con filtros y paginación.

    Query params:
        gym_id   int  — filtrar por gimnasio
        rol      str  — filtrar por nombre de rol
        activo   bool
        q        str  — buscar por nombre o email (ILIKE)
        page     int
        per_page int
    """
    gym_id_param = request.args.get("gym_id", type=int)
    rol_param    = request.args.get("rol")
    activo_param = request.args.get("activo")
    q_param      = request.args.get("q", "").strip()
    page         = max(1, int(request.args.get("page", 1)))
    per_page     = min(100, int(request.args.get("per_page", 25)))

    query = Usuario.query.join(Rol, isouter=True).join(Gimnasio, isouter=True)

    if gym_id_param is not None:
        query = query.filter(Usuario.id_gimnasio == gym_id_param)
    if rol_param:
        # Comparación normalizada (SCRUM-107): el filtro acepta el valor
        # canónico exacto ("Entrenador", "owner_gym", ...) y también alias
        # o variantes de mayúsculas, en vez de exigir una coincidencia
        # exacta sensible a mayúsculas.
        query = query.filter(db.func.lower(Rol.nombre) == normalizar_rol(rol_param).lower())
    if activo_param is not None:
        query = query.filter(Usuario.activo == (activo_param.lower() == "true"))
    if q_param:
        like = f"%{q_param}%"
        query = query.filter(
            db.or_(
                Usuario.nombre.ilike(like),
                Usuario.email.ilike(like),
            )
        )

    query    = query.order_by(Usuario.created_at.desc())
    paginado = query.paginate(page=page, per_page=per_page, error_out=False)

    items = []
    for u in paginado.items:
        items.append({
            "id":          u.id,
            "nombre":      u.nombre,
            "email":       u.email,
            "activo":      u.activo,
            "rol":         u.rol.nombre if u.rol else None,
            "id_gimnasio": u.id_gimnasio,
            "gimnasio":    u.gimnasio.nombre if u.gimnasio else None,
            "created_at":  u.created_at.isoformat() if u.created_at else None,
        })

    return jsonify({
        "usuarios": items,
        "total":    paginado.total,
        "page":     page,
        "pages":    paginado.pages,
        "per_page": per_page,
    }), 200


@usuarios_admin_bp.route("/usuarios/<int:user_id>", methods=["GET"])
@jwt_required()
@require_role("superadmin")
def detalle_usuario(user_id: int):
    """Devuelve perfil completo de un usuario."""
    u = Usuario.query.get_or_404(user_id)
    return jsonify({
        "id":          u.id,
        "nombre":      u.nombre,
        "email":       u.email,
        "activo":      u.activo,
        "rol":         u.rol.nombre if u.rol else None,
        "id_gimnasio": u.id_gimnasio,
        "gimnasio":    u.gimnasio.nombre if u.gimnasio else None,
        # SCRUM-223: el plan vigente es el de la organización, no el de
        # Gimnasio.plan (que ya no se actualiza -- ver utils/capacidad.py).
        "plan":        (u.gimnasio.organizacion.plan if u.gimnasio and u.gimnasio.organizacion else None),
        "created_at":  u.created_at.isoformat() if u.created_at else None,
    }), 200


@usuarios_admin_bp.route("/usuarios/<int:user_id>/impersonate", methods=["POST"])
@jwt_required()
@require_role("superadmin")
def impersonate(user_id: int):
    """
    Genera un JWT de corta duración (1 h) con la identidad del usuario indicado.
    Permite al superadmin diagnosticar problemas desde la perspectiva de ese usuario
    sin conocer su contraseña.

    El token generado incluye el claim 'impersonated_by' con el ID del superadmin
    para auditoría.

    Response:
        { "access_token": "...", "user": {...}, "expira_en": "1h" }
    """
    target = Usuario.query.get_or_404(user_id)
    if not target.activo:
        return jsonify({"msg": "No se puede impersonar un usuario inactivo."}), 403

    superadmin_id = get_jwt_identity()
    rol_nombre    = target.rol.nombre if target.rol else "Desconocido"

    # SCRUM-223: mismo criterio que el login real (auth/routes.py) --
    # el plan vigente es el de la organización, no Gimnasio.plan.
    if target.gimnasio and target.gimnasio.organizacion:
        _plan = target.gimnasio.organizacion.plan
    else:
        _plan = "basico"
    plan  = _plan.value if hasattr(_plan, "value") else str(_plan)

    claims = {
        "email":           target.email,
        "role":            rol_nombre,
        "id_gimnasio":     target.id_gimnasio,
        "plan":            plan,
        "access_level":    "premium" if plan in ("pro", "enterprise") else "basico",
        "perfil_completo": True,
        "peso_inicial":    None,
        "fuente":          "pg",
        "impersonated_by": superadmin_id,   # auditoría
    }

    token = create_access_token(
        identity=str(target.id),
        additional_claims=claims,
        expires_delta=_IMPERSONATE_TTL,
    )

    respuesta = jsonify({
        "access_token": token,
        "expira_en":    "1h",
        "user": {
            "id":          target.id,
            "nombre":      target.nombre,
            "email":       target.email,
            "role":        rol_nombre,
            "id_gimnasio": target.id_gimnasio,
            "gimnasio":    target.gimnasio.nombre if target.gimnasio else None,
            "plan":        plan,
        },
    })
    # Bug encontrado al revisar este endpoint: era el ÚNICO lugar que emite
    # un access token y NO lo refleja también en la cookie HttpOnly (login
    # y /auth/refresh sí lo hacen, ver auth/routes.py). JWT_TOKEN_LOCATION
    # = ["headers", "cookies"] revisa headers primero, así que el token de
    # localStorage que inyecta el frontend normalmente gana -- pero dejar
    # la cookie del superadmin vigente y sin reemplazar es un estado
    # inconsistente que no debería existir: cualquier código que alguna
    # vez verifique el JWT solo por cookie (o un proxy/caché que la
    # reenvíe) seguiría viendo al superadmin, no al usuario impersonado.
    set_access_cookies(respuesta, token)
    return respuesta, 200


@usuarios_admin_bp.route("/usuarios/<int:user_id>/toggle", methods=["PATCH"])
@jwt_required()
@require_role("superadmin")
def toggle_usuario(user_id: int):
    """
    Activa o desactiva un usuario individualmente.
    No permite desactivarse a sí mismo.
    """
    superadmin_id = int(get_jwt_identity())
    if user_id == superadmin_id:
        return jsonify({"msg": "No puedes desactivar tu propia cuenta."}), 403

    u = Usuario.query.get_or_404(user_id)
    u.activo = not u.activo
    db.session.commit()

    accion = "activado" if u.activo else "desactivado"
    return jsonify({
        "msg":    f"Usuario {accion}.",
        "id":     u.id,
        "email":  u.email,
        "activo": u.activo,
    }), 200


# ────────────────────────────────────────────────────────────────────────────────────
# ELIMINACIÓN PERMANENTE (hard delete)
#
# MongoDB no tiene integridad referencial: a diferencia del DELETE de PG
# (que sólo arrastra las FKs declaradas como CASCADE/SET NULL -- ya
# verificado para `usuarios.id` en todo el esquema), cada colección Mongo
# que apunta a este usuario hay que purgarla a mano o queda como "dato
# fantasma" que sigue contando en dashboards, reportes y validaciones de
# capacidad del gimnasio.
# ────────────────────────────────────────────────────────────────────────────────────

def _purgar_miembro_mongo(mdb, usuario_id: int) -> dict:
    """
    Purga todo rastro Mongo de un Miembro antes de borrar su fila en PG.

    Alcance deliberado: sólo datos PROPIOS del miembro. Las entradas de
    `rutinas` que el miembro tiene asignadas por un entrenador (plantillas
    compartidas, referenciadas via `miembro_rutina`/`rutinas_asignadas`)
    NUNCA se tocan aquí -- sólo se borran las entradas de `rutinas` que
    el propio miembro posee (`id_miembro`), replicando la cascada
    rutina->rutina_dias->rutina_ejercicios que ya usa trainer_routes.py.
    """
    counts = {}
    miembro_doc = mdb.miembros.find_one({"id_usuario_pg": usuario_id})
    if not miembro_doc:
        return counts
    mid = miembro_doc["_id"]

    rutina_ids = [r["_id"] for r in mdb.rutinas.find({"id_miembro": mid}, {"_id": 1})]
    if rutina_ids:
        dia_ids = [d["_id"] for d in mdb.rutina_dias.find(
            {"id_rutina": {"$in": rutina_ids}}, {"_id": 1}
        )]
        if dia_ids:
            counts["rutina_ejercicios"] = mdb.rutina_ejercicios.delete_many(
                {"id_rutina_dia": {"$in": dia_ids}}
            ).deleted_count
        counts["rutina_dias"] = mdb.rutina_dias.delete_many(
            {"id_rutina": {"$in": rutina_ids}}
        ).deleted_count
    counts["rutinas"] = mdb.rutinas.delete_many({"id_miembro": mid}).deleted_count

    for coleccion in (
        "asistencias", "dietas", "entrenamientos_realizados",
        "miembro_membresia", "pagos", "sesiones", "progreso_fisico",
        "consumo_recetas", "miembro_rutina",
    ):
        counts[coleccion] = mdb[coleccion].delete_many({"id_miembro": mid}).deleted_count

    for coleccion in ("pt_solicitudes", "mensajes_chat", "rutinas_asignadas"):
        counts[coleccion] = mdb[coleccion].delete_many(
            {"id_miembro_pg": usuario_id}
        ).deleted_count

    counts["evaluaciones_entrenador"] = mdb.evaluaciones_entrenador.delete_many(
        {"id_miembro_pg": usuario_id}
    ).deleted_count

    counts["ventas"] = mdb.ventas.delete_many(
        {"$or": [{"id_miembro": mid}, {"id_miembro_pg": usuario_id}]}
    ).deleted_count

    counts["miembros"] = mdb.miembros.delete_one({"_id": mid}).deleted_count
    return counts


def _purgar_entrenador_mongo(mdb, usuario_id: int) -> dict:
    """
    Purga todo rastro Mongo de un Entrenador antes de borrar su fila en PG.

    Alcance deliberado: NO borra las plantillas `rutinas` que el entrenador
    haya creado -- un miembro puede seguir teniéndolas asignadas aunque el
    entrenador ya no exista, y romper ese plan sería más dañino que dejar
    la plantilla como contenido histórico sin dueño. Por la misma razón no
    se toca `miembros`: la asignación vigente vive en `pt_solicitudes`
    (fuente de verdad, ver GET /profile de este mismo blueprint), que sí
    se purga abajo.
    """
    counts = {
        "perfil_entrenador": mdb.perfil_entrenador.delete_one(
            {"id_entrenador_pg": usuario_id}
        ).deleted_count,
    }
    for coleccion in (
        "certificaciones_entrenador", "logros_entrenador",
        "evaluaciones_entrenador", "pt_solicitudes", "mensajes_chat",
        "sesiones",
    ):
        counts[coleccion] = mdb[coleccion].delete_many(
            {"id_entrenador_pg": usuario_id}
        ).deleted_count
    return counts


@usuarios_admin_bp.route("/usuarios/<int:user_id>", methods=["DELETE"])
@jwt_required()
@require_role("superadmin")
def eliminar_usuario(user_id: int):
    """
    Elimina PERMANENTEMENTE un usuario: borra su fila en PostgreSQL y purga
    manualmente cada colección MongoDB que lo referencia.

    Pensado para limpiar cuentas de prueba (miembros/staff) sin dejar datos
    fantasma que sigan contando en las operaciones reales del gimnasio --
    a diferencia de /toggle, que sólo desactiva. NO es reversible.

    Reglas de seguridad:
      - No se puede eliminar la propia cuenta.
      - No se puede eliminar a otro superadmin.
      - No se puede eliminar a un Propietario desde aquí: arrastraría la
        orfandad de todo su gimnasio/organización, que es un alcance
        distinto (borrar un gimnasio completo) no cubierto por este
        endpoint.
    """
    superadmin_id = int(get_jwt_identity())
    if user_id == superadmin_id:
        return jsonify({"msg": "No puedes eliminar tu propia cuenta."}), 403

    u = Usuario.query.get_or_404(user_id)
    rol_nombre = normalizar_rol(u.rol.nombre) if u.rol else None

    if rol_nombre == ROL_SUPERADMIN:
        return jsonify({"msg": "No se puede eliminar a otro superadmin."}), 403
    if rol_nombre == ROL_PROPIETARIO:
        return jsonify({
            "msg": "No se puede eliminar a un Propietario desde aquí: "
                   "arrastraría la orfandad de todo su gimnasio. Si necesitas "
                   "borrar un gimnasio de prueba completo, es una operación aparte.",
        }), 409

    # Snapshot para la bitácora -- tras el commit el registro ya no existe.
    snapshot = {
        "nombre":      u.nombre,
        "email":       u.email,
        "rol":         rol_nombre,
        "id_gimnasio": u.id_gimnasio,
    }

    mdb     = get_db()
    purgado = {}
    if rol_nombre == ROL_MIEMBRO:
        purgado = _purgar_miembro_mongo(mdb, user_id)
    elif rol_nombre == ROL_ENTRENADOR:
        purgado = _purgar_entrenador_mongo(mdb, user_id)

    # Notificaciones: aplica a cualquier rol (destinatario por id_usuario_pg).
    purgado["notificaciones"] = mdb.notificaciones.delete_many(
        {"id_usuario_pg": user_id}
    ).deleted_count

    db.session.delete(u)
    registrar_evento(
        superadmin_id,
        "superadmin.usuario_eliminado",
        id_gimnasio=snapshot["id_gimnasio"],
        detalle={"usuario_eliminado": snapshot, "documentos_mongo_purgados": purgado},
    )
    db.session.commit()

    return jsonify({
        "msg":     f"Usuario {snapshot['email']} eliminado permanentemente.",
        "id":      user_id,
        "purgado": purgado,
    }), 200
