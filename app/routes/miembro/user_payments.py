from flask import Blueprint, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timedelta

from app.mongo import get_db
from app.utils.tenant import require_tenant

user_payments_bp = Blueprint('user_payments', __name__)


@user_payments_bp.route('/api/user/payments', methods=['GET'])
@jwt_required()
@require_tenant
def get_user_payments():
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

        # ── Pagos de membresía ───────────────────────────────────────────────
        pagos = list(mdb.pagos.find({"id_miembro": miembro["_id"]}).sort("fecha_pago", -1))

        pagos_formateados = []
        for pago in pagos:
            fecha_p = _a_fecha(pago.get("fecha_pago"))
            pagos_formateados.append({
                "id":      f"PAY-{str(pago['_id'])[-5:].upper()}",
                "date":    _texto_fecha(fecha_p),
                "concept": pago.get("concepto") or "Pago de membresía",
                "amount":  float(pago.get("monto", 0)),
                "method":  _format_payment_method(pago.get("metodo_pago")),
                "status":  "Completado",
                "type":    "membresia",
                "rawDate": _iso(fecha_p),
            })

        # ── Compras en el punto de venta ─────────────────────────────────────
        # El miembro ve en un mismo lugar lo que pagó por su membresía y lo que
        # compró en el gimnasio; sin esto parecía que las compras no se habían
        # registrado. Se buscan por ambos identificadores porque las ventas
        # conviven guardadas con el ObjectId del miembro o con su id de
        # PostgreSQL (ver GET /api/ventas).
        ventas = list(mdb.ventas.find({
            "id_gimnasio": gym_id,
            "$or": [
                {"id_miembro": miembro["_id"]},
                {"id_miembro_pg": user_pg_id},
                {"id_miembro": user_pg_id},
            ],
        }).sort("fecha", -1))

        for venta in ventas:
            fecha_v = _a_fecha(venta.get("fecha"))
            articulos = venta.get("items") or []
            cantidad = sum(int(i.get("qty") or i.get("cantidad") or 1) for i in articulos)
            if articulos:
                primero = articulos[0].get("nombre", "Producto")
                concepto = (primero if len(articulos) == 1
                            else f"{primero} y {len(articulos) - 1} más")
            else:
                concepto = "Compra en el gimnasio"
            pagos_formateados.append({
                "id":      f"POS-{str(venta['_id'])[-5:].upper()}",
                "date":    _texto_fecha(fecha_v),
                "concept": concepto,
                "amount":  float(venta.get("total", 0)),
                "method":  _format_payment_method(venta.get("metodo_pago")),
                "status":  "Completado",
                "type":    "producto",
                "items":   cantidad,
                "rawDate": _iso(fecha_v),
            })

        # Un solo orden cronológico para las dos fuentes
        pagos_formateados.sort(key=lambda p: p.get("rawDate") or "", reverse=True)

        total_membresias = sum(float(p.get("monto", 0)) for p in pagos)
        total_compras    = sum(float(v.get("total", 0)) for v in ventas)
        total_pagado     = total_membresias + total_compras

        ultimo_pago = pagos[0] if pagos else None

        membresia_activa = mdb.miembro_membresia.find_one({
            "id_miembro": miembro["_id"],
            "estado":     "Activa"
        })
        proximo_pago = None
        if membresia_activa and membresia_activa.get("fecha_fin"):
            fecha_f = membresia_activa["fecha_fin"]
            if isinstance(fecha_f, str):
                fecha_f = datetime.strptime(fecha_f[:10], "%Y-%m-%d")
            proximo_pago = (fecha_f - timedelta(days=3)).strftime('%d %b %Y')

        up_date = _a_fecha(ultimo_pago.get("fecha_pago")) if ultimo_pago else None

        return jsonify({
            "stats": {
                "totalPaid":       float(total_pagado),
                "totalMembresias": float(total_membresias),
                "totalCompras":    float(total_compras),
                "lastPayment": _texto_fecha(up_date) if up_date else "N/A",
                "nextPayment": proximo_pago or "No programado",
                "status":      "Al día" if membresia_activa else "Sin membresía"
            },
            "payments": pagos_formateados
        }), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_payments_bp.route('/api/user/payments/stats', methods=['GET'])
@jwt_required()
@require_tenant
def get_payment_stats():
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

        current_year  = datetime.now().year
        start_of_year = datetime(current_year, 1, 1)
        end_of_year   = datetime(current_year + 1, 1, 1)

        pipeline = [
            {"$match": {
                "id_miembro": miembro["_id"],
                "fecha_pago": {"$gte": start_of_year, "$lt": end_of_year}
            }},
            {"$group": {
                "_id":      {"$month": "$fecha_pago"},
                "total":    {"$sum": "$monto"},
                "cantidad": {"$sum": 1}
            }}
        ]
        monthly_stats = list(mdb.pagos.aggregate(pipeline))
        stats_dict    = {doc["_id"]: doc for doc in monthly_stats}

        meses = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
        return jsonify({
            "year": current_year,
            "monthly": [
                {
                    "mes":      meses[i - 1],
                    "total":    float(stats_dict[i]["total"])    if i in stats_dict else 0,
                    "cantidad": int(stats_dict[i]["cantidad"]) if i in stats_dict else 0
                }
                for i in range(1, 13)
            ]
        }), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


def _format_payment_method(metodo):
    return {
        "Efectivo":      "Efectivo",
        "Tarjeta":       "Tarjeta de crédito/débito",
        "Transferencia": "Transferencia bancaria",
        "PayPal":        "PayPal",
        "Mercado Pago":  "Mercado Pago",
    }.get(metodo, metodo or "")


# ── Fechas ───────────────────────────────────────────────────────────────────
# Las dos colecciones guardan la fecha de forma distinta: `pagos` a veces la
# tiene como texto (registros antiguos) y `ventas` como datetime. Estos tres
# ayudantes normalizan ambos casos para poder ordenarlas juntas.

_MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun',
          'jul', 'ago', 'sep', 'oct', 'nov', 'dic']


def _a_fecha(valor):
    """Devuelve un datetime a partir de un datetime o de un texto ISO."""
    if isinstance(valor, datetime):
        return valor
    if isinstance(valor, str) and valor:
        for corte, patron in ((19, "%Y-%m-%dT%H:%M:%S"), (10, "%Y-%m-%d")):
            try:
                return datetime.strptime(valor[:corte], patron)
            except ValueError:
                continue
    return None


def _texto_fecha(f):
    """'05 mar 2026'. Se arma a mano para no depender del locale del contenedor."""
    if not isinstance(f, datetime):
        return ""
    return f"{f.day:02d} {_MESES[f.month - 1]} {f.year}"


def _iso(f):
    return f.isoformat() if isinstance(f, datetime) else ""
