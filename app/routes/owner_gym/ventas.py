from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity
from datetime import datetime, timezone
from bson import ObjectId
import math

from app.mongo import get_db
from app.utils.tenant import get_tenant_filter, require_tenant
from app.utils.security import require_funcion_plan  # SCRUM-224
from app.utils.recibos import enviar_recibo_venta, registrar_evento_transaccion

ventas_bp = Blueprint("ventas", __name__)


@ventas_bp.route("/api/ventas", methods=["POST"])
@jwt_required()
@require_tenant
@require_funcion_plan("pos")  # SCRUM-224
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

        # ── Resolver comprador ────────────────────────────────────────────────
        # El historial del miembro (GET /api/ventas) filtra por el ObjectId del
        # documento miembro. Resolvemos y persistimos AMBOS (ObjectId + PG id)
        # para que la compra se refleje correctamente.
        #
        # Si quien compra es un MIEMBRO, ignoramos el id del body y usamos su
        # propia identidad del JWT (a prueba de fallos). Para POS de staff
        # (owner/recepcionista) usamos el id_miembro enviado en el body.
        role = (get_jwt().get("role") or "").lower()
        id_miembro_oid = None
        id_miembro_pg  = None
        miembro_doc    = None

        if role in ("user", "miembro"):
            try:
                id_miembro_pg = int(get_jwt_identity())
                miembro_doc = db.miembros.find_one({
                    "id_usuario_pg":  id_miembro_pg,
                    "id_gimnasio_pg": id_gimnasio,
                })
            except (ValueError, TypeError):
                miembro_doc = None
        else:
            id_miembro_raw = data.get("id_miembro")
            if id_miembro_raw is not None and id_miembro_raw != "":
                try:
                    id_miembro_pg = int(id_miembro_raw)
                    miembro_doc = db.miembros.find_one({"id_usuario_pg": id_miembro_pg})
                except (ValueError, TypeError):
                    # Puede venir ya como ObjectId string (POS de admin)
                    try:
                        miembro_doc = db.miembros.find_one({"_id": ObjectId(str(id_miembro_raw))})
                    except Exception:
                        miembro_doc = None

        if miembro_doc:
            id_miembro_oid = miembro_doc["_id"]
            if id_miembro_pg is None:
                id_miembro_pg = miembro_doc.get("id_usuario_pg")

        doc = {
            "items":          items,
            "total":          float(total),
            "metodo_pago":    metodo,
            "fecha":          datetime.now(timezone.utc),
            "id_gimnasio":    id_gimnasio,
            # Datos del comprador (ObjectId para que el historial del miembro lo encuentre)
            "id_miembro":     id_miembro_oid,
            "id_miembro_pg":  id_miembro_pg,
            "nombre_miembro": data.get("nombre_miembro", ""),
            # Datos de pago según método
            "numero_tarjeta": data.get("numero_tarjeta", ""),
            "referencia":     data.get("referencia", ""),
        }
        result = db.ventas.insert_one(doc)

        # ── Decrementar stock de cada producto vendido ────────────────────────
        # Usa pipeline de actualización para que stock nunca baje de 0.
        # Si el artículo es un COMBO, no se descuenta él mismo (no lleva stock
        # propio): se descuenta el stock de cada componente que lo integra,
        # multiplicado por la cantidad de combos vendidos.
        for item in items:
            try:
                oid = ObjectId(str(item["id"]))
                qty = max(1, int(item.get("qty", 1)))

                prod = db.productos.find_one(
                    {"_id": oid, "id_gimnasio": id_gimnasio},
                    {"es_combo": 1, "items_combo": 1},
                )

                if prod and prod.get("es_combo"):
                    for comp in (prod.get("items_combo") or []):
                        try:
                            comp_oid = ObjectId(str(comp["id_producto"]))
                            comp_qty = max(1, int(comp.get("cantidad", 1))) * qty
                            db.productos.update_one(
                                {"_id": comp_oid, "id_gimnasio": id_gimnasio},
                                [{"$set": {"stock": {"$max": [0, {"$subtract": ["$stock", comp_qty]}]}}}]
                            )
                        except Exception:
                            continue
                else:
                    db.productos.update_one(
                        {"_id": oid, "id_gimnasio": id_gimnasio},
                        [{"$set": {"stock": {"$max": [0, {"$subtract": ["$stock", qty]}]}}}]
                    )
            except Exception:
                pass  # no-bloqueante: fallo de stock no cancela la venta

        venta_id  = str(result.inserted_id)
        venta_num = venta_id[-8:].upper()

        # ── Recibo por correo al miembro Y al propietario (no-bloqueante) ──────
        # SCRUM-224+: antes solo se le avisaba al miembro (ver _send_ticket_email,
        # mas abajo en este archivo, ahora reemplazada por utils/recibos.py, que
        # comparte la plantilla con las renovaciones de membresia).
        try:
            enviar_recibo_venta(
                id_gimnasio, venta_num=venta_num,
                fecha_str=doc["fecha"].strftime("%d/%m/%Y %H:%M"),
                items=items, total=doc["total"], metodo_pago=metodo,
                nombre_miembro=doc.get("nombre_miembro", ""),
                email_miembro=(miembro_doc.get("email") if miembro_doc else None),
                referencia_pasarela=doc.get("referencia") or None,
            )
        except Exception as e:
            print(f"[ventas] Error enviando recibo: {e}")

        # ── Bitacora de seguridad: trazabilidad de la transaccion ──────────────
        try:
            id_actor = int(get_jwt_identity())
        except (TypeError, ValueError):
            id_actor = None
        registrar_evento_transaccion(
            "pos.venta_registrada",
            id_usuario_actor=id_actor, id_gimnasio=id_gimnasio,
            detalle={
                "venta_id": venta_id, "total": doc["total"],
                "metodo_pago": metodo, "items": len(items),
                "id_miembro_pg": id_miembro_pg,
            },
        )

        return jsonify({
            "id":             venta_id,
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
@require_funcion_plan("pos")  # SCRUM-224
def listar_ventas():
    """
    Lista ventas paginadas. Si el JWT corresponde a un miembro, solo devuelve sus compras.
    Query params: page (default 1), per_page (default 10).
    """
    try:
        from flask_jwt_extended import get_jwt
        db            = get_db()
        tenant_filter = get_tenant_filter()
        page          = request.args.get("page", 1, type=int)
        per_page      = request.args.get("per_page", 10, type=int)
        skip          = (page - 1) * per_page

        claims = get_jwt()
        role   = (claims.get("role") or "").lower()

        filtro = {}
        if tenant_filter:
            filtro["id_gimnasio"] = tenant_filter["id_gimnasio"]

        # Si es miembro, filtrar solo sus compras propias.
        #
        # Se busca por los DOS identificadores porque conviven ventas guardadas
        # de formas distintas: las del POS traen el ObjectId del documento
        # miembro, y las pagadas en línea antes de la corrección quedaron solo
        # con el id de PostgreSQL. Con un único criterio, parte del historial
        # queda invisible para el miembro.
        if role in ("user", "miembro"):
            user_pg_id = int(get_jwt_identity())
            gym_id = tenant_filter.get("id_gimnasio") if tenant_filter else None
            query_m = {"id_usuario_pg": user_pg_id}
            if gym_id:
                query_m["id_gimnasio_pg"] = gym_id
            miembro = db.miembros.find_one(query_m) or db.miembros.find_one(
                {"id_usuario_pg": user_pg_id})

            criterios = [{"id_miembro_pg": user_pg_id}, {"id_miembro": user_pg_id}]
            if miembro:
                criterios.append({"id_miembro": miembro["_id"]})
            filtro["$or"] = criterios

        # ── Periodo (opcional) ────────────────────────────────────────────────
        # anio sin mes devuelve el año completo. Se filtra por rango de fechas
        # en vez de por año extraído, para que el índice de 'fecha' sirva.
        anio = request.args.get("anio", type=int)
        mes  = request.args.get("mes",  type=int)
        if anio:
            if mes and 1 <= mes <= 12:
                desde = datetime(anio, mes, 1)
                hasta = datetime(anio + (mes == 12), (mes % 12) + 1, 1)
            else:
                desde = datetime(anio, 1, 1)
                hasta = datetime(anio + 1, 1, 1)
            filtro["fecha"] = {"$gte": desde, "$lt": hasta}

        total  = db.ventas.count_documents(filtro)
        cursor = db.ventas.find(filtro).sort("fecha", -1).skip(skip).limit(per_page)
        pages  = math.ceil(total / per_page) if total > 0 else 0

        # Importe de todo el filtro, no solo de la página: es lo que la pantalla
        # muestra como total del periodo.
        agg = list(db.ventas.aggregate([
            {"$match": filtro},
            {"$group": {"_id": None, "suma": {"$sum": "$total"}}},
        ]))
        monto_total = float(agg[0]["suma"]) if agg and agg[0].get("suma") is not None else 0.0

        # Años con ventas, para el selector de periodo. Se calcula sobre el mismo
        # filtro pero sin el rango de fechas, si no siempre devolvería un año.
        filtro_sin_fecha = {k: v for k, v in filtro.items() if k != "fecha"}
        anios = sorted(
            {
                d["_id"] for d in db.ventas.aggregate([
                    {"$match": filtro_sin_fecha},
                    {"$group": {"_id": {"$year": {"$toDate": "$fecha"}}}},
                ]) if d.get("_id")
            },
            reverse=True,
        )

        ventas = []
        for v in cursor:
            ventas.append({
                "id":             str(v["_id"]),
                "total":          v.get("total", 0),
                "metodo_pago":    v.get("metodo_pago"),
                "items":          v.get("items", []),
                "fecha":          v["fecha"].isoformat() if isinstance(v.get("fecha"), datetime) else str(v.get("fecha")),
                "nombre_miembro": v.get("nombre_miembro", ""),
            })

        return jsonify({
            "ventas":      ventas,
            "total":       total,
            "pages":       pages,
            "page":        page,
            "per_page":    per_page,
            "monto_total": monto_total,
            "anios":       anios,
        }), 200

    except Exception as e:
        print(f"Error en listar_ventas: {e}")
        return jsonify({"ventas": [], "total": 0}), 500
