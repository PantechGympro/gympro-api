"""
app/routes/miembro/logros.py -- HU-26 / SCRUM-206: logros, rangos y medallas.

Vista de perfil completa (conseguidos + pendientes con progreso). El widget
"Logros recientes" del dashboard (GET /api/user/dashboard) usa el mismo motor
-- ver app/utils/logros.py y el comentario en user_dashboard._get_recent_achievements.
"""
from flask import Blueprint, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.mongo import get_db
from app.utils.tenant import require_tenant
from app.utils.logros import calcular_logros_miembro, ACHIEVEMENT_CATALOG

logros_bp = Blueprint('logros', __name__)


@logros_bp.route('/api/user/logros', methods=['GET'])
@jwt_required()
@require_tenant
def get_logros():
    try:
        mdb        = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        miembro    = mdb.miembros.find_one({"id_usuario_pg": user_pg_id, "id_gimnasio_pg": gym_id})
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404

        logros = calcular_logros_miembro(mdb, miembro["_id"], gym_id)

        conseguidos = [l for l in logros if l["conseguido"]]
        pendientes  = [l for l in logros if not l["conseguido"]]

        # "Siguiente logro" por familia: el pendiente de menor "objetivo"
        # dentro de cada familia -- el próximo nivel alcanzable, no
        # cualquier pendiente al azar.
        orden_familia = {c["id"]: c["orden"] for c in ACHIEVEMENT_CATALOG}
        siguientes = {}
        for l in pendientes:
            familia = l["familia"]
            if familia not in siguientes or orden_familia[l["id"]] < orden_familia[siguientes[familia]["id"]]:
                siguientes[familia] = l

        return jsonify({
            "logros": logros,
            "resumen": {
                "conseguidos": len(conseguidos),
                "total":       len(logros),
                "siguientes":  list(siguientes.values()),
            },
        }), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500
