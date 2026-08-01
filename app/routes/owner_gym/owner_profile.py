"""
owner_gym/owner_profile.py — Perfil y configuración del Gimnasio.

Endpoints:
    GET  /api/owner_gym/perfil        Datos del gimnasio propio
    PUT  /api/owner_gym/perfil        Actualizar datos del gimnasio
"""
from flask import Blueprint, jsonify, request, g
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.extensions import db
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.usuario import Usuario
from app.utils.tenant import require_tenant
from app.utils.security import require_role

owner_profile_bp = Blueprint("owner_profile", __name__)


@owner_profile_bp.route("/perfil", methods=["GET"])
@jwt_required()
@require_role("owner_gym")
@require_tenant
def get_perfil():
    """
    Datos del gimnasio y de su propietario.

    La app tiene dos pantallas distintas alimentadas por este endpoint:
    'Mi Perfil' (la persona) y 'Perfil del Gym' (el negocio). Antes solo se
    devolvían el nombre y la foto del dueño, así que 'Mi Perfil' terminaba
    mostrando los datos del gimnasio. Ahora el propietario viaja completo en
    su propio bloque y el tipo de gimnasio incluye su etiqueta legible.
    """
    from app.utils.gym_types import GYM_TYPES

    gym = Gimnasio.query.get(g.tenant_id)
    if not gym:
        return jsonify({"msg": "Gimnasio no encontrado"}), 404

    data = gym.to_dict()

    # Etiqueta legible del tipo: la interfaz no debe mostrar 'gimnasio_tradicional'.
    tipo = data.get("tipo_gimnasio") or "gimnasio_tradicional"
    data["tipo_gimnasio_label"] = GYM_TYPES.get(tipo, {}).get("label", "Gimnasio Tradicional")

    # Catálogo de tipos válidos, para que la app ofrezca una lista y no un
    # campo de texto libre donde se puede escribir cualquier cosa.
    data["tipos_disponibles"] = [
        {"value": clave, "label": conf.get("label", clave)}
        for clave, conf in GYM_TYPES.items()
    ]

    # ── Propietario ───────────────────────────────────────────────────────────
    try:
        owner = Usuario.query.get(int(get_jwt_identity()))
    except Exception:
        owner = None

    if owner:
        fp = getattr(owner, "foto_perfil", None)
        data["propietario"] = {
            "id":          owner.id,
            "nombre":      owner.nombre,
            "email":       owner.email,
            "telefono":    getattr(owner, "telefono", None),
            "rol":         owner.rol.nombre if getattr(owner, "rol", None) else "Owner",
            "activo":      owner.activo,
            "foto_perfil": fp if (fp and fp.startswith("data:image")) else None,
            "created_at":  owner.created_at.isoformat() if owner.created_at else None,
        }
    else:
        data["propietario"] = None

    # Se conservan por compatibilidad con pantallas que aún los leen.
    data["owner_foto"]   = (data["propietario"] or {}).get("foto_perfil")
    data["owner_nombre"] = (data["propietario"] or {}).get("nombre")

    return jsonify(data), 200


@owner_profile_bp.route("/perfil", methods=["PUT"])
@jwt_required()
@require_role("owner_gym")
@require_tenant
def update_perfil():
    """
    Actualiza los datos editables del gimnasio.
    Body JSON (todos opcionales):
        {
            "nombre":         "Mi Gym",
            "email_contacto": "gym@mail.com",
            "telefono":       "555-1234",
            "tipo_gimnasio":  "crossfit"
        }
    """
    gym = Gimnasio.query.get(g.tenant_id)
    if not gym:
        return jsonify({"msg": "Gimnasio no encontrado"}), 404

    from app.utils.gym_types import GYM_TYPES

    data = request.get_json() or {}

    # El tipo debe pertenecer al catálogo del SaaS: de él dependen las etiquetas
    # y los módulos que se activan, así que un valor libre dejaría al gimnasio
    # con una configuración inexistente.
    if "tipo_gimnasio" in data:
        tipo = (data.get("tipo_gimnasio") or "").strip()
        if tipo and tipo not in GYM_TYPES:
            return jsonify({
                "msg": "Tipo de gimnasio no válido",
                "tipos_validos": list(GYM_TYPES.keys()),
            }), 400

    EDITABLES = ["nombre", "email_contacto", "telefono", "tipo_gimnasio"]
    updated = []
    for field in EDITABLES:
        if field in data:
            setattr(gym, field, data[field])
            updated.append(field)

    if not updated:
        return jsonify({"msg": "Sin campos para actualizar"}), 400

    db.session.commit()

    salida = gym.to_dict()
    salida["tipo_gimnasio_label"] = GYM_TYPES.get(
        salida.get("tipo_gimnasio") or "", {}
    ).get("label", "Gimnasio Tradicional")
    return jsonify({"msg": "Perfil actualizado", **salida}), 200
