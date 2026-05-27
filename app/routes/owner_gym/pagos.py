from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required
from datetime import date
from dateutil.relativedelta import relativedelta
from bson import ObjectId
import math

from app.mongo import get_db
from app.models.pago import Pago
from app.models.miembro_membresia import MiembroMembresia
from app.models.miembro import Miembro
from app.models.pg.tipo_membresia import TipoMembresia
from app.utils.tenant import require_tenant
from app.utils.luhn import validar_luhn
from app.utils.tenant import get_tenant_filter

pagos_bp = Blueprint("pagos", __name__)

@pagos_bp.route("/api/pagos", methods=["POST"])
@jwt_required()
@require_tenant
def registrar_pago():
    try:
        mdb    = get_db()
        data   = request.json
        gym_id = g.tenant_id

        # 1. Validaciones básicas
        required = ["id_miembro", "id_membresia", "metodo_pago"]
        for field in required:
            if field not in data:
                return jsonify({"error": f"Falta el campo {field}"}), 400

        # 2. Validar TipoMembresia desde PG (id entero)
        try:
            tm = TipoMembresia.query.filter_by(
                id=int(data["id_membresia"]),
                id_gimnasio=gym_id,
                activo=True
            ).first()
        except (ValueError, TypeError):
            tm = None
        if not tm:
            return jsonify({"error": "Membresía no válida"}), 404

        # 3. Validar Miembro en Mongo
        miembro = Miembro.find_by_id(data["id_miembro"])
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404

        # 4. Validar tarjeta
        if data["metodo_pago"] == "Tarjeta":
            tarjeta = data.get("numero_tarjeta", "")
            if not validar_luhn(tarjeta):
                return jsonify({"error": "Número de tarjeta inválido"}), 400

        # 5. Crear el Pago con datos de PG TipoMembresia
        pago = Pago(
            id_miembro=miembro._id,
            monto=tm.precio,
            metodo_pago=data["metodo_pago"],
            concepto=f"Pago membresía {tm.nombre}",
            id_gimnasio=gym_id,
        )
        pago.save()

        # 6. Calcular fechas
        inicio   = date.today()
        duracion = int(tm.duracion_meses or 1)
        fin      = inicio + relativedelta(months=duracion)

        # 7. Expirar membresía activa anterior (si existe) antes de crear la nueva
        #    El índice único en id_miembro impediría insertar si no se elimina/actualiza.
        mdb.miembro_membresia.update_many(
            {"id_miembro": miembro._id, "estado": "Activa"},
            {"$set": {"estado": "Expirada"}}
        )

        # 8. Crear la nueva relación Miembro-Membresía (id_membresia = entero PG)
        mm = MiembroMembresia(
            id_miembro=miembro._id,
            id_membresia=tm.id,
            fecha_inicio=inicio.strftime('%Y-%m-%d'),
            fecha_fin=fin.strftime('%Y-%m-%d'),
            estado="Activa"
        )
        mm.save()

        return jsonify(pago.to_dict()), 201

    except Exception as e:
        print(f"Error en registrar_pago: {str(e)}")
        return jsonify({"error": "Error interno del servidor", "detalle": str(e)}), 500

@pagos_bp.route("/api/pagos", methods=["GET"])
@jwt_required()
def listar_pagos():
    try:
        db            = get_db()
        tenant_filter = get_tenant_filter()
        page          = request.args.get("page", 1, type=int)
        per_page      = 6
        skip          = (page - 1) * per_page

        filtro = {}
        if tenant_filter:
            filtro["id_gimnasio"] = tenant_filter["id_gimnasio"]

        total_pagos  = db.pagos.count_documents(filtro)
        pagos_cursor = db.pagos.find(filtro).sort("fecha_pago", -1).skip(skip).limit(per_page)
        pages        = math.ceil(total_pagos / per_page) if total_pagos > 0 else 0

        pagos_lista = []
        for p_data in pagos_cursor:
            p        = Pago(**p_data)
            dict_data = p.to_dict()
            dict_data["id_pago"] = str(p_data["_id"])
            pagos_lista.append(dict_data)

        return jsonify({
            "pagos": pagos_lista,
            "total": total_pagos,
            "pages": pages,
            "page":  page,
        }), 200
    except Exception as e:
        print(f"Error listando pagos: {e}")
        return jsonify({"pagos": [], "total": 0}), 500


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/pagos/categorias — Categorías únicas con ventas en este gimnasio
# ─────────────────────────────────────────────────────────────────────────────
@pagos_bp.route("/api/pagos/categorias", methods=["GET"])
@jwt_required()
@require_tenant
def listar_categorias_ventas():
    """Devuelve las categorías distintas presentes en ventas POS del gimnasio."""
    try:
        db     = get_db()
        gym_id = g.tenant_id

        pipeline = [
            {"$match": {"id_gimnasio": gym_id}},
            {"$unwind": "$items"},
            {"$group": {"_id": "$items.categoria"}},
            {"$match": {"_id": {"$ne": None}}},
            {"$sort": {"_id": 1}},
        ]
        cats = [r["_id"] for r in db.ventas.aggregate(pipeline) if r.get("_id")]
        return jsonify({"categorias": cats}), 200

    except Exception as e:
        print(f"Error en listar_categorias_ventas: {e}")
        return jsonify({"categorias": []}), 500


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/pagos/todos — Feed unificado: membresías + ventas POS
#   ?tipo=todos|membresia|venta   (default: todos)
#   ?categoria=<str>              (sólo cuando tipo=venta, filtra por categoría de ítem)
#   ?page=<int>                   (default: 1)
# ─────────────────────────────────────────────────────────────────────────────
@pagos_bp.route("/api/pagos/todos", methods=["GET"])
@jwt_required()
@require_tenant
def listar_todos_movimientos():
    """Feed paginado unificado de membresías y ventas POS con filtros opcionales."""
    try:
        db        = get_db()
        gym_id    = g.tenant_id
        page      = request.args.get("page", 1, type=int)
        tipo      = request.args.get("tipo", "todos")          # todos | membresia | venta
        categoria = (request.args.get("categoria") or "").strip()
        per_page  = 6
        skip      = (page - 1) * per_page

        # ── Construir pipeline según filtro de tipo ───────────────────────────
        if tipo == "membresia":
            # Solo pagos de membresías
            pipeline = [
                {"$match": {"id_gimnasio": gym_id}},
                {"$addFields": {
                    "_tipo":     {"$literal": "membresia"},
                    "_fecha_dt": {"$toDate": "$fecha_pago"},
                    "_monto":    "$monto",
                }},
                {"$sort": {"_fecha_dt": -1}},
                {"$facet": {
                    "metadata": [{"$count": "total"}],
                    "data":     [{"$skip": skip}, {"$limit": per_page}],
                }},
            ]
            agg   = list(db.pagos.aggregate(pipeline))

        elif tipo == "venta":
            # Solo ventas POS (con filtro opcional de categoría)
            venta_match: dict = {"id_gimnasio": gym_id}
            if categoria:
                venta_match["items.categoria"] = categoria

            pipeline = [
                {"$match": venta_match},
                {"$addFields": {
                    "_tipo":     {"$literal": "venta"},
                    "_fecha_dt": "$fecha",
                    "_monto":    "$total",
                }},
                {"$sort": {"_fecha_dt": -1}},
                {"$facet": {
                    "metadata": [{"$count": "total"}],
                    "data":     [{"$skip": skip}, {"$limit": per_page}],
                }},
            ]
            agg = list(db.ventas.aggregate(pipeline))

        else:
            # Todos: union de ambas colecciones
            pipeline = [
                {"$match": {"id_gimnasio": gym_id}},
                {"$addFields": {
                    "_tipo":     {"$literal": "membresia"},
                    "_fecha_dt": {"$toDate": "$fecha_pago"},
                    "_monto":    "$monto",
                }},
                {"$unionWith": {
                    "coll": "ventas",
                    "pipeline": [
                        {"$match": {"id_gimnasio": gym_id}},
                        {"$addFields": {
                            "_tipo":     {"$literal": "venta"},
                            "_fecha_dt": "$fecha",
                            "_monto":    "$total",
                        }},
                    ],
                }},
                {"$sort": {"_fecha_dt": -1}},
                {"$facet": {
                    "metadata": [{"$count": "total"}],
                    "data":     [{"$skip": skip}, {"$limit": per_page}],
                }},
            ]
            agg = list(db.pagos.aggregate(pipeline))

        total = agg[0]["metadata"][0]["total"] if agg and agg[0]["metadata"] else 0
        docs  = agg[0]["data"] if agg else []

        # ── Batch-lookup nombres para pagos de membresía ──────────────────────
        ids_lookup = set()
        for doc in docs:
            if doc.get("_tipo") == "membresia" and doc.get("id_miembro"):
                try:
                    ids_lookup.add(ObjectId(str(doc["id_miembro"])))
                except Exception:
                    pass

        nombre_cache: dict = {}
        if ids_lookup:
            for m in db.miembros.find({"_id": {"$in": list(ids_lookup)}}):
                full = f"{m.get('nombre', '')} {m.get('apellido', '')}".strip()
                nombre_cache[str(m["_id"])] = full or "—"

        # ── Serializar ────────────────────────────────────────────────────────
        movimientos = []
        for doc in docs:
            doc_tipo  = doc.get("_tipo", "membresia")
            fecha     = doc.get("_fecha_dt")
            fecha_str = fecha.isoformat() if hasattr(fecha, "isoformat") else str(fecha or "")

            if doc_tipo == "membresia":
                id_m   = doc.get("id_miembro")
                nombre = nombre_cache.get(str(id_m), "—") if id_m else "—"
                movimientos.append({
                    "id":          str(doc["_id"]),
                    "tipo":        "membresia",
                    "titulo":      nombre,
                    "monto":       float(doc.get("_monto", 0)),
                    "metodo_pago": doc.get("metodo_pago", ""),
                    "concepto":    doc.get("concepto", ""),
                    "fecha":       fecha_str,
                    "categoria":   None,
                })
            else:
                nombre  = (doc.get("nombre_miembro") or "").strip() or "Cliente general"
                items   = doc.get("items", [])
                resumen = items[0].get("nombre", "Venta POS") if items else "Venta POS"
                if len(items) > 1:
                    resumen = f"{resumen} +{len(items) - 1} más"
                # Categorías presentes en la venta (únicas)
                cats = list(dict.fromkeys(
                    i.get("categoria", "") for i in items if i.get("categoria")
                ))
                movimientos.append({
                    "id":          str(doc["_id"]),
                    "tipo":        "venta",
                    "titulo":      nombre,
                    "monto":       float(doc.get("_monto", 0)),
                    "metodo_pago": doc.get("metodo_pago", ""),
                    "concepto":    resumen,
                    "fecha":       fecha_str,
                    "categoria":   cats[0] if len(cats) == 1 else (", ".join(cats) if cats else None),
                })

        pages = math.ceil(total / per_page) if total > 0 else 0
        return jsonify({
            "movimientos": movimientos,
            "total":       total,
            "pages":       pages,
            "page":        page,
        }), 200

    except Exception as e:
        print(f"Error en listar_todos_movimientos: {e}")
        return jsonify({"movimientos": [], "total": 0, "pages": 0, "page": 1}), 500