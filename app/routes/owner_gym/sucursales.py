"""
routes/owner_gym/sucursales.py — Gestión de sucursales y administradores
(HU-45 / SCRUM-216).

Quién usa este blueprint: únicamente Propietario. superadmin tiene su
propio panel (routes/superadmin/gimnasios.py) y Administrador no gestiona
sucursales ni a otros administradores -- solo opera dentro de la(s) que le
asignaron (ver POST /api/auth/sucursal-activa en auth/routes.py).

Alcance del Propietario sobre una sucursal: a propósito NO se resuelve
contra AsignacionUsuario (esa vía es para Administrador -- multi-sucursal,
reasignable en caliente, ver utils/alcance.py). Un Propietario es dueño de
TODA su Organizacion por definición del rol: hoy existe una sola fila de
asignacion_usuario por Propietario (apuntando a su gimnasio original, la
sede principal) y seguirá siendo así -- no se necesita una fila por cada
sucursal nueva para que pueda administrarlas. La pregunta real es
simplemente "¿esa sucursal es de mi organización?", resuelta de forma
directa sobre Gimnasio.id_organizacion (ver _mi_organizacion() más abajo).

Endpoints (prefijo /api/owner_gym, registrado en app/__init__.py):
    GET    /sucursales                         lista las sucursales de mi organización
    POST   /sucursales                         crea una sucursal nueva
    PUT    /sucursales/<id>                    edita datos de una sucursal
    PATCH  /sucursales/<id>/toggle              activa/desactiva una sucursal
    GET    /administradores                     lista administradores de mi organización
    POST   /administradores                     crea un administrador y sus sucursales asignadas
    PUT    /administradores/<id>/sucursales      reemplaza el conjunto de sucursales asignadas
    PATCH  /administradores/<id>/toggle          activa/desactiva un administrador

Nota de alcance deliberadamente fuera de este ticket: crear una sucursal
aquí NO crea una Suscripcion/FacturaSuscripcion nueva (el billing SaaS
gimnasio-paga-a-GymPro, ver billing.py/billing_stripe.py) -- solo copia,
si se pide, la configuración de PASARELAS DE COBRO de la sede principal
(con qué el gimnasio cobra a SUS miembros, ver pasarelas.py). Si el
negocio decide que cada sucursal debe pagar su propia suscripción SaaS,
eso es una decisión de producto que no estaba en el alcance de HU-45 tal
como se leyó, y se deja para una actividad aparte.
"""
from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.extensions import db
from app.models.pg.usuario import Usuario
from app.models.pg.rol import Rol
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.asignacion_usuario import AsignacionUsuario
from app.models.pg.pasarela_pago import ConfiguracionPasarela
from app.utils.tenant import require_tenant
from app.utils.security import require_role
from app.utils.alcance import crear_asignacion, sucursales_de_administrador, registrar_evento
from app.constants.roles import ROL_PROPIETARIO, ROL_ADMINISTRADOR

sucursales_bp = Blueprint("sucursales", __name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de alcance (ver docstring del módulo)
# ─────────────────────────────────────────────────────────────────────────────

def _mi_organizacion():
    """Organización del Propietario autenticado -- la de su propio gimnasio (g.tenant_id)."""
    gym = Gimnasio.query.get(g.tenant_id)
    return gym.id_organizacion if gym else None


def _sucursal_de_mi_organizacion(id_gimnasio, id_organizacion=None):
    """True si `id_gimnasio` pertenece a la misma organización que el Propietario autenticado."""
    org = id_organizacion if id_organizacion is not None else _mi_organizacion()
    if org is None:
        return False
    objetivo = Gimnasio.query.get(id_gimnasio)
    return objetivo is not None and objetivo.id_organizacion == org


def _ids_sucursales_de_mi_organizacion():
    org = _mi_organizacion()
    if org is None:
        return []
    filas = Gimnasio.query.filter_by(id_organizacion=org).with_entities(Gimnasio.id).all()
    return [fila.id for fila in filas]


def _rol_administrador():
    return Rol.query.filter_by(nombre=ROL_ADMINISTRADOR).first()


# ─────────────────────────────────────────────────────────────────────────────
# SUCURSALES
# ─────────────────────────────────────────────────────────────────────────────

@sucursales_bp.route("/sucursales", methods=["GET"])
@jwt_required()
@require_role(ROL_PROPIETARIO)
@require_tenant
def listar_sucursales():
    org = _mi_organizacion()
    if org is None:
        return jsonify([]), 200
    sucursales = Gimnasio.query.filter_by(id_organizacion=org).order_by(Gimnasio.id).all()
    return jsonify([s.to_dict() for s in sucursales]), 200


@sucursales_bp.route("/sucursales", methods=["POST"])
@jwt_required()
@require_role(ROL_PROPIETARIO)
@require_tenant
def crear_sucursal():
    """
    Crea una sucursal nueva dentro de la misma organización del Propietario.

    Body JSON:
        {
            "nombre": "Hyper Panchito Gym - Sucursal Norte",
            "email_contacto": "...", "telefono": "...", "tipo_gimnasio": "...",
            "plan": "basico",       // opcional, hereda el de la sede principal si se omite
            "heredar_cobro": true   // opcional, default true -- copia la configuración de
                                    // pasarelas de pago (PayPal/Mercado Pago) de la sede principal
        }
    """
    org = _mi_organizacion()
    if org is None:
        return jsonify({"msg": "No se pudo determinar tu organización"}), 500

    data   = request.get_json() or {}
    nombre = (data.get("nombre") or "").strip()
    if not nombre:
        return jsonify({"msg": "nombre es requerido"}), 400

    sede_principal = Gimnasio.query.filter_by(id_organizacion=org, es_sucursal_principal=True).first()
    if sede_principal is not None:
        plan_heredado = sede_principal.plan.value if hasattr(sede_principal.plan, "value") else sede_principal.plan
    else:
        plan_heredado = "basico"

    nueva = Gimnasio(
        nombre                = nombre,
        plan                   = (data.get("plan") or plan_heredado),
        activo                 = True,
        email_contacto         = (data.get("email_contacto") or "").strip() or None,
        telefono               = (data.get("telefono") or "").strip() or None,
        tipo_gimnasio          = (data.get("tipo_gimnasio") or "").strip()
                                  or (sede_principal.tipo_gimnasio if sede_principal else None),
        id_organizacion        = org,
        es_sucursal_principal  = False,
    )
    db.session.add(nueva)
    db.session.flush()  # necesita nueva.id

    # HU-45 AC: la sucursal nueva hereda la cuenta de cobro de la principal
    # -- se copian las credenciales YA CIFRADAS (Fernet, una sola llave para
    # toda la app -- ver utils/crypto.py), no un alta desde cero. Es una
    # copia puntual al momento de crear la sucursal, no un vínculo vivo: si
    # luego el Propietario cambia las credenciales de la principal, la
    # sucursal nueva conserva las que tenía al crearse (puede editar las
    # suyas por separado en /api/owner/pasarelas, ya scoped por g.tenant_id).
    heredar_cobro = bool(data.get("heredar_cobro", True))
    if heredar_cobro and sede_principal:
        for cfg in ConfiguracionPasarela.query.filter_by(id_gimnasio=sede_principal.id).all():
            db.session.add(ConfiguracionPasarela(
                id_gimnasio    = nueva.id,
                proveedor      = cfg.proveedor,
                modo           = cfg.modo,
                activo         = cfg.activo,
                credenciales   = cfg.credenciales,
                moneda         = cfg.moneda,
                titular_cuenta = cfg.titular_cuenta,
            ))

    registrar_evento(
        int(get_jwt_identity()), "sucursal.crear",
        id_organizacion=org, id_gimnasio=nueva.id,
        detalle={"nombre": nombre, "heredar_cobro": heredar_cobro and sede_principal is not None},
    )
    db.session.commit()
    return jsonify({"msg": "Sucursal creada", **nueva.to_dict()}), 201


@sucursales_bp.route("/sucursales/<int:id_gimnasio>", methods=["PUT"])
@jwt_required()
@require_role(ROL_PROPIETARIO)
@require_tenant
def actualizar_sucursal(id_gimnasio):
    if not _sucursal_de_mi_organizacion(id_gimnasio):
        return jsonify({"msg": "Sucursal no encontrada"}), 404

    sucursal = Gimnasio.query.get(id_gimnasio)
    data     = request.get_json() or {}

    for campo in ("nombre", "email_contacto", "telefono", "tipo_gimnasio"):
        if campo in data:
            valor = (data[campo] or "").strip() or None
            if campo == "nombre" and valor is None:
                return jsonify({"msg": "nombre no puede quedar vacío"}), 400
            setattr(sucursal, campo, valor)
    if data.get("plan"):
        sucursal.plan = data["plan"]

    db.session.commit()
    return jsonify({"msg": "Sucursal actualizada", **sucursal.to_dict()}), 200


@sucursales_bp.route("/sucursales/<int:id_gimnasio>/toggle", methods=["PATCH"])
@jwt_required()
@require_role(ROL_PROPIETARIO)
@require_tenant
def toggle_sucursal(id_gimnasio):
    """
    Activa/desactiva una sucursal.

    Guarda de eliminación (HU-45): la sede principal no puede desactivarse
    mientras existan otras sucursales activas en la misma organización --
    desactivarla dejaría a esas sucursales dependiendo implícitamente de
    una sede que ya no opera (p. ej. la cuenta de cobro que heredaron al
    crearse). No hay endpoint de borrado físico, a propósito: ninguna otra
    ruta de este proyecto borra una fila de Gimnasio (ver
    superadmin/gimnasios.py), siempre se desactiva.
    """
    if not _sucursal_de_mi_organizacion(id_gimnasio):
        return jsonify({"msg": "Sucursal no encontrada"}), 404

    sucursal = Gimnasio.query.get(id_gimnasio)

    if sucursal.activo and sucursal.es_sucursal_principal:
        otras_activas = Gimnasio.query.filter(
            Gimnasio.id_organizacion == sucursal.id_organizacion,
            Gimnasio.id != sucursal.id,
            Gimnasio.activo == True,  # noqa: E712 (comparación explícita, estilo del resto del proyecto)
        ).count()
        if otras_activas > 0:
            return jsonify({
                "msg": "No puedes desactivar la sede principal mientras existan otras sucursales activas",
            }), 409

    sucursal.activo = not sucursal.activo
    registrar_evento(
        int(get_jwt_identity()),
        "sucursal.activar" if sucursal.activo else "sucursal.desactivar",
        id_organizacion=sucursal.id_organizacion, id_gimnasio=sucursal.id,
    )
    db.session.commit()
    return jsonify({"msg": "Sucursal actualizada", "activo": sucursal.activo}), 200


# ─────────────────────────────────────────────────────────────────────────────
# ADMINISTRADORES
# ─────────────────────────────────────────────────────────────────────────────

@sucursales_bp.route("/administradores", methods=["GET"])
@jwt_required()
@require_role(ROL_PROPIETARIO)
@require_tenant
def listar_administradores():
    ids_sucursales = _ids_sucursales_de_mi_organizacion()
    rol = _rol_administrador()
    if not ids_sucursales or rol is None:
        return jsonify([]), 200

    administradores = Usuario.query.filter(
        Usuario.id_rol == rol.id,
        Usuario.id_gimnasio.in_(ids_sucursales),
    ).order_by(Usuario.nombre).all()

    salida = []
    for a in administradores:
        d = a.to_dict()
        d["sucursales"] = sucursales_de_administrador(a.id)
        salida.append(d)
    return jsonify(salida), 200


@sucursales_bp.route("/administradores", methods=["POST"])
@jwt_required()
@require_role(ROL_PROPIETARIO)
@require_tenant
def crear_administrador():
    """
    Crea un Administrador y le asigna una o varias sucursales de mi
    organización -- replica AsignacionUsuario desde el día uno (igual que
    crear_asignacion() hace para el resto de roles, ver owner_trainers.py).

    Body JSON:
        {
            "nombre": "...", "email": "...", "password": "...",
            "sucursales": [3, 5]   // ids de Gimnasio de MI organización;
                                   // el primero queda como su sucursal activa inicial
        }
    """
    data     = request.get_json() or {}
    nombre   = (data.get("nombre")   or "").strip()
    email    = (data.get("email")    or "").strip().lower()
    password = (data.get("password") or "").strip()
    sucursales_ids = data.get("sucursales") or []

    if not all([nombre, email, password]) or not sucursales_ids:
        return jsonify({"msg": "nombre, email, password y al menos una sucursal son requeridos"}), 400
    if not isinstance(sucursales_ids, list) or not all(isinstance(i, int) for i in sucursales_ids):
        return jsonify({"msg": "sucursales debe ser una lista de ids"}), 400

    org = _mi_organizacion()
    for sid in sucursales_ids:
        if not _sucursal_de_mi_organizacion(sid, org):
            return jsonify({"msg": f"La sucursal {sid} no pertenece a tu organización"}), 400

    if Usuario.query.filter_by(email=email).first():
        return jsonify({"msg": "El email ya está registrado"}), 409

    rol = _rol_administrador()
    if rol is None:
        return jsonify({"msg": "El rol Administrador no existe en la base de datos"}), 500

    nuevo = Usuario(
        nombre      = nombre,
        email       = email,
        id_rol      = rol.id,
        id_gimnasio = sucursales_ids[0],   # sucursal activa inicial
        activo      = True,
    )
    nuevo.set_password(password)
    db.session.add(nuevo)
    db.session.flush()  # necesita nuevo.id

    for sid in sucursales_ids:
        crear_asignacion(nuevo, gimnasio=Gimnasio.query.get(sid))

    registrar_evento(
        int(get_jwt_identity()), "administrador.crear",
        id_organizacion=org, id_gimnasio=sucursales_ids[0],
        detalle={"id_usuario": nuevo.id, "sucursales": sucursales_ids},
    )
    db.session.commit()
    return jsonify({"msg": "Administrador creado", **nuevo.to_dict(), "sucursales": sucursales_ids}), 201


@sucursales_bp.route("/administradores/<int:id_usuario>/sucursales", methods=["PUT"])
@jwt_required()
@require_role(ROL_PROPIETARIO)
@require_tenant
def reasignar_sucursales_administrador(id_usuario):
    """
    Reemplaza por completo el conjunto de sucursales asignadas a un
    Administrador (no un parche incremental -- manda la lista completa
    que debe quedar vigente).
    """
    rol = _rol_administrador()
    if rol is None:
        return jsonify({"msg": "El rol Administrador no existe en la base de datos"}), 500

    admin = Usuario.query.filter_by(id=id_usuario, id_rol=rol.id).first()
    ids_mi_org = _ids_sucursales_de_mi_organizacion()
    if admin is None or not any(sid in ids_mi_org for sid in sucursales_de_administrador(admin.id)):
        return jsonify({"msg": "Administrador no encontrado"}), 404

    data       = request.get_json() or {}
    nuevas_ids = data.get("sucursales") or []
    if not isinstance(nuevas_ids, list) or not nuevas_ids or not all(isinstance(i, int) for i in nuevas_ids):
        return jsonify({"msg": "sucursales debe ser una lista no vacía de ids"}), 400

    org = _mi_organizacion()
    for sid in nuevas_ids:
        if not _sucursal_de_mi_organizacion(sid, org):
            return jsonify({"msg": f"La sucursal {sid} no pertenece a tu organización"}), 400

    AsignacionUsuario.query.filter_by(id_usuario=admin.id, id_rol=rol.id).delete(synchronize_session=False)
    db.session.flush()
    for sid in nuevas_ids:
        crear_asignacion(admin, gimnasio=Gimnasio.query.get(sid))

    # Si su sucursal activa (la que ve en su sesión actual) ya no está en el
    # conjunto nuevo, se reasigna a la primera -- evita dejarlo con una
    # sucursal activa sobre la que ya no tiene alcance; el cambio surte
    # efecto en su próximo refresco de token (igual que /auth/refresh).
    if admin.id_gimnasio not in nuevas_ids:
        admin.id_gimnasio = nuevas_ids[0]

    registrar_evento(
        int(get_jwt_identity()), "administrador.reasignar_sucursales",
        id_organizacion=org, id_gimnasio=admin.id_gimnasio,
        detalle={"id_usuario": admin.id, "sucursales": nuevas_ids},
    )
    db.session.commit()
    return jsonify({"msg": "Sucursales reasignadas", "sucursales": nuevas_ids}), 200


@sucursales_bp.route("/administradores/<int:id_usuario>/toggle", methods=["PATCH"])
@jwt_required()
@require_role(ROL_PROPIETARIO)
@require_tenant
def toggle_administrador(id_usuario):
    rol = _rol_administrador()
    if rol is None:
        return jsonify({"msg": "El rol Administrador no existe en la base de datos"}), 500

    admin = Usuario.query.filter(
        Usuario.id == id_usuario, Usuario.id_rol == rol.id,
        Usuario.id_gimnasio.in_(_ids_sucursales_de_mi_organizacion()),
    ).first()
    if admin is None:
        return jsonify({"msg": "Administrador no encontrado"}), 404

    admin.activo = not admin.activo
    registrar_evento(
        int(get_jwt_identity()),
        "administrador.activar" if admin.activo else "administrador.desactivar",
        id_organizacion=_mi_organizacion(), id_gimnasio=admin.id_gimnasio,
        detalle={"id_usuario": admin.id},
    )
    db.session.commit()
    return jsonify({"msg": "Administrador actualizado", "activo": admin.activo}), 200
