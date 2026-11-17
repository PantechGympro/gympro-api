"""
Bandeja "Pagos por confirmar" (nov/2026).

El miembro puede declarar pagos manuales (Efectivo / Transferencia / TotalPass en
la tienda; Efectivo / Transferencia en renovaciones). Esos pagos nacen PENDIENTES
y aquí el personal que maneja dinero -- Recepcionista, Administrador y Propietario
-- los verifica contra la caja o el estado de cuenta y los confirma o rechaza.

  GET  /api/confirmaciones-pago                          -> pendientes (ventas + renovaciones)
  GET  /api/confirmaciones-pago/resumen                  -> solo contadores (insignia del menú)
  POST /api/confirmaciones-pago/ventas/<id>/confirmar
  POST /api/confirmaciones-pago/ventas/<id>/rechazar          {motivo?}
  POST /api/confirmaciones-pago/renovaciones/<id>/confirmar
  POST /api/confirmaciones-pago/renovaciones/<id>/rechazar    {motivo?}

PayPal y Mercado Pago no pasan por aquí: los verifica el webhook del proveedor.
La lógica de cada transición vive en utils/pagos_pendientes.py (misma que usa
PATCH /api/ventas/<id>/entregar al "cobrar y entregar").
"""
from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity
from bson import ObjectId

from app.mongo import get_db
from app.utils.tenant import require_tenant
from app.utils.recibos import registrar_evento_transaccion
from app.utils.pagos_pendientes import (
    PAGO_PENDIENTE, puede_confirmar_pago,
    confirmar_venta, rechazar_venta, confirmar_renovacion, rechazar_renovacion,
)

confirmaciones_pago_bp = Blueprint("confirmaciones_pago", __name__, url_prefix="/api/confirmaciones-pago")

MOTIVO_MAX = 200


def _sin_permiso():
    if not puede_confirmar_pago(get_jwt()):
        return jsonify({
            "error": "Solo recepción, administración o el propietario pueden confirmar pagos.",
        }), 403
    return None


def _actor():
    claims = get_jwt()
    try:
        uid = int(get_jwt_identity())
    except (TypeError, ValueError):
        uid = None
    return uid, (claims.get("nombre") or "Personal del gimnasio")


def _oid(valor):
    try:
        return ObjectId(valor)
    except Exception:
        return None


def _iso(dt):
    return dt.isoformat() if hasattr(dt, "isoformat") else None


def _venta_dto(v):
    return {
        "tipo": "venta",
        "id": str(v["_id"]),
        "nombre_miembro": v.get("nombre_miembro", ""),
        "items": v.get("items", []),
        "total": v.get("total", 0),
        "metodo_pago": v.get("metodo_pago"),
        "referencia": v.get("referencia") or None,
        "fecha": _iso(v.get("fecha")),
        "fecha_recogida": v.get("fecha_recogida"),
        "hora_recogida": v.get("hora_recogida"),
    }


def _renovacion_dto(r):
    return {
        "tipo": "renovacion",
        "id": str(r["_id"]),
        "nombre_miembro": r.get("nombre_miembro", ""),
        "membresia_nombre": r.get("membresia_nombre"),
        "total": r.get("monto", 0),
        "metodo_pago": r.get("metodo_pago"),
        "referencia": r.get("referencia") or None,
        "fecha": _iso(r.get("creado_en")),
    }


@confirmaciones_pago_bp.route("", methods=["GET"])
@jwt_required()
@require_tenant
def listar_pendientes():
    err = _sin_permiso()
    if err:
        return err
    db, gym_id = get_db(), g.tenant_id
    ventas = [_venta_dto(v) for v in db.ventas.find(
        {"id_gimnasio": gym_id, "estado_pago": PAGO_PENDIENTE}).sort("fecha", 1).limit(200)]
    renovaciones = [_renovacion_dto(r) for r in db.renovaciones_pendientes.find(
        {"id_gimnasio": gym_id, "estado": PAGO_PENDIENTE}).sort("creado_en", 1).limit(200)]
    return jsonify({
        "ventas": ventas, "renovaciones": renovaciones,
        "total": len(ventas) + len(renovaciones),
    }), 200


@confirmaciones_pago_bp.route("/resumen", methods=["GET"])
@jwt_required()
@require_tenant
def resumen():
    err = _sin_permiso()
    if err:
        return err
    db, gym_id = get_db(), g.tenant_id
    n_ventas = db.ventas.count_documents({"id_gimnasio": gym_id, "estado_pago": PAGO_PENDIENTE})
    n_ren = db.renovaciones_pendientes.count_documents({"id_gimnasio": gym_id, "estado": PAGO_PENDIENTE})
    return jsonify({"ventas": n_ventas, "renovaciones": n_ren, "total": n_ventas + n_ren}), 200


def _motivo():
    m = ((request.get_json(silent=True) or {}).get("motivo") or "").strip()
    return m[:MOTIVO_MAX] or None


def _bitacora(accion, gym_id, uid, detalle):
    try:
        registrar_evento_transaccion(accion, id_usuario_actor=uid, id_gimnasio=gym_id, detalle=detalle)
    except Exception as e:
        print(f"[confirmaciones_pago] bitácora: {e}")


def _ya_resuelto():
    return jsonify({"error": "Este pago ya fue resuelto por otra persona. Actualiza la lista.",
                    "codigo": "ya_resuelto"}), 409


@confirmaciones_pago_bp.route("/ventas/<venta_id>/confirmar", methods=["POST"])
@jwt_required()
@require_tenant
def confirmar_pago_venta(venta_id):
    err = _sin_permiso()
    if err:
        return err
    db, gym_id = get_db(), g.tenant_id
    oid = _oid(venta_id)
    venta = db.ventas.find_one({"_id": oid, "id_gimnasio": gym_id}) if oid else None
    if not venta:
        return jsonify({"error": "Compra no encontrada"}), 404
    uid, nombre = _actor()
    if not confirmar_venta(db, venta, uid, nombre):
        return _ya_resuelto()
    _bitacora("pos.pago_confirmado", gym_id, uid,
              {"venta_id": venta_id, "total": venta.get("total"), "metodo_pago": venta.get("metodo_pago"),
               "referencia": venta.get("referencia") or None})
    return jsonify({"ok": True, "estado_pago": "pagado"}), 200


@confirmaciones_pago_bp.route("/ventas/<venta_id>/rechazar", methods=["POST"])
@jwt_required()
@require_tenant
def rechazar_pago_venta(venta_id):
    err = _sin_permiso()
    if err:
        return err
    db, gym_id = get_db(), g.tenant_id
    oid = _oid(venta_id)
    venta = db.ventas.find_one({"_id": oid, "id_gimnasio": gym_id}) if oid else None
    if not venta:
        return jsonify({"error": "Compra no encontrada"}), 404
    uid, nombre = _actor()
    motivo = _motivo()
    if not rechazar_venta(db, venta, uid, nombre, motivo):
        return _ya_resuelto()
    _bitacora("pos.pago_rechazado", gym_id, uid,
              {"venta_id": venta_id, "total": venta.get("total"), "metodo_pago": venta.get("metodo_pago"),
               "motivo": motivo})
    return jsonify({"ok": True, "estado_pago": "rechazado"}), 200


@confirmaciones_pago_bp.route("/renovaciones/<solicitud_id>/confirmar", methods=["POST"])
@jwt_required()
@require_tenant
def confirmar_pago_renovacion(solicitud_id):
    err = _sin_permiso()
    if err:
        return err
    db, gym_id = get_db(), g.tenant_id
    oid = _oid(solicitud_id)
    sol = db.renovaciones_pendientes.find_one({"_id": oid, "id_gimnasio": gym_id}) if oid else None
    if not sol:
        return jsonify({"error": "Solicitud no encontrada"}), 404
    uid, nombre = _actor()
    act = confirmar_renovacion(db, sol, uid, nombre)
    if act is None:
        return _ya_resuelto()
    _bitacora("membresia.renovacion_confirmada", gym_id, uid,
              {"solicitud_id": solicitud_id, "id_membresia": sol.get("id_membresia"),
               "monto": sol.get("monto"), "metodo_pago": sol.get("metodo_pago"),
               "referencia": sol.get("referencia"), "id_miembro": str(sol.get("id_miembro"))})
    return jsonify({
        "ok": True,
        "membresia": {
            "nombre": sol.get("membresia_nombre"),
            "fechaInicio": act["fecha_inicio"].strftime("%Y-%m-%d"),
            "fechaFin": act["fecha_fin"].strftime("%Y-%m-%d"),
        },
    }), 200


@confirmaciones_pago_bp.route("/renovaciones/<solicitud_id>/rechazar", methods=["POST"])
@jwt_required()
@require_tenant
def rechazar_pago_renovacion(solicitud_id):
    err = _sin_permiso()
    if err:
        return err
    db, gym_id = get_db(), g.tenant_id
    oid = _oid(solicitud_id)
    sol = db.renovaciones_pendientes.find_one({"_id": oid, "id_gimnasio": gym_id}) if oid else None
    if not sol:
        return jsonify({"error": "Solicitud no encontrada"}), 404
    uid, nombre = _actor()
    motivo = _motivo()
    if not rechazar_renovacion(db, sol, uid, nombre, motivo):
        return _ya_resuelto()
    _bitacora("membresia.renovacion_rechazada", gym_id, uid,
              {"solicitud_id": solicitud_id, "monto": sol.get("monto"),
               "metodo_pago": sol.get("metodo_pago"), "motivo": motivo})
    return jsonify({"ok": True}), 200
