from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import date, datetime, timedelta
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
from app.utils.recibos import enviar_recibo_membresia, registrar_evento_transaccion
from app.utils.security import require_role
from app.constants.roles import ROL_PROPIETARIO, ROL_ADMINISTRADOR, ROL_RECEPCIONISTA

pagos_bp = Blueprint("pagos", __name__)

@pagos_bp.route("/api/pagos", methods=["POST"])
@jwt_required()
@require_role(ROL_PROPIETARIO, ROL_ADMINISTRADOR, ROL_RECEPCIONISTA)
@require_tenant
def registrar_pago():
    """Cobro manual de membresia en la sucursal (Efectivo) o via pasarela.

    Peticion explicita del usuario: el cobro manual (para quien paga en
    efectivo en la sucursal) debe distinguir si es una RENOVACION (el
    miembro ya tenia membresia) o una INSCRIPCION Y PRIMER PAGO (alta
    nueva), porque la inscripcion lleva un costo adicional que el staff
    captura a mano en cada cobro -- varia por promocion/negociacion y no
    es un precio fijo del catalogo de TipoMembresia.
    """
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

        # 4a. Transferencia: la referencia (folio o clave de rastreo) es
        #     obligatoria -- es lo único que permite ubicar el movimiento en
        #     el estado de cuenta del banco si hace falta conciliarlo después.
        referencia = (data.get("referencia") or "").strip()
        if data["metodo_pago"] == "Transferencia" and not referencia:
            return jsonify({"error": "Falta la referencia de la transferencia"}), 400

        # 4b2. Fecha del pago: por defecto es ahora, pero el staff puede
        #      indicar que el pago ocurrió en otro día (p. ej. una
        #      transferencia ya hecha que no se pudo registrar antes por no
        #      existir todavía ese método) -- la renovación y el conteo de
        #      días de la membresía deben arrancar desde esa fecha, no desde
        #      hoy. Acepta "YYYY-MM-DD" (hora opcional, se usa la hora
        #      actual) o "YYYY-MM-DDTHH:MM".
        fecha_pago_raw = (data.get("fecha_pago") or "").strip()
        fecha_pago_dt  = None
        if fecha_pago_raw:
            try:
                if "T" in fecha_pago_raw:
                    fecha_pago_dt = datetime.strptime(fecha_pago_raw[:16], "%Y-%m-%dT%H:%M")
                else:
                    fecha_pago_dt = datetime.combine(
                        datetime.strptime(fecha_pago_raw[:10], "%Y-%m-%d").date(),
                        datetime.now().time().replace(microsecond=0),
                    )
            except ValueError:
                return jsonify({"error": "Fecha de pago inválida"}), 400
            if fecha_pago_dt.date() > date.today():
                return jsonify({"error": "La fecha del pago no puede ser futura"}), 400

        # 4b. Renovación vs. inscripción y primer pago -- la inscripción
        #     lleva un costo adicional que el staff teclea a mano (no un
        #     precio de catálogo), sumado al precio normal de la membresía.
        tipo_pago = data.get("tipo_pago", "renovacion")
        if tipo_pago not in ("renovacion", "inscripcion"):
            return jsonify({"error": "tipo_pago inválido (usar renovacion o inscripcion)"}), 400

        monto_membresia   = float(tm.precio)
        monto_inscripcion = 0.0
        if tipo_pago == "inscripcion":
            try:
                monto_inscripcion = float(data.get("costo_inscripcion"))
            except (TypeError, ValueError):
                monto_inscripcion = -1.0
            if monto_inscripcion <= 0:
                return jsonify({"error": "Falta el costo de inscripción"}), 400
        monto_total = monto_membresia + monto_inscripcion

        concepto = (
            f"Inscripción y primer pago — {tm.nombre}" if tipo_pago == "inscripcion"
            else f"Renovación de membresía — {tm.nombre}"
        )

        # 5. Crear el Pago con datos de PG TipoMembresia
        pago = Pago(
            id_miembro=miembro._id,
            monto=monto_total,
            metodo_pago=data["metodo_pago"],
            concepto=concepto,
            id_gimnasio=gym_id,
            tipo_pago=tipo_pago,
            costo_inscripcion=(monto_inscripcion if tipo_pago == "inscripcion" else None),
            referencia=(referencia or None),
            fecha_pago=fecha_pago_dt,
        )
        pago.save()

        # 6. Calcular fechas -- si el staff indicó que el pago ocurrió en otro
        #    día, la renovación y el conteo de días de la membresía arrancan
        #    desde esa fecha, no desde hoy.
        inicio   = fecha_pago_dt.date() if fecha_pago_dt else date.today()
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

        # -- Recibo por correo al miembro Y al propietario, mas bitacora de
        #    seguridad (misma peticion que ya cubre ventas.py: una renovacion
        #    antes no mandaba ningun correo en absoluto). No-bloqueante: un
        #    fallo de correo o de bitacora nunca debe deshacer el pago, que
        #    ya quedo guardado arriba.
        try:
            enviar_recibo_membresia(
                gym_id,
                numero=str(pago._id)[-8:].upper() if pago._id else "N/A",
                fecha_str=pago.fecha_pago.strftime("%d/%m/%Y %H:%M"),
                membresia_nombre=tm.nombre, monto=pago.monto, metodo_pago=pago.metodo_pago,
                fecha_inicio_str=inicio.strftime("%d/%m/%Y"), fecha_fin_str=fin.strftime("%d/%m/%Y"),
                nombre_miembro=getattr(miembro, "nombre", "") or "",
                email_miembro=getattr(miembro, "email", None),
                tipo_pago=tipo_pago,
                costo_inscripcion=(monto_inscripcion if tipo_pago == "inscripcion" else None),
            )
        except Exception as e:
            print(f"[pagos] Error enviando recibo: {e}")

        try:
            id_actor = int(get_jwt_identity())
        except (TypeError, ValueError):
            id_actor = None
        registrar_evento_transaccion(
            "membresia.inscripcion_manual" if tipo_pago == "inscripcion" else "membresia.renovada",
            id_usuario_actor=id_actor, id_gimnasio=gym_id,
            detalle={
                "id_membresia": tm.id, "membresia_nombre": tm.nombre,
                "monto": pago.monto, "metodo_pago": pago.metodo_pago,
                "id_miembro": str(miembro._id) if miembro._id else None,
                "tipo_pago": tipo_pago,
                "costo_inscripcion": (monto_inscripcion if tipo_pago == "inscripcion" else None),
            },
        )

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
#   ?anio=<int>&mes=<int>         (periodo; mes=0 o ausente = año completo)
#   ?desde=<YYYY-MM-DD>&hasta=<YYYY-MM-DD>  (rango libre; tiene prioridad
#                                  sobre anio/mes -- así la app puede pedir
#                                  un día o una semana concretos sin que el
#                                  backend tenga que adivinar "qué es una
#                                  semana" en cada selector)
#   ?page=<int>                   (default: 1)
#   ?per_page=<int>               (default: 10, máximo 50)
#
# Devuelve, además de la página pedida, el TOTAL EN DINERO de todo el filtro
# (no solo de la página) y los periodos con movimientos, para que la app pueda
# ofrecer el selector de mes y año sin adivinar.
# ─────────────────────────────────────────────────────────────────────────────
@pagos_bp.route("/api/pagos/todos", methods=["GET"])
@jwt_required()
@require_tenant
def listar_todos_movimientos():
    """Feed paginado unificado de membresías y ventas POS con filtros opcionales."""
    try:
        db        = get_db()
        gym_id    = g.tenant_id
        page      = max(1, request.args.get("page", 1, type=int))
        tipo      = request.args.get("tipo", "todos")          # todos | membresia | venta
        categoria = (request.args.get("categoria") or "").strip()
        per_page  = min(50, max(1, request.args.get("per_page", 10, type=int)))
        skip      = (page - 1) * per_page

        # ── Periodo ───────────────────────────────────────────────────────────
        # desde/hasta (rango libre) tiene prioridad sobre anio/mes -- mismo
        # criterio que _resolver_periodo() en reportes_negocio.py, para que
        # "hoy" o "esta semana" en Pagos y en Reportes filtren igual.
        # anio sin mes filtra el año entero; sin anio no se filtra por fecha.
        anio = request.args.get("anio", type=int)
        mes  = request.args.get("mes",  type=int)
        desde_txt = (request.args.get("desde") or "").strip()
        hasta_txt = (request.args.get("hasta") or "").strip()
        rango_fechas = None
        if desde_txt and hasta_txt:
            try:
                desde = datetime.strptime(desde_txt[:10], "%Y-%m-%d")
                hasta = datetime.strptime(hasta_txt[:10], "%Y-%m-%d") + timedelta(days=1)
                rango_fechas = {"$gte": desde, "$lt": hasta}
            except ValueError:
                pass
        if rango_fechas is None and anio:
            if mes and 1 <= mes <= 12:
                desde = datetime(anio, mes, 1)
                hasta = datetime(anio + (mes == 12), (mes % 12) + 1, 1)
            else:
                desde = datetime(anio, 1, 1)
                hasta = datetime(anio + 1, 1, 1)
            rango_fechas = {"$gte": desde, "$lt": hasta}

        # El filtro de periodo se aplica DESPUÉS de normalizar la fecha, porque
        # las dos colecciones la guardan en campos distintos y a veces como texto.
        filtro_periodo = [{"$match": {"_fecha_dt": rango_fechas}}] if rango_fechas else []

        # $facet resuelve en una sola pasada las tres cosas que necesita la app:
        # el conteo de documentos, el importe total del filtro completo y la
        # página pedida. Antes el importe se sumaba en el cliente sobre la página
        # visible, así que "Monto en esta vista" no era el total del filtro.
        facet = {
            "metadata": [{"$count": "total"}],
            "importe":  [{"$group": {"_id": None, "suma": {"$sum": "$_monto"}}}],
            "data":     [{"$skip": skip}, {"$limit": per_page}],
        }

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
                *filtro_periodo,
                {"$sort": {"_fecha_dt": -1}},
                {"$facet": facet},
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
                    # $toDate también aquí: algunas ventas antiguas guardaron la
                    # fecha como texto y sin convertir se ordenaban aparte.
                    "_fecha_dt": {"$toDate": "$fecha"},
                    "_monto":    "$total",
                }},
                *filtro_periodo,
                {"$sort": {"_fecha_dt": -1}},
                {"$facet": facet},
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
                            "_fecha_dt": {"$toDate": "$fecha"},
                            "_monto":    "$total",
                        }},
                    ],
                }},
                *filtro_periodo,
                {"$sort": {"_fecha_dt": -1}},
                {"$facet": facet},
            ]
            agg = list(db.pagos.aggregate(pipeline))

        total = agg[0]["metadata"][0]["total"] if agg and agg[0]["metadata"] else 0
        docs  = agg[0]["data"] if agg else []
        monto_total = (
            float(agg[0]["importe"][0]["suma"])
            if agg and agg[0].get("importe") and agg[0]["importe"][0].get("suma") is not None
            else 0.0
        )

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
                    "referencia":  doc.get("referencia") or None,
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
                    "referencia":  doc.get("referencia") or None,
                    "fecha":       fecha_str,
                    "categoria":   cats[0] if len(cats) == 1 else (", ".join(cats) if cats else None),
                })

        # ── Años con movimientos ──────────────────────────────────────────────
        # Alimenta el selector de periodo: solo se ofrecen años que existen.
        anios = sorted(
            {
                *(
                    d["_id"] for d in db.pagos.aggregate([
                        {"$match": {"id_gimnasio": gym_id}},
                        {"$group": {"_id": {"$year": {"$toDate": "$fecha_pago"}}}},
                    ]) if d.get("_id")
                ),
                *(
                    d["_id"] for d in db.ventas.aggregate([
                        {"$match": {"id_gimnasio": gym_id}},
                        {"$group": {"_id": {"$year": {"$toDate": "$fecha"}}}},
                    ]) if d.get("_id")
                ),
            },
            reverse=True,
        )

        pages = math.ceil(total / per_page) if total > 0 else 0
        return jsonify({
            "movimientos": movimientos,
            "total":       total,
            "pages":       pages,
            "page":        page,
            "per_page":    per_page,
            # Importe de TODO el filtro, no solo de esta página.
            "monto_total": monto_total,
            "anios":       anios,
            "filtro":      {"tipo": tipo, "anio": anio, "mes": mes,
                            "desde": desde_txt or None, "hasta": hasta_txt or None},
        }), 200

    except Exception as e:
        print(f"Error en listar_todos_movimientos: {e}")
        return jsonify({"movimientos": [], "total": 0, "pages": 0, "page": 1}), 500