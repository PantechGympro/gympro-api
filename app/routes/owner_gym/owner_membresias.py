"""
owner_gym/owner_membresias.py — CRUD de Tipos de Membresía del gimnasio.

Consolida la gestión de planes de membresía bajo el prefijo /api/owner_gym.

Endpoints:
    GET    /api/owner_gym/membresias            Listar todos (activos + inactivos)
    POST   /api/owner_gym/membresias            Crear nuevo tipo
    PUT    /api/owner_gym/membresias/<id>       Editar
    PATCH  /api/owner_gym/membresias/<id>/toggle Activar / desactivar
    DELETE /api/owner_gym/membresias/<id>       Eliminar (solo si sin usos)
"""
from flask import Blueprint, jsonify, request, g, current_app
from flask_jwt_extended import jwt_required

from app.extensions import db
from app.models.pg.tipo_membresia import TipoMembresia
from app.utils.tenant import require_tenant
from app.utils.security import require_role

owner_membresias_bp = Blueprint("owner_membresias", __name__)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/owner_gym/membresias
# ─────────────────────────────────────────────────────────────────────────────
@owner_membresias_bp.route("/membresias", methods=["GET"])
@jwt_required()
@require_role("owner_gym")
@require_tenant
def listar_membresias():
    gym_id       = g.tenant_id
    solo_activos = request.args.get("activos", "false").lower() == "true"
    filtro_tipo  = request.args.get("tipo", "").lower().strip()

    # Antes de listar se retiran de circulación las promociones ya vencidas.
    _desactivar_promociones_caducadas(gym_id)

    q = TipoMembresia.query.filter_by(id_gimnasio=gym_id)
    if solo_activos:
        q = q.filter_by(activo=True)
    if filtro_tipo in {"estandar", "promocion"}:
        q = q.filter_by(tipo=filtro_tipo)

    items = q.order_by(TipoMembresia.precio).all()
    return jsonify([m.to_dict() for m in items]), 200


def _desactivar_promociones_caducadas(gym_id: int) -> int:
    """
    Desactiva las promociones cuya fecha de fin ya pasó.

    Se ejecuta al consultar el catálogo, de modo que una promoción de tiempo
    limitado deja de ofrecerse sola, sin depender de un proceso programado.
    Devuelve cuántas se desactivaron.
    """
    from datetime import date
    vencidas = (TipoMembresia.query
                .filter(TipoMembresia.id_gimnasio == gym_id,
                        TipoMembresia.activo.is_(True),
                        TipoMembresia.fecha_fin_promo.isnot(None),
                        TipoMembresia.fecha_fin_promo < date.today())
                .all())
    if not vencidas:
        return 0
    for m in vencidas:
        m.activo = False
        current_app.logger.info(
            "Promoción '%s' (gym %s) desactivada: venció el %s",
            m.nombre, gym_id, m.fecha_fin_promo)
    db.session.commit()
    return len(vencidas)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/owner_gym/membresias
# ─────────────────────────────────────────────────────────────────────────────
@owner_membresias_bp.route("/membresias", methods=["POST"])
@jwt_required()
@require_role("owner_gym")
@require_tenant
def crear_membresia():
    """
    Body JSON:
        {
            "nombre":         "Mensual Plus",
            "duracion_meses": 1,
            "precio":         499.00,
            "descripcion":    "Acceso completo + clases grupales"
        }
    """
    TIPOS_VALIDOS = {"estandar", "promocion"}

    gym_id = g.tenant_id
    data   = request.get_json() or {}

    nombre         = (data.get("nombre") or "").strip()
    duracion_meses = data.get("duracion_meses")
    precio         = data.get("precio")
    tipo           = (data.get("tipo") or "estandar").lower().strip()

    if not nombre:
        return jsonify({"msg": "El campo 'nombre' es requerido"}), 400
    if tipo not in TIPOS_VALIDOS:
        return jsonify({"msg": f"tipo debe ser uno de: {', '.join(TIPOS_VALIDOS)}"}), 400
    try:
        duracion_meses = int(duracion_meses)
        precio         = float(precio)
        if duracion_meses < 1 or precio < 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"msg": "duracion_meses (entero ≥1) y precio (decimal ≥0) son requeridos"}), 400

    if TipoMembresia.query.filter_by(id_gimnasio=gym_id, nombre=nombre).first():
        return jsonify({"msg": f"Ya existe una membresía con el nombre '{nombre}'"}), 409

    nueva = TipoMembresia(
        id_gimnasio    = gym_id,
        nombre         = nombre,
        tipo           = tipo,
        duracion_meses = duracion_meses,
        precio         = precio,
        descripcion    = (data.get("descripcion") or "").strip() or None,
        activo         = True,
        beneficios     = _limpiar_beneficios(data.get("beneficios")),
        es_combo       = bool(data.get("es_combo")),
        items_combo    = _limpiar_items_combo(data.get("items_combo")),
        fecha_fin_promo= _parsear_fecha(data.get("fecha_fin_promo")),
    )
    db.session.add(nueva)
    db.session.commit()
    return jsonify(nueva.to_dict()), 201


# ── Helpers de normalización ────────────────────────────────────────────────

def _limpiar_beneficios(valor):
    """Normaliza la lista de beneficios: textos no vacíos, sin duplicar, máx. 12."""
    if not isinstance(valor, list):
        return []
    vistos, salida = set(), []
    for b in valor:
        texto = str(b or "").strip()[:120]
        if texto and texto.lower() not in vistos:
            vistos.add(texto.lower())
            salida.append(texto)
    return salida[:12]


def _limpiar_items_combo(valor):
    """
    Normaliza los conceptos de un combo.
    Cada item: {"nombre": str, "cantidad": int, "id_producto": int|None}
    """
    if not isinstance(valor, list):
        return []
    salida = []
    for it in valor[:20]:
        if not isinstance(it, dict):
            continue
        nombre = str(it.get("nombre") or "").strip()[:120]
        if not nombre:
            continue
        try:
            cantidad = max(1, int(it.get("cantidad") or 1))
        except (TypeError, ValueError):
            cantidad = 1
        item = {"nombre": nombre, "cantidad": cantidad}
        if it.get("id_producto") not in (None, ""):
            try:
                item["id_producto"] = int(it["id_producto"])
            except (TypeError, ValueError):
                pass
        salida.append(item)
    return salida


def _parsear_fecha(valor):
    """Convierte 'YYYY-MM-DD' a date; None si viene vacío o mal formado."""
    if not valor:
        return None
    from datetime import datetime as _dt
    try:
        return _dt.strptime(str(valor)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# PUT /api/owner_gym/membresias/<id>
# ─────────────────────────────────────────────────────────────────────────────
@owner_membresias_bp.route("/membresias/<int:mem_id>", methods=["PUT"])
@jwt_required()
@require_role("owner_gym")
@require_tenant
def editar_membresia(mem_id: int):
    gym_id = g.tenant_id
    mem    = TipoMembresia.query.filter_by(id=mem_id, id_gimnasio=gym_id).first()
    if not mem:
        return jsonify({"msg": "Membresía no encontrada"}), 404

    data = request.get_json() or {}

    if "nombre" in data:
        nombre = data["nombre"].strip()
        if not nombre:
            return jsonify({"msg": "El nombre no puede estar vacío"}), 400
        existing = TipoMembresia.query.filter_by(id_gimnasio=gym_id, nombre=nombre).first()
        if existing and existing.id != mem_id:
            return jsonify({"msg": f"Ya existe otra membresía con el nombre '{nombre}'"}), 409
        mem.nombre = nombre

    if "duracion_meses" in data:
        try:
            mem.duracion_meses = int(data["duracion_meses"])
        except (ValueError, TypeError):
            return jsonify({"msg": "duracion_meses debe ser un entero"}), 400

    if "precio" in data:
        try:
            mem.precio = float(data["precio"])
        except (ValueError, TypeError):
            return jsonify({"msg": "precio debe ser un número"}), 400

    if "tipo" in data:
        t = (data["tipo"] or "estandar").lower().strip()
        if t not in {"estandar", "promocion"}:
            return jsonify({"msg": "tipo debe ser 'estandar' o 'promocion'"}), 400
        mem.tipo = t

    if "descripcion" in data:
        mem.descripcion = (data["descripcion"] or "").strip() or None

    if "beneficios" in data:
        mem.beneficios = _limpiar_beneficios(data["beneficios"])

    if "es_combo" in data:
        mem.es_combo = bool(data["es_combo"])

    if "items_combo" in data:
        mem.items_combo = _limpiar_items_combo(data["items_combo"])

    if "fecha_fin_promo" in data:
        mem.fecha_fin_promo = _parsear_fecha(data["fecha_fin_promo"])
        # Si se extiende la vigencia de una promoción vencida, se reactiva.
        if mem.fecha_fin_promo and not mem.caducada and not mem.activo:
            mem.activo = True

    db.session.commit()
    return jsonify({"msg": "Membresía actualizada", **mem.to_dict()}), 200


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/owner_gym/membresias/<id>/toggle
# ─────────────────────────────────────────────────────────────────────────────
@owner_membresias_bp.route("/membresias/<int:mem_id>/toggle", methods=["PATCH"])
@jwt_required()
@require_role("owner_gym")
@require_tenant
def toggle_membresia(mem_id: int):
    gym_id = g.tenant_id
    mem    = TipoMembresia.query.filter_by(id=mem_id, id_gimnasio=gym_id).first()
    if not mem:
        return jsonify({"msg": "Membresía no encontrada"}), 404

    mem.activo = not mem.activo
    db.session.commit()
    estado = "activada" if mem.activo else "desactivada"
    return jsonify({"msg": f"Membresía {estado}", **mem.to_dict()}), 200


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /api/owner_gym/membresias/<id>
# ─────────────────────────────────────────────────────────────────────────────
@owner_membresias_bp.route("/membresias/<int:mem_id>", methods=["DELETE"])
@jwt_required()
@require_role("owner_gym")
@require_tenant
def eliminar_membresia(mem_id: int):
    """Elimina solo si no tiene miembro_membresias asociadas."""
    from app.mongo import get_db
    gym_id = g.tenant_id
    mem    = TipoMembresia.query.filter_by(id=mem_id, id_gimnasio=gym_id).first()
    if not mem:
        return jsonify({"msg": "Membresía no encontrada"}), 404

    mdb  = get_db()
    usos = mdb.miembro_membresias.count_documents({"id_membresia": mem_id})
    if usos > 0:
        return jsonify({
            "msg":  f"No se puede eliminar: hay {usos} membresía(s) de miembro activas con este tipo.",
            "tip":  "Desactívala en lugar de eliminarla.",
        }), 409

    db.session.delete(mem)
    db.session.commit()
    return jsonify({"msg": "Membresía eliminada"}), 200
