from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required
from datetime import datetime, timezone
import math

from app.mongo import get_db
from app.utils.tenant import get_tenant_filter, require_tenant

ventas_bp = Blueprint("ventas", __name__)


@ventas_bp.route("/api/ventas", methods=["POST"])
@jwt_required()
@require_tenant
def registrar_venta():
    """
    Registra una venta del punto de venta en la colección 'ventas'.
    Body JSON:
      {
        "items": [{"id": int, "nombre": str, "precio": float, "qty": int, "categoria": str}],
        "total": float,
        "metodo_pago": "Efectivo" | "Tarjeta"
      }
    """
    try:
        db   = get_db()
        data = request.get_json()

        items      = data.get("items", [])
        total      = data.get("total", 0)
        metodo     = data.get("metodo_pago", "Efectivo")
        id_gimnasio = g.tenant_id

        if not items:
            return jsonify({"error": "El carrito está vacío"}), 400

        if metodo not in ("Efectivo", "Tarjeta", "Transferencia"):
            return jsonify({"error": "Método de pago inválido"}), 400

        doc = {
            "items":          items,
            "total":          float(total),
            "metodo_pago":    metodo,
            "fecha":          datetime.now(timezone.utc),
            "id_gimnasio":    id_gimnasio,
            # Datos opcionales del comprador
            "id_miembro":     data.get("id_miembro"),
            "nombre_miembro": data.get("nombre_miembro", ""),
            # Datos de pago según método
            "numero_tarjeta": data.get("numero_tarjeta", ""),
            "referencia":     data.get("referencia", ""),
        }
        result = db.ventas.insert_one(doc)

        return jsonify({
            "id":             str(result.inserted_id),
            "total":          doc["total"],
            "metodo_pago":    doc["metodo_pago"],
            "items":          items,
            "fecha":          doc["fecha"].isoformat(),
            "nombre_miembro": doc["nombre_miembro"],
            "referencia":     doc["referencia"],
        }), 201

    except Exception as e:
        print(f"Error en registrar_venta: {e}")
        return jsonify({"error": "Error interno", "detalle": str(e)}), 500


@ventas_bp.route("/api/ventas", methods=["GET"])
@jwt_required()
def listar_ventas():
    """
    Lista ventas paginadas. Query params: page (default 1), per_page (default 10).
    """
    try:
        db            = get_db()
        tenant_filter = get_tenant_filter()
        page          = request.args.get("page", 1, type=int)
        per_page      = request.args.get("per_page", 10, type=int)
        skip          = (page - 1) * per_page

        filtro = {}
        if tenant_filter:
            filtro["id_gimnasio"] = tenant_filter["id_gimnasio"]

        total  = db.ventas.count_documents(filtro)
        cursor = db.ventas.find(filtro).sort("fecha", -1).skip(skip).limit(per_page)
        pages  = math.ceil(total / per_page) if total > 0 else 0

        ventas = []
        for v in cursor:
            ventas.append({
                "id":          str(v["_id"]),
                "total":       v.get("total", 0),
                "metodo_pago": v.get("metodo_pago"),
                "items":       v.get("items", []),
                "fecha":       v["fecha"].isoformat() if isinstance(v.get("fecha"), datetime) else str(v.get("fecha")),
            })

        return jsonify({"ventas": ventas, "total": total, "pages": pages, "page": page}), 200

    except Exception as e:
        print(f"Error en listar_ventas: {e}")
        return jsonify({"ventas": [], "total": 0}), 500
