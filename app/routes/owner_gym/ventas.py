from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity
from datetime import datetime, timezone
from bson import ObjectId
import math
import re

from app.mongo import get_db
from app.utils.tenant import get_tenant_filter, require_tenant
from app.utils.security import require_funcion_plan  # SCRUM-224
from app.utils.recibos import enviar_recibo_venta, registrar_evento_transaccion
from app.utils.timezone import local_now_naive
from app.utils.horarios_gym import horarios_desde_configuracion, DIAS_SEMANA
from app.models.pg.gimnasio import Gimnasio
from app.routes.compartido.notificaciones import crear_notificacion
from app.constants.roles import (
    ROL_PROPIETARIO, ROL_ADMINISTRADOR, ROL_RECEPCIONISTA, ROL_ENTRENADOR,
    ROL_SUPERADMIN, normalizar_rol,
)

ventas_bp = Blueprint("ventas", __name__)

_HORA_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _require_staff():
    """
    HU-39 AC5: cualquier integrante del personal (Propietario, Administrador,
    Recepcionista o Entrenador) puede ver el panel de entregas pendientes y
    marcar una compra como entregada -- no solo quien atendio la venta ni
    solo recepcion.
    """
    role = normalizar_rol(get_jwt().get("role", ""))
    allowed = {ROL_PROPIETARIO, ROL_ADMINISTRADOR, ROL_RECEPCIONISTA, ROL_ENTRENADOR, ROL_SUPERADMIN}
    if role not in allowed:
        return jsonify({"error": "Acceso no autorizado"}), 403
    return None


def _validar_hora_recogida(id_gimnasio, fecha_recogida, hora_recogida):
    """
    HU-39 AC1: valida dia+hora de recogida contra el horario de atencion
    del gimnasio ese dia. Retorna None si es valida, o (mensaje, codigo
    http) si no.
    """
    try:
        fecha_dt = datetime.strptime(fecha_recogida, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return "Fecha de recogida invalida", 400

    if not _HORA_RE.match(hora_recogida or ""):
        return "Hora de recogida invalida", 400

    hoy_local = local_now_naive().date()
    if fecha_dt < hoy_local:
        return "La fecha de recogida no puede ser anterior a hoy", 400

    gym = Gimnasio.query.get(id_gimnasio)
    config = (gym.configuracion or {}) if gym else {}
    horarios = horarios_desde_configuracion(config)
    dia_semana = DIAS_SEMANA[fecha_dt.weekday()]
    horario_dia = horarios.get(dia_semana) or {}

    if horario_dia.get("cerrado"):
        return f"El gimnasio no abre el {dia_semana}. Elige otro dia.", 400

    apertura = horario_dia.get("apertura", "00:00")
    cierre   = horario_dia.get("cierre", "23:59")
    if not (apertura <= hora_recogida <= cierre):
        return f"Elige una hora entre {apertura} y {cierre} para ese dia.", 400

    return None


def _resumen_items(items):
    partes = []
    for it in items[:10]:
        try:
            qty = max(1, int(it.get("qty", 1)))
        except (TypeError, ValueError):
            qty = 1
        partes.append(f"{qty}\u00d7 {it.get('nombre', 'Producto')}")
    return ", ".join(partes) or "producto"


def _notificar_compra_pendiente(db, id_gimnasio, venta_id, nombre_comprador, items,
                                 total, metodo, fecha_recogida, hora_recogida,
                                 id_entrenador_pg=None):
    """
    HU-39 AC2/AC3: avisa al propietario, a los recepcionistas y al
    entrenador asignado del miembro (si tiene uno) de una compra pendiente
    de entrega. No-bloqueante: un fallo aqui nunca debe afectar la venta ya
    registrada.
    """
    try:
        fecha_fmt = datetime.strptime(fecha_recogida, "%Y-%m-%d").strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        fecha_fmt = fecha_recogida

    mensaje = (
        f"{nombre_comprador or 'Un miembro'} compro {_resumen_items(items)} "
        f"\u2014 ${float(total):.2f} ({metodo}). Pasara a recogerlo el {fecha_fmt} "
        f"a las {hora_recogida}."
    )

    destinatarios_pg = set()
    try:
        from app.models.pg.usuario import Usuario
        from app.models.pg.rol import Rol

        roles_aviso = Rol.query.filter(
            Rol.nombre.in_([ROL_PROPIETARIO, ROL_RECEPCIONISTA])
        ).all()
        role_ids = [r.id for r in roles_aviso]
        if role_ids:
            staff = Usuario.query.filter(
                Usuario.id_gimnasio == id_gimnasio,
                Usuario.id_rol.in_(role_ids),
                Usuario.activo.is_(True),
            ).all()
            destinatarios_pg.update(u.id for u in staff)
    except Exception as e:
        print(f"[ventas] No se pudo resolver propietario/recepcionistas: {e}")

    # AC2: entrenador asignado -- se omite sin fallar si el miembro no tiene uno.
    if id_entrenador_pg:
        try:
            destinatarios_pg.add(int(id_entrenador_pg))
        except (TypeError, ValueError):
            pass

    for uid in destinatarios_pg:
        try:
            crear_notificacion(
                db=db,
                id_usuario_pg=uid,
                id_gimnasio_pg=id_gimnasio,
                tipo="compra_pendiente_entrega",
                titulo="Producto por entregar",
                mensaje=mensaje,
                referencia_tipo="venta",
                referencia_id=venta_id,
            )
        except Exception as e:
            print(f"[ventas] No se pudo notificar compra pendiente a {uid}: {e}")


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

        # Transferencia: la referencia (folio o clave de rastreo) es obligatoria
        # -- es lo único que permite ubicar el movimiento en el estado de cuenta
        # del banco si hace falta conciliarlo después.
        referencia = (data.get("referencia") or "").strip()
        if metodo == "Transferencia" and not referencia:
            return jsonify({"error": "Falta la referencia de la transferencia"}), 400

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
            # POS de staff (owner/administrador/recepcionista/entrenador): el
            # miembro es obligatorio -- una venta sin miembro asociado nunca
            # cuadra en su historial de compras ni en los reportes del negocio.
            id_miembro_raw = data.get("id_miembro")
            if id_miembro_raw is None or id_miembro_raw == "":
                return jsonify({"error": "Selecciona un miembro para esta venta"}), 400
            try:
                id_miembro_pg = int(id_miembro_raw)
                miembro_doc = db.miembros.find_one({"id_usuario_pg": id_miembro_pg})
            except (ValueError, TypeError):
                # Puede venir ya como ObjectId string (POS de admin)
                try:
                    miembro_doc = db.miembros.find_one({"_id": ObjectId(str(id_miembro_raw))})
                except Exception:
                    miembro_doc = None
            if not miembro_doc:
                return jsonify({"error": "Miembro no encontrado"}), 404

        if miembro_doc:
            id_miembro_oid = miembro_doc["_id"]
            if id_miembro_pg is None:
                id_miembro_pg = miembro_doc.get("id_usuario_pg")

        # ── HU-39: dia y hora de recogida ───────────────────────────────────────
        # Obligatorio cuando compra el propio miembro desde la app (AC1); opcional
        # para una venta de mostrador hecha por staff (el producto se entrega ahi
        # mismo, en el momento). Si llegan ambos campos en cualquier caso, se
        # validan igual contra el horario de atencion del gimnasio.
        fecha_recogida = (data.get("fecha_recogida") or "").strip()
        hora_recogida  = (data.get("hora_recogida") or "").strip()
        es_compra_miembro = role in ("user", "miembro")

        if es_compra_miembro and not (fecha_recogida and hora_recogida):
            return jsonify({"error": "Indica el dia y la hora en que pasaras por tu producto"}), 400

        if fecha_recogida or hora_recogida:
            if not (fecha_recogida and hora_recogida):
                return jsonify({"error": "Indica dia Y hora de recogida"}), 400
            _error = _validar_hora_recogida(id_gimnasio, fecha_recogida, hora_recogida)
            if _error:
                _mensaje, _codigo = _error
                return jsonify({"error": _mensaje}), _codigo

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
            "referencia":     referencia,
            # HU-39: entrega del producto comprado
            "estado_entrega": "pendiente" if (fecha_recogida and hora_recogida) else None,
            "fecha_recogida": fecha_recogida or None,
            "hora_recogida":  hora_recogida or None,
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

        # ── HU-39: avisar al personal si la compra queda pendiente de entrega ──
        if doc.get("estado_entrega") == "pendiente":
            try:
                _id_entrenador_pg = (miembro_doc or {}).get("id_entrenador_pg")
                _notificar_compra_pendiente(
                    db, id_gimnasio, venta_id,
                    nombre_comprador=doc.get("nombre_miembro") or (miembro_doc or {}).get("nombre"),
                    items=items, total=doc["total"], metodo=metodo,
                    fecha_recogida=doc["fecha_recogida"], hora_recogida=doc["hora_recogida"],
                    id_entrenador_pg=_id_entrenador_pg,
                )
            except Exception as e:
                print(f"[ventas] Error notificando compra pendiente: {e}")

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
            "estado_entrega": doc.get("estado_entrega"),
            "fecha_recogida": doc.get("fecha_recogida"),
            "hora_recogida":  doc.get("hora_recogida"),
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
                "estado_entrega": v.get("estado_entrega"),
                "fecha_recogida": v.get("fecha_recogida"),
                "hora_recogida":  v.get("hora_recogida"),
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


# ─────────────────────────────────────────────────────────────────────────────
# HU-39 — Panel de entregas pendientes
# ─────────────────────────────────────────────────────────────────────────────

@ventas_bp.route("/api/ventas/pendientes-entrega", methods=["GET"])
@jwt_required()
@require_tenant
def listar_pendientes_entrega():
    """
    AC4: lista de compras pendientes de entrega del gimnasio, ordenada por
    hora de recogida. Visible a cualquier integrante del personal (no solo
    recepción), porque cualquiera puede entregar el producto.
    """
    err = _require_staff()
    if err:
        return err

    try:
        db     = get_db()
        gym_id = g.tenant_id

        ahora     = local_now_naive()
        hoy_str   = ahora.strftime("%Y-%m-%d")
        hora_str  = ahora.strftime("%H:%M")

        cursor = db.ventas.find(
            {"id_gimnasio": gym_id, "estado_entrega": "pendiente"},
        ).sort([("fecha_recogida", 1), ("hora_recogida", 1)])

        pendientes = []
        for v in cursor:
            fecha_v = v.get("fecha_recogida") or ""
            hora_v  = v.get("hora_recogida") or ""
            # AC7: vencida si ya pasó la hora acordada -- solo se resalta, nunca
            # se cancela ni se oculta sola (el producto ya está pagado).
            vencida = (fecha_v, hora_v) < (hoy_str, hora_str)
            pendientes.append({
                "id":             str(v["_id"]),
                "nombre_miembro": v.get("nombre_miembro", ""),
                "items":          v.get("items", []),
                "total":          v.get("total", 0),
                "metodo_pago":    v.get("metodo_pago"),
                "fecha_recogida": fecha_v,
                "hora_recogida":  hora_v,
                "vencida":        vencida,
            })

        return jsonify({"pendientes": pendientes}), 200

    except Exception as e:
        print(f"Error en listar_pendientes_entrega: {e}")
        return jsonify({"pendientes": []}), 500


@ventas_bp.route("/api/ventas/<venta_id>/entregar", methods=["PATCH"])
@jwt_required()
@require_tenant
def marcar_entregada(venta_id):
    """
    AC5/AC6: marca una compra pendiente como entregada, deja registrado
    quién la entregó y cuándo, y confirma la entrega al miembro.
    """
    err = _require_staff()
    if err:
        return err

    try:
        db     = get_db()
        gym_id = g.tenant_id

        try:
            oid = ObjectId(venta_id)
        except Exception:
            return jsonify({"error": "ID inválido"}), 400

        venta = db.ventas.find_one({"_id": oid, "id_gimnasio": gym_id})
        if not venta:
            return jsonify({"error": "Compra no encontrada"}), 404
        if venta.get("estado_entrega") != "pendiente":
            return jsonify({"error": "Esta compra no está pendiente de entrega"}), 409

        claims = get_jwt()
        try:
            id_actor = int(get_jwt_identity())
        except (TypeError, ValueError):
            id_actor = None
        nombre_actor = claims.get("nombre") or "Personal del gimnasio"

        ahora = datetime.now(timezone.utc)
        db.ventas.update_one(
            {"_id": oid},
            {"$set": {
                "estado_entrega":       "entregado",
                "entregado_por_pg":     id_actor,
                "entregado_por_nombre": nombre_actor,
                "entregado_en":         ahora,
            }},
        )

        # AC6: confirmación de entrega al miembro.
        id_miembro_pg = venta.get("id_miembro_pg")
        if id_miembro_pg:
            try:
                crear_notificacion(
                    db=db,
                    id_usuario_pg=int(id_miembro_pg),
                    id_gimnasio_pg=gym_id,
                    tipo="compra_entregada",
                    titulo="Producto entregado",
                    mensaje=f"Recogiste tu compra correctamente ({_resumen_items(venta.get('items', []))}). ¡Gracias!",
                    referencia_tipo="venta",
                    referencia_id=venta_id,
                )
            except Exception as e:
                print(f"[ventas] No se pudo notificar entrega al miembro: {e}")

        try:
            registrar_evento_transaccion(
                "pos.venta_entregada",
                id_usuario_actor=id_actor, id_gimnasio=gym_id,
                detalle={"venta_id": venta_id, "entregado_por": nombre_actor},
            )
        except Exception as e:
            print(f"[ventas] No se pudo registrar bitácora de entrega: {e}")

        return jsonify({
            "ok":              True,
            "estado_entrega":  "entregado",
            "entregado_por":   nombre_actor,
            "entregado_en":    ahora.isoformat(),
        }), 200

    except Exception as e:
        print(f"Error en marcar_entregada: {e}")
        return jsonify({"error": "Error interno", "detalle": str(e)}), 500
