"""
routes/pagos_online.py — Checkout y webhooks de PayPal / Mercado Pago.

Cubre los tres contextos de cobro de GymPro:

  membresia    el miembro paga su membresía  -> cuenta del GIMNASIO
  producto     venta de productos (POS)      -> cuenta del GIMNASIO
  suscripcion  el gimnasio paga su plan SaaS -> cuenta de la PLATAFORMA

Endpoints:
  GET  /api/pagos/metodos                 métodos disponibles para el gimnasio
  POST /api/pagos/checkout                crea el cobro y devuelve la URL de pago
  GET  /api/pagos/estado/<id>             consulta y confirma una transacción
  POST /api/pagos/webhook/<proveedor>     notificación asíncrona de la pasarela

Los webhooks están exentos de JWT (ver utils/tenant.py): la pasarela no envía
token. Se identifican por la transacción referenciada en el payload.
"""
import logging
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity

from app.extensions import db
from app.models.pg.pasarela_pago import TransaccionPago
from app.utils.tenant import require_tenant
from app.services.payments import (
    PasarelaError, pasarela_de_gimnasio, pasarela_de_plataforma, proveedores_disponibles,
)

logger = logging.getLogger(__name__)

pagos_online_bp = Blueprint("pagos_online", __name__, url_prefix="/api/pagos")

_CONTEXTOS = ("membresia", "producto", "suscripcion")


def _url_base_front() -> str:
    """URL pública del frontend, usada para las páginas de retorno."""
    return os.getenv("FRONTEND_URL", "http://localhost:8080").rstrip("/")


def _url_base_api() -> str:
    """URL pública de la API, usada para las notificaciones (webhooks)."""
    return os.getenv("PUBLIC_API_URL", "").rstrip("/")


# ── Métodos disponibles ──────────────────────────────────────────────────────

@pagos_online_bp.route("/metodos", methods=["GET"])
@jwt_required()
@require_tenant
def metodos_disponibles():
    """Métodos de pago en línea activos en el gimnasio actual."""
    return jsonify({"metodos": proveedores_disponibles(g.tenant_id)}), 200


@pagos_online_bp.route("/metodos-plataforma", methods=["GET"])
@jwt_required()
def metodos_plataforma():
    """
    Métodos con los que la PLATAFORMA puede cobrar la suscripción SaaS.
    Se derivan de las variables de entorno, no de la configuración del gimnasio.
    """
    modo = os.getenv("PLATAFORMA_PAGOS_MODO", "sandbox")
    moneda = os.getenv("PLATAFORMA_PAGOS_MONEDA", "MXN")
    metodos = []
    if os.getenv("PLATAFORMA_PAYPAL_CLIENT_ID"):
        metodos.append({"proveedor": "paypal", "nombre": "PayPal",
                        "modo": modo, "moneda": moneda})
    if os.getenv("PLATAFORMA_MP_ACCESS_TOKEN"):
        metodos.append({"proveedor": "mercadopago", "nombre": "Mercado Pago",
                        "modo": modo, "moneda": moneda})
    return jsonify({"metodos": metodos}), 200


# ── Crear checkout ───────────────────────────────────────────────────────────

@pagos_online_bp.route("/checkout", methods=["POST"])
@jwt_required()
@require_tenant
def crear_checkout():
    """
    Crea un cobro en línea y devuelve la URL a la que redirigir al pagador.

    Body JSON:
      {
        "proveedor": "paypal" | "mercadopago",
        "contexto":  "membresia" | "producto" | "suscripcion",
        "monto": 499.00,
        "descripcion": "Membresía mensual",
        "referencia_local": "id del miembro / venta / suscripción"
      }
    """
    data = request.get_json() or {}
    proveedor = (data.get("proveedor") or "").lower()
    contexto  = (data.get("contexto") or "").lower()

    if contexto not in _CONTEXTOS:
        return jsonify({"msg": f"Contexto inválido. Usa uno de: {', '.join(_CONTEXTOS)}"}), 400

    try:
        monto = round(float(data.get("monto") or 0), 2)
    except (TypeError, ValueError):
        return jsonify({"msg": "Monto inválido"}), 400
    if monto <= 0:
        return jsonify({"msg": "El monto debe ser mayor que cero"}), 400

    descripcion = (data.get("descripcion") or "Pago GymPro").strip()
    referencia_local = str(data.get("referencia_local") or "") or None

    # Resolver de dónde salen las credenciales según el contexto
    try:
        if contexto == "suscripcion":
            pasarela = pasarela_de_plataforma(proveedor)
            moneda = pasarela.moneda
        else:
            pasarela, cfg = pasarela_de_gimnasio(g.tenant_id, proveedor)
            moneda = cfg.moneda or pasarela.moneda
    except PasarelaError as exc:
        return jsonify({"msg": str(exc)}), 400

    # Registrar la transacción antes de llamar a la pasarela
    try:
        id_usuario = int(get_jwt_identity())
    except (TypeError, ValueError):
        id_usuario = None

    tx = TransaccionPago(
        id_gimnasio=g.tenant_id,
        proveedor=proveedor,
        contexto=contexto,
        estado="pendiente",
        monto=Decimal(str(monto)),
        moneda=moneda,
        descripcion=descripcion[:255],
        referencia_local=referencia_local,
        id_usuario=id_usuario,
        # 'extra' transporta lo necesario para aplicar el efecto del pago cuando
        # se confirme: a qué miembro renovar, o qué artículos vender.
        metadatos={
            "origen": data.get("origen") or "web",
            **(data.get("metadatos") if isinstance(data.get("metadatos"), dict) else {}),
        },
    )
    db.session.add(tx)
    db.session.flush()   # obtiene tx.id sin cerrar la transacción

    front = _url_base_front()
    url_exito      = f"{front}/pago/exito?tx={tx.id}"
    url_cancelacion = f"{front}/pago/cancelado?tx={tx.id}"

    extra = {"brand_name": "GymPro"}
    api_publica = _url_base_api()
    if api_publica:
        extra["notification_url"] = f"{api_publica}/api/pagos/webhook/{proveedor}"
    if data.get("email_pagador"):
        extra["email_pagador"] = data["email_pagador"]

    try:
        resultado = pasarela.crear_checkout(
            monto=monto, descripcion=descripcion,
            url_exito=url_exito, url_cancelacion=url_cancelacion,
            referencia=str(tx.id), extra=extra,
        )
    except PasarelaError as exc:
        db.session.rollback()
        logger.warning("Checkout %s falló: %s", proveedor, exc)
        return jsonify({"msg": str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        logger.exception("Error inesperado creando checkout")
        return jsonify({"msg": f"No se pudo iniciar el pago: {exc}"}), 500

    tx.referencia_externa = resultado.referencia_externa
    db.session.commit()

    return jsonify({
        "transaccion_id": tx.id,
        "url_pago":       resultado.url_pago,
        "proveedor":      proveedor,
        "monto":          monto,
        "moneda":         moneda,
    }), 201


# ── Consultar / confirmar estado ─────────────────────────────────────────────

@pagos_online_bp.route("/estado/<int:tx_id>", methods=["GET"])
@jwt_required()
@require_tenant
def estado_transaccion(tx_id):
    """
    Consulta el estado real del pago en la pasarela y actualiza la transacción.
    Se llama desde la página de retorno tras completar el checkout.
    """
    tx = TransaccionPago.query.filter_by(id=tx_id, id_gimnasio=g.tenant_id).first()
    if not tx:
        return jsonify({"msg": "Transacción no encontrada"}), 404

    estado_actual = tx.estado.value if hasattr(tx.estado, "value") else tx.estado
    if estado_actual in ("aprobado", "reembolsado"):
        return jsonify({"transaccion": tx.to_dict()}), 200

    proveedor = tx.proveedor.value if hasattr(tx.proveedor, "value") else tx.proveedor
    contexto  = tx.contexto.value if hasattr(tx.contexto, "value") else tx.contexto

    try:
        pasarela = (pasarela_de_plataforma(proveedor) if contexto == "suscripcion"
                    else pasarela_de_gimnasio(tx.id_gimnasio, proveedor)[0])
        resultado = pasarela.confirmar_pago(tx.referencia_externa)
    except PasarelaError as exc:
        return jsonify({"msg": str(exc), "transaccion": tx.to_dict()}), 400
    except Exception as exc:
        logger.exception("Error consultando pago")
        return jsonify({"msg": f"No se pudo consultar el pago: {exc}"}), 500

    _aplicar_resultado(tx, resultado)
    db.session.commit()
    return jsonify({"transaccion": tx.to_dict()}), 200


# ── Webhooks ─────────────────────────────────────────────────────────────────

@pagos_online_bp.route("/reconciliar", methods=["POST"])
@jwt_required()
@require_tenant
def reconciliar():
    """
    Confirma contra la pasarela las transacciones que quedaron pendientes.

    Es la red de seguridad del cobro en línea: si el pagador cerró la pestaña,
    la pasarela no devolvió al sitio (habitual en desarrollo local, donde
    Mercado Pago no puede redirigir a localhost) o el webhook no llegó, el pago
    igual queda registrado la próxima vez que se abra una pantalla de cobro.

    Revisa las últimas 24 horas del gimnasio actual y aplica los efectos de las
    que resulten aprobadas (renovar membresía, registrar venta, activar plan).
    """
    desde = datetime.now(timezone.utc) - timedelta(hours=24)
    pendientes = (TransaccionPago.query
                  .filter(TransaccionPago.id_gimnasio == g.tenant_id,
                          TransaccionPago.estado == "pendiente",
                          TransaccionPago.referencia_externa.isnot(None),
                          TransaccionPago.created_at >= desde)
                  .order_by(TransaccionPago.created_at.desc())
                  .limit(25)
                  .all())

    confirmadas, revisadas = [], 0
    for tx in pendientes:
        proveedor = tx.proveedor.value if hasattr(tx.proveedor, "value") else tx.proveedor
        contexto  = tx.contexto.value if hasattr(tx.contexto, "value") else tx.contexto
        try:
            pasarela = (pasarela_de_plataforma(proveedor) if contexto == "suscripcion"
                        else pasarela_de_gimnasio(tx.id_gimnasio, proveedor)[0])
            resultado = pasarela.confirmar_pago(tx.referencia_externa)
            revisadas += 1
        except PasarelaError:
            continue
        except Exception as exc:
            logger.warning("Reconciliación: no se pudo revisar la tx %s: %s", tx.id, exc)
            continue

        anterior = tx.estado.value if hasattr(tx.estado, "value") else tx.estado
        _aplicar_resultado(tx, resultado)
        nuevo = tx.estado.value if hasattr(tx.estado, "value") else tx.estado
        if anterior != nuevo and nuevo == "aprobado":
            confirmadas.append(tx.to_dict())

    if confirmadas:
        db.session.commit()
        logger.info("Reconciliación gym %s: %s pago(s) confirmado(s)",
                    g.tenant_id, len(confirmadas))
    else:
        db.session.commit()

    return jsonify({
        "revisadas":   revisadas,
        "confirmadas": confirmadas,
    }), 200


@pagos_online_bp.route("/webhook/<proveedor>", methods=["POST"])
def webhook(proveedor):
    """
    Recibe notificaciones asíncronas de la pasarela. Sin JWT: la petición
    proviene del proveedor, no del navegador del usuario.
    """
    proveedor = (proveedor or "").lower()
    payload = request.get_json(silent=True) or {}
    logger.info("Webhook %s recibido: %s", proveedor, str(payload)[:300])

    # La transacción se localiza por la referencia externa del proveedor
    tx = _localizar_transaccion(proveedor, payload)
    if not tx:
        # Se responde 200 para que la pasarela no reintente indefinidamente
        return jsonify({"msg": "Sin transacción asociada"}), 200

    contexto = tx.contexto.value if hasattr(tx.contexto, "value") else tx.contexto
    try:
        pasarela = (pasarela_de_plataforma(proveedor) if contexto == "suscripcion"
                    else pasarela_de_gimnasio(tx.id_gimnasio, proveedor)[0])
        resultado = pasarela.interpretar_webhook(payload, dict(request.headers))
    except Exception as exc:
        logger.error("Webhook %s no procesado: %s", proveedor, exc)
        return jsonify({"msg": "Error procesando webhook"}), 200

    if resultado:
        _aplicar_resultado(tx, resultado)
        db.session.commit()

    return jsonify({"msg": "ok"}), 200


# ── Helpers ──────────────────────────────────────────────────────────────────

def _localizar_transaccion(proveedor: str, payload: dict) -> TransaccionPago | None:
    """Encuentra la transacción local referida por el payload del webhook."""
    recurso = (payload or {}).get("resource", {}) or {}
    candidatos = [
        recurso.get("id"),
        (recurso.get("supplementary_data", {}) or {}).get("related_ids", {}).get("order_id"),
        ((payload or {}).get("data") or {}).get("id"),
        (payload or {}).get("id"),
    ]
    # PayPal envía reference_id = id de nuestra transacción
    ref_local = None
    try:
        ref_local = (recurso.get("purchase_units") or [{}])[0].get("reference_id")
    except (IndexError, AttributeError):
        pass
    if ref_local and str(ref_local).isdigit():
        tx = TransaccionPago.query.get(int(ref_local))
        if tx:
            return tx
    for c in candidatos:
        if not c:
            continue
        tx = TransaccionPago.query.filter_by(referencia_externa=str(c)).first()
        if tx:
            return tx
    # Mercado Pago: external_reference lleva el id de nuestra transacción
    ext = (payload or {}).get("external_reference")
    if ext and str(ext).isdigit():
        return TransaccionPago.query.get(int(ext))
    return None


def _aplicar_resultado(tx: TransaccionPago, resultado) -> None:
    """Vuelca el resultado de la pasarela sobre la transacción local."""
    if not resultado or not resultado.estado:
        return
    anterior = tx.estado.value if hasattr(tx.estado, "value") else tx.estado
    tx.estado = resultado.estado
    if resultado.referencia_pago:
        tx.referencia_pago = str(resultado.referencia_pago)[:120]
    if resultado.estado == "aprobado" and not tx.fecha_pago:
        tx.fecha_pago = datetime.now(timezone.utc)

    meta = dict(tx.metadatos or {})
    meta["ultimo_estado_pasarela"] = resultado.datos.get("status") if resultado.datos else None
    tx.metadatos = meta

    if anterior != "aprobado" and resultado.estado == "aprobado":
        _registrar_pago_aprobado(tx)


def _registrar_pago_aprobado(tx: TransaccionPago) -> None:
    """
    Efecto de negocio al aprobarse un pago; se ejecuta UNA sola vez por
    transacción (al pasar de no-aprobado a aprobado):

        membresia    renueva la membresía del miembro y registra el pago
        producto     registra la venta y descuenta inventario
        suscripcion  activa la suscripción SaaS del gimnasio

    Es idempotente: si ya se aplicó (marca en metadatos), no vuelve a hacerlo.
    """
    contexto = tx.contexto.value if hasattr(tx.contexto, "value") else tx.contexto
    meta = dict(tx.metadatos or {})
    if meta.get("aplicado"):
        return

    try:
        if contexto == "suscripcion" and tx.referencia_local:
            from app.models.pg.suscripcion import Suscripcion
            sus = Suscripcion.query.get(int(tx.referencia_local))
            if sus:
                sus.estado = "active"

        elif contexto == "membresia":
            _aplicar_membresia(tx, meta)

        elif contexto == "producto":
            _aplicar_venta(tx, meta)

        meta["aplicado"] = True
        tx.metadatos = meta
    except Exception as exc:
        logger.exception("No se pudo aplicar el efecto del pago %s: %s", tx.id, exc)


def _aplicar_membresia(tx: TransaccionPago, meta: dict) -> None:
    """
    Renueva la membresía pagada: cierra la vigente, crea la nueva y deja el
    pago en el historial. Replica la lógica de /api/user/membership/renew.
    """
    from datetime import timedelta
    from app.mongo import get_db
    from app.models.pg.tipo_membresia import TipoMembresia

    mdb = get_db()

    # El plan comprado viaja en referencia_local (id del TipoMembresia)
    tm = None
    if tx.referencia_local:
        try:
            tm = TipoMembresia.query.filter_by(
                id=int(tx.referencia_local), id_gimnasio=tx.id_gimnasio).first()
        except (TypeError, ValueError):
            tm = None
    if not tm:
        logger.error("Pago %s: no se encontró el tipo de membresía %s",
                     tx.id, tx.referencia_local)
        return

    # A quién se le renueva: el miembro indicado por el cobrador, o quien pagó
    miembro = None
    id_miembro_mongo = meta.get("id_miembro_mongo")
    if id_miembro_mongo:
        from bson.objectid import ObjectId
        try:
            miembro = mdb.miembros.find_one({"_id": ObjectId(str(id_miembro_mongo))})
        except Exception:
            miembro = None
    if not miembro and tx.id_usuario:
        miembro = mdb.miembros.find_one({
            "id_usuario_pg": tx.id_usuario, "id_gimnasio_pg": tx.id_gimnasio})
    if not miembro:
        logger.error("Pago %s: no se identificó al miembro a renovar", tx.id)
        return

    # La nueva vigencia arranca al terminar la actual, si sigue vigente
    ahora = datetime.now()
    fecha_inicio = ahora
    activa = mdb.miembro_membresia.find_one(
        {"id_miembro": miembro["_id"], "estado": "Activa"})
    if activa:
        fin = activa.get("fecha_fin")
        if isinstance(fin, str):
            try:
                fin = datetime.strptime(fin[:10], "%Y-%m-%d")
            except ValueError:
                fin = None
        if isinstance(fin, datetime) and fin > ahora:
            fecha_inicio = fin + timedelta(days=1)
        mdb.miembro_membresia.update_one(
            {"_id": activa["_id"]}, {"$set": {"estado": "Vencida"}})

    duracion = tm.duracion_meses or 1
    mdb.miembro_membresia.insert_one({
        "id_miembro":   miembro["_id"],
        "id_membresia": tm.id,
        "fecha_inicio": fecha_inicio,
        "fecha_fin":    fecha_inicio + timedelta(days=duracion * 30),
        "estado":       "Activa",
    })

    proveedor = tx.proveedor.value if hasattr(tx.proveedor, "value") else tx.proveedor
    mdb.pagos.insert_one({
        "id_miembro":  miembro["_id"],
        "id_gimnasio": tx.id_gimnasio,
        "monto":       float(tx.monto),
        "metodo_pago": "PayPal" if proveedor == "paypal" else "Mercado Pago",
        "concepto":    f"Renovación {tm.nombre}",
        "fecha_pago":  datetime.now(),
        "referencia_pasarela": tx.referencia_pago or tx.referencia_externa,
        "id_transaccion":      tx.id,
    })
    logger.info("Pago %s: membresía '%s' renovada al miembro %s",
                tx.id, tm.nombre, miembro.get("nombre"))


def _aplicar_venta(tx: TransaccionPago, meta: dict) -> None:
    """
    Registra la venta pagada en línea y descuenta el inventario, incluyendo
    los componentes de los combos.
    """
    from bson.objectid import ObjectId
    from app.mongo import get_db

    items = meta.get("items") or []
    if not items:
        logger.warning("Pago %s: venta sin artículos, no se registra", tx.id)
        return

    mdb = get_db()
    proveedor = tx.proveedor.value if hasattr(tx.proveedor, "value") else tx.proveedor

    venta = {
        "id_gimnasio":    tx.id_gimnasio,
        "items":          items,
        "total":          float(tx.monto),
        "metodo_pago":    "PayPal" if proveedor == "paypal" else "Mercado Pago",
        "id_miembro":     meta.get("id_miembro"),
        "nombre_miembro": meta.get("nombre_miembro", ""),
        "fecha":          datetime.now(),
        "referencia_pasarela": tx.referencia_pago or tx.referencia_externa,
        "id_transaccion":      tx.id,
    }
    mdb.ventas.insert_one(venta)

    # Descuento de inventario (los combos descuentan sus componentes)
    for item in items:
        try:
            oid = ObjectId(str(item["id"]))
            qty = max(1, int(item.get("qty", 1)))
            prod = mdb.productos.find_one(
                {"_id": oid, "id_gimnasio": tx.id_gimnasio},
                {"es_combo": 1, "items_combo": 1})
            if prod and prod.get("es_combo"):
                for comp in (prod.get("items_combo") or []):
                    try:
                        mdb.productos.update_one(
                            {"_id": ObjectId(str(comp["id_producto"])),
                             "id_gimnasio": tx.id_gimnasio},
                            [{"$set": {"stock": {"$max": [0, {"$subtract": [
                                "$stock", max(1, int(comp.get("cantidad", 1))) * qty]}]}}}])
                    except Exception:
                        continue
            else:
                mdb.productos.update_one(
                    {"_id": oid, "id_gimnasio": tx.id_gimnasio},
                    [{"$set": {"stock": {"$max": [0, {"$subtract": ["$stock", qty]}]}}}])
        except Exception:
            continue

    logger.info("Pago %s: venta registrada por %s", tx.id, tx.monto)
