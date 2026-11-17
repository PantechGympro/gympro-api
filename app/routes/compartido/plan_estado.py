"""
routes/compartido/plan_estado.py

GET /api/plan/estado — resumen del plan contratado y de cuánto cupo queda,
para que el panel avise ANTES de que un alta sea rechazada.

Roles: Propietario y Administrador (los que dan de alta miembros/staff).
Solo lectura; los topes se siguen haciendo cumplir en cada alta
(utils/capacidad.py), esto únicamente los hace visibles.
"""
from flask import Blueprint, g, jsonify
from flask_jwt_extended import jwt_required

from app.constants.roles import ROL_ADMINISTRADOR, ROL_PROPIETARIO, ROL_SUPERADMIN
from app.utils.capacidad import _plan_de, estado_capacidad, estado_capacidad_organizacion
from app.utils.security import require_role
from app.utils.tenant import require_tenant
from app.models.pg.gimnasio import Gimnasio

plan_estado_bp = Blueprint("plan_estado", __name__)


@plan_estado_bp.route("/api/plan/estado", methods=["GET"])
@jwt_required()
@require_tenant
@require_role(ROL_PROPIETARIO, ROL_ADMINISTRADOR, ROL_SUPERADMIN)
def plan_estado():
    gym_id = g.tenant_id
    plan = _plan_de(gym_id)
    cap = estado_capacidad(gym_id)
    gym = Gimnasio.query.get(gym_id)
    sucursales = (estado_capacidad_organizacion(gym.id_organizacion)
                  if gym and gym.id_organizacion else None)
    return jsonify({
        "plan": plan.nombre if plan else None,
        "funciones": (plan.limites or {}) if plan else {},
        "miembros": cap["miembros"],
        "staff": cap["staff"],
        "sucursales": sucursales,
    }), 200
