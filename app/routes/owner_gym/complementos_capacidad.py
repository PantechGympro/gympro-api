"""
owner_gym/complementos_capacidad.py -- Comprar mas espacio para miembros.

Peticion explicita del usuario: que el Propietario pueda ampliar el tope
de miembros de su plan comprando lotes de tamano fijo (10/50/100/200/500),
cada uno con su propio precio, desde "Mi suscripcion".

El cobro en si lo hace el checkout generico de un solo pago que ya existe
en routes/pagos_online.py (contexto "lote_capacidad") -- este archivo solo
expone el catalogo y la lista/cancelacion de lo que el gimnasio ya compro.
Ver models/pg/complemento_capacidad.py para por que un lote es un cobro
unico atado al ciclo de facturacion, no un cargo recurrente aparte.

Endpoints (bajo /api/owner_gym):
  GET    /complementos-capacidad/catalogo   catalogo con precios (cualquier
                                             usuario autenticado, igual que
                                             GET /api/billing/planes)
  GET    /complementos-capacidad            lotes del gimnasio actual +
                                             cuanta capacidad extra suman hoy
  DELETE /complementos-capacidad/<id>       desactivar uno antes de que venza
                                             (Propietario/SuperAdmin, mismo
                                             candado que billing.py)
"""
from flask import Blueprint, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt

from app.extensions import db
from app.models.pg.complemento_capacidad import ComplementoCapacidad, catalogo_lotes
from app.utils.capacidad import extra_miembros_de
from app.utils.tenant import require_tenant
from app.constants.roles import ROL_PROPIETARIO, ROL_SUPERADMIN, normalizar_rol

complementos_capacidad_bp = Blueprint("complementos_capacidad", __name__)


def _es_admin(claims):
    return normalizar_rol(claims.get("role")) in (ROL_PROPIETARIO, ROL_SUPERADMIN)


@complementos_capacidad_bp.route("/complementos-capacidad/catalogo", methods=["GET"])
@jwt_required()
def catalogo():
    """Catalogo de tamanos y precios -- sin restriccion de rol, igual que /api/billing/planes."""
    return jsonify({"lotes": catalogo_lotes()}), 200


@complementos_capacidad_bp.route("/complementos-capacidad", methods=["GET"])
@jwt_required()
@require_tenant
def listar():
    """Lotes comprados por el gimnasio actual, mas recientes primero."""
    lotes = (ComplementoCapacidad.query
             .filter_by(id_gimnasio=g.tenant_id)
             .order_by(ComplementoCapacidad.creado_en.desc())
             .all())
    return jsonify({
        "lotes": [l.to_dict() for l in lotes],
        "extra_miembros_vigente": extra_miembros_de(g.tenant_id),
    }), 200


@complementos_capacidad_bp.route("/complementos-capacidad/<int:lote_id>", methods=["DELETE"])
@jwt_required()
@require_tenant
def cancelar(lote_id):
    """
    Desactiva un lote antes de que venza por su cuenta. No hay reembolso
    -- fue un cobro unico, no un cargo recurrente que se pueda detener a
    tiempo; el frontend avisa esto antes de confirmar.
    """
    claims = get_jwt()
    if not _es_admin(claims):
        return jsonify({"msg": "Solo el propietario puede administrar la capacidad del gimnasio"}), 403

    lote = ComplementoCapacidad.query.filter_by(id=lote_id, id_gimnasio=g.tenant_id).first()
    if not lote:
        return jsonify({"msg": "Lote no encontrado"}), 404

    lote.estado = "cancelado"
    db.session.commit()
    return jsonify({"msg": "Lote desactivado.", "lote": lote.to_dict()}), 200
