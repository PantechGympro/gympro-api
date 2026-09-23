"""
compartido/citas_lectura.py — Consulta de citas para el miembro y el entrenador.

Recepción crea la cita (ver recepcionista/recepcionista_routes.py) y guarda en
Mongo tanto `client_id_pg` como `trainer_id_pg` (ambos son el id de Usuario en
PostgreSQL). Hasta ahora nada leía esos dos campos de vuelta: ni el miembro ni
el entrenador tenían forma de ver una cita que recepción les agendó, solo
existía en la vista de recepción. Este módulo cierra ese hueco con un
endpoint de solo lectura por rol, filtrando por el mismo campo que ya se
guardaba al crear la cita.

Endpoints:
    GET /api/user/citas    — citas del miembro autenticado
    GET /api/trainer/citas — citas del entrenador autenticado

Ambos devuelven la hora tal cual se capturó ("time": "HH:MM", "date":
"YYYY-MM-DD"), sin zona horaria propia — así la hora que ve recepción, el
miembro y el entrenador es siempre la misma cadena, sin conversión de por
medio que pueda desalinearla entre web y móvil.
"""
from flask import Blueprint, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.mongo import get_db
from app.utils.tenant import require_tenant

citas_miembro_bp   = Blueprint("citas_miembro",   __name__)
citas_entrenador_bp = Blueprint("citas_entrenador", __name__)


def _serialize(doc: dict) -> dict:
    """Convierte ObjectId a texto; el resto de los campos ya son JSON-serializables."""
    out = {}
    for k, v in doc.items():
        out[k] = str(v) if k == "_id" else v
    return out


def _mis_citas(campo_filtro: str):
    """
    Citas del gimnasio del usuario autenticado donde `campo_filtro`
    (client_id_pg o trainer_id_pg) coincide con su id de Usuario en PG.
    Ordenadas por fecha/hora para que la próxima cita aparezca primero.
    """
    db      = get_db()
    gym_id  = g.tenant_id
    user_id = int(get_jwt_identity())

    citas = list(
        db.citas.find({
            "id_gimnasio_pg": gym_id,
            campo_filtro:     user_id,
        }).sort([("date", 1), ("time", 1)])
    )
    return jsonify({"citas": [_serialize(c) for c in citas]}), 200


@citas_miembro_bp.route("/citas", methods=["GET"])
@jwt_required()
@require_tenant
def citas_miembro():
    """Citas agendadas para el miembro autenticado, en cualquier estado."""
    return _mis_citas("client_id_pg")


@citas_entrenador_bp.route("/citas", methods=["GET"])
@jwt_required()
@require_tenant
def citas_entrenador():
    """Citas agendadas con el entrenador autenticado, en cualquier estado."""
    return _mis_citas("trainer_id_pg")
