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
    """Devuelve los datos del gimnasio del owner autenticado."""
    gym = Gimnasio.query.get(g.tenant_id)
    if not gym:
        return jsonify({"msg": "Gimnasio no encontrado"}), 404

    data = gym.to_dict()
    # Adjuntar la foto de perfil del propietario (Usuario autenticado).
    try:
        owner = Usuario.query.get(int(get_jwt_identity()))
        fp = getattr(owner, "foto_perfil", None) if owner else None
        data["owner_foto"]   = fp if (fp and fp.startswith("data:image")) else None
        data["owner_nombre"] = owner.nombre if owner else None
    except Exception:
        data["owner_foto"] = None
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

    data = request.get_json() or {}
    EDITABLES = ["nombre", "email_contacto", "telefono", "tipo_gimnasio"]
    updated = []
    for field in EDITABLES:
        if field in data:
            setattr(gym, field, data[field])
            updated.append(field)

    if not updated:
        return jsonify({"msg": "Sin campos para actualizar"}), 400

    db.session.commit()
    return jsonify({"msg": "Perfil actualizado", **gym.to_dict()}), 200
