from flask import Blueprint, request, jsonify, g, current_app
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity
from flask_mail import Message
from datetime import datetime, timezone
from bson import ObjectId
import math

from app.mongo import get_db
from app.extensions import mail
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

        # ── Enviar ticket por correo al miembro (no-bloqueante) ───────────────
        if miembro_doc:
            try:
                email_dest = miembro_doc.get("email")
                if email_dest and "@" in email_dest:
                    _send_ticket_email(email_dest, doc, items, venta_num)
            except Exception as e:
                print(f"[ventas] Error enviando ticket email: {e}")

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


def _send_ticket_email(email_dest: str, doc: dict, items: list, venta_num: str):
    """Genera y envía el ticket de venta en HTML al correo del miembro."""
    fmt = lambda n: f"${float(n):,.2f}"

    filas = "".join(
        f"""<tr>
              <td style="padding:6px 10px;border-bottom:1px solid #2d3148">{it['nombre']} x{it.get('qty',1)}</td>
              <td style="padding:6px 10px;border-bottom:1px solid #2d3148;text-align:right;color:#10b981;font-weight:700">{fmt(float(it['precio'])*int(it.get('qty',1)))}</td>
            </tr>"""
        for it in items
    )

    metodo_info = ""
    if doc.get("numero_tarjeta"):
        metodo_info = f"<p style='margin:4px 0;font-size:12px;color:#94a3b8'>Tarjeta: .... {doc['numero_tarjeta'][-4:]}</p>"
    if doc.get("referencia"):
        metodo_info = f"<p style='margin:4px 0;font-size:12px;color:#94a3b8'>Referencia: {doc['referencia']}</p>"

    fecha_str = doc["fecha"].strftime("%d/%m/%Y %H:%M") if hasattr(doc["fecha"], "strftime") else str(doc["fecha"])[:16]

    html = f"""
    <div style="background:#0f1117;color:#f1f5f9;font-family:Arial,sans-serif;max-width:480px;margin:0 auto;border-radius:12px;overflow:hidden">
      <div style="background:#6366f1;padding:20px 24px;text-align:center">
        <h1 style="margin:0;font-size:22px;letter-spacing:2px;color:#fff">GYM PRO</h1>
        <p style="margin:4px 0 0;font-size:12px;color:rgba(255,255,255,.8)">Punto de Venta — Comprobante</p>
      </div>
      <div style="padding:20px 24px">
        <div style="text-align:center;margin-bottom:16px">
          <p style="margin:0;font-size:11px;color:#64748b"># {venta_num}</p>
          <p style="margin:4px 0 0;font-size:12px;color:#94a3b8">{fecha_str}</p>
        </div>
        {"<p style='font-size:13px;color:#94a3b8;margin-bottom:12px'>Cliente: <strong style=\"color:#f1f5f9\">" + doc.get('nombre_miembro','') + "</strong></p>" if doc.get('nombre_miembro') else ""}
        <table width="100%" cellspacing="0" style="border-collapse:collapse;margin-bottom:16px">
          <thead>
            <tr style="border-bottom:1px solid #3d4166">
              <th style="padding:6px 10px;text-align:left;font-size:11px;color:#64748b;text-transform:uppercase">Artículo</th>
              <th style="padding:6px 10px;text-align:right;font-size:11px;color:#64748b;text-transform:uppercase">Importe</th>
            </tr>
          </thead>
          <tbody>{filas}</tbody>
        </table>
        <div style="border-top:2px dashed #3d4166;padding-top:12px;display:flex;justify-content:space-between">
          <span style="font-size:16px;font-weight:700">TOTAL</span>
          <span style="font-size:20px;font-weight:900;color:#10b981">{fmt(doc['total'])}</span>
        </div>
        <div style="margin-top:12px;padding-top:12px;border-top:1px solid #2d3148">
          <p style="margin:4px 0;font-size:12px;color:#94a3b8">Método: <strong style="color:#f1f5f9">{doc.get('metodo_pago','')}</strong></p>
          {metodo_info}
        </div>
        <p style="text-align:center;margin-top:20px;font-size:11px;color:#64748b">Gracias por tu compra · GYM PRO</p>
      </div>
    </div>
    """

    msg = Message(
        subject=f"[GYM PRO] Tu ticket de compra #{venta_num}",
        recipients=[email_dest],
        html=html,
    )
    with current_app.app_context():
        mail.send(msg)


@ventas_bp.route("/api/ventas", methods=["GET"])
@jwt_required()
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

        # Si es miembro, filtrar solo sus compras propias
        if role in ("user", "miembro"):
            user_pg_id = int(get_jwt_identity())
            gym_id = tenant_filter.get("id_gimnasio") if tenant_filter else None
            query_m = {"id_usuario_pg": user_pg_id}
            if gym_id:
                query_m["id_gimnasio_pg"] = gym_id
            miembro = db.miembros.find_one(query_m)
            if miembro:
                filtro["id_miembro"] = miembro["_id"]
            else:
                return jsonify({"ventas": [], "total": 0, "pages": 0, "page": page}), 200

        total  = db.ventas.count_documents(filtro)
        cursor = db.ventas.find(filtro).sort("fecha", -1).skip(skip).limit(per_page)
        pages  = math.ceil(total / per_page) if total > 0 else 0

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

        return jsonify({"ventas": ventas, "total": total, "pages": pages, "page": page}), 200

    except Exception as e:
        print(f"Error en listar_ventas: {e}")
        return jsonify({"ventas": [], "total": 0}), 500
