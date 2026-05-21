"""
owner_gym/owner_dashboard.py — Dashboard KPIs del Owner de Gimnasio.

Endpoints:
    GET /api/owner_gym/dashboard         KPIs principales del gimnasio
    GET /api/owner_gym/dashboard/ingresos Ingresos últimos N meses
    GET /api/owner_gym/dashboard/actividad Actividad reciente (pagos + registros)
"""
from flask import Blueprint, jsonify, request, g
from flask_jwt_extended import jwt_required
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

from app.mongo import get_db
from app.extensions import db
from app.models.pg.usuario      import Usuario
from app.models.pg.tipo_membresia import TipoMembresia
from app.utils.tenant import require_tenant
from app.utils.security import require_role

owner_dashboard_bp = Blueprint("owner_dashboard", __name__)


def _month_range(year: int, month: int):
    """Devuelve (inicio, fin) del mes como datetime."""
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end   = start + relativedelta(months=1)
    return start, end


def _prev_months(n: int):
    """Últimos n meses en orden ascendente → lista de (year, month)."""
    now    = datetime.now(timezone.utc)
    result = []
    for i in range(n - 1, -1, -1):
        d = now - relativedelta(months=i)
        result.append((d.year, d.month))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/owner_gym/dashboard
# ─────────────────────────────────────────────────────────────────────────────
@owner_dashboard_bp.route("/dashboard", methods=["GET"])
@jwt_required()
@require_role("owner_gym")
@require_tenant
def get_owner_dashboard():
    """KPIs principales: miembros activos/inactivos, ingresos, membresías, staff."""
    gym_id = g.tenant_id
    mdb    = get_db()
    now    = datetime.now(timezone.utc)

    # ── Miembros ──────────────────────────────────────────────────────────────
    activos   = mdb.miembros.count_documents({"id_gimnasio_pg": gym_id, "estado": "Activo"})
    inactivos = mdb.miembros.count_documents({"id_gimnasio_pg": gym_id, "estado": "Inactivo"})
    total_miembros = activos + inactivos

    # Nuevos miembros este mes
    start_mes, end_mes = _month_range(now.year, now.month)
    nuevos_mes = mdb.miembros.count_documents({
        "id_gimnasio_pg": gym_id,
        "$or": [
            {"fecha_registro": {"$gte": start_mes.isoformat(), "$lt": end_mes.isoformat()}},
            {"fecha_registro": {"$gte": start_mes, "$lt": end_mes}},
        ]
    })

    # Membresías próximas a vencer (próximos 7 días)
    hoy        = now.date().isoformat()
    en_7_dias  = (now + relativedelta(days=7)).date().isoformat()
    por_vencer = mdb.miembro_membresias.count_documents({
        "id_gimnasio": gym_id,
        "estado": "activa",
        "fecha_fin": {"$gte": hoy, "$lte": en_7_dias},
    })

    # ── Ingresos del mes actual ───────────────────────────────────────────────
    pipeline_mes = [
        {"$match": {"id_gimnasio": gym_id}},
        {"$addFields": {"fecha_dt": {"$toDate": "$fecha_pago"}}},
        {"$match": {"fecha_dt": {"$gte": start_mes, "$lt": end_mes}}},
        {"$group": {"_id": None, "total": {"$sum": "$monto"}}},
    ]
    res = list(mdb.pagos.aggregate(pipeline_mes))
    ingresos_mes = float(res[0]["total"]) if res else 0.0

    # Ingresos mes anterior (para calcular variación)
    prev_start, prev_end = _month_range(
        (now - relativedelta(months=1)).year,
        (now - relativedelta(months=1)).month,
    )
    pipeline_prev = [
        {"$match": {"id_gimnasio": gym_id}},
        {"$addFields": {"fecha_dt": {"$toDate": "$fecha_pago"}}},
        {"$match": {"fecha_dt": {"$gte": prev_start, "$lt": prev_end}}},
        {"$group": {"_id": None, "total": {"$sum": "$monto"}}},
    ]
    res_prev = list(mdb.pagos.aggregate(pipeline_prev))
    ingresos_prev = float(res_prev[0]["total"]) if res_prev else 0.0

    variacion_ingresos = 0.0
    if ingresos_prev > 0:
        variacion_ingresos = round(((ingresos_mes - ingresos_prev) / ingresos_prev) * 100, 1)

    # ── Staff (entrenadores + recepcionistas) ─────────────────────────────────
    from app.models.pg.rol import Rol
    rol_trainer = Rol.query.filter_by(nombre="Entrenador").first()
    rol_recep   = Rol.query.filter_by(nombre="Recepcionista").first()

    entrenadores   = 0
    recepcionistas = 0
    if rol_trainer:
        entrenadores = Usuario.query.filter_by(id_gimnasio=gym_id, id_rol=rol_trainer.id, activo=True).count()
    if rol_recep:
        recepcionistas = Usuario.query.filter_by(id_gimnasio=gym_id, id_rol=rol_recep.id, activo=True).count()

    # ── Tipos de membresía activos ────────────────────────────────────────────
    tipos_membresia = TipoMembresia.query.filter_by(id_gimnasio=gym_id, activo=True).count()

    # ── Ventas del mes (POS) ──────────────────────────────────────────────────
    pipeline_ventas = [
        {"$match": {"id_gimnasio": gym_id}},
        {"$addFields": {"fecha_dt": {"$toDate": "$fecha"}}},
        {"$match": {"fecha_dt": {"$gte": start_mes, "$lt": end_mes}}},
        {"$group": {"_id": None, "total": {"$sum": "$total"}, "count": {"$sum": 1}}},
    ]
    res_v = list(mdb.ventas.aggregate(pipeline_ventas))
    ventas_mes        = float(res_v[0]["total"]) if res_v else 0.0
    ventas_mes_count  = int(res_v[0]["count"])   if res_v else 0

    return jsonify({
        "miembros": {
            "activos":      activos,
            "inactivos":    inactivos,
            "total":        total_miembros,
            "nuevos_mes":   nuevos_mes,
            "por_vencer":   por_vencer,
        },
        "ingresos": {
            "mes_actual":   ingresos_mes,
            "mes_anterior": ingresos_prev,
            "variacion_pct": variacion_ingresos,
        },
        "ventas_pos": {
            "total_mes":    ventas_mes,
            "transacciones": ventas_mes_count,
        },
        "staff": {
            "entrenadores":    entrenadores,
            "recepcionistas":  recepcionistas,
        },
        "tipos_membresia": tipos_membresia,
    }), 200


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/owner_gym/dashboard/ingresos?meses=6
# ─────────────────────────────────────────────────────────────────────────────
@owner_dashboard_bp.route("/dashboard/ingresos", methods=["GET"])
@jwt_required()
@require_role("owner_gym")
@require_tenant
def get_ingresos_historicos():
    """Ingresos por mes (pagos + ventas POS) para el gráfico de tendencia."""
    gym_id = g.tenant_id
    mdb    = get_db()
    meses  = min(12, max(2, request.args.get("meses", 6, type=int)))
    months = _prev_months(meses)

    oldest = datetime(months[0][0], months[0][1], 1, tzinfo=timezone.utc)

    # Pagos de membresías
    pipeline_pagos = [
        {"$match": {"id_gimnasio": gym_id}},
        {"$addFields": {"fecha_dt": {"$toDate": "$fecha_pago"}}},
        {"$match": {"fecha_dt": {"$gte": oldest}}},
        {"$group": {
            "_id":   {"year": {"$year": "$fecha_dt"}, "month": {"$month": "$fecha_dt"}},
            "total": {"$sum": "$monto"},
        }},
    ]
    pagos_map = {
        (r["_id"]["year"], r["_id"]["month"]): float(r["total"])
        for r in mdb.pagos.aggregate(pipeline_pagos)
    }

    # Ventas POS
    pipeline_ventas = [
        {"$match": {"id_gimnasio": gym_id}},
        {"$addFields": {"fecha_dt": {"$toDate": "$fecha"}}},
        {"$match": {"fecha_dt": {"$gte": oldest}}},
        {"$group": {
            "_id":   {"year": {"$year": "$fecha_dt"}, "month": {"$month": "$fecha_dt"}},
            "total": {"$sum": "$total"},
        }},
    ]
    ventas_map = {
        (r["_id"]["year"], r["_id"]["month"]): float(r["total"])
        for r in mdb.ventas.aggregate(pipeline_ventas)
    }

    MONTHS_ES = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
    result = []
    for y, m in months:
        result.append({
            "label":    f"{MONTHS_ES[m-1]} {y}",
            "pagos":    pagos_map.get((y, m), 0.0),
            "ventas":   ventas_map.get((y, m), 0.0),
            "total":    pagos_map.get((y, m), 0.0) + ventas_map.get((y, m), 0.0),
        })

    return jsonify(result), 200


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/owner_gym/dashboard/actividad?limit=10
# ─────────────────────────────────────────────────────────────────────────────
@owner_dashboard_bp.route("/dashboard/actividad", methods=["GET"])
@jwt_required()
@require_role("owner_gym")
@require_tenant
def get_actividad_reciente():
    """Feed de actividad reciente: últimos pagos, registros de miembros y ventas POS."""
    from bson import ObjectId

    gym_id = g.tenant_id
    mdb    = get_db()
    limit  = min(30, request.args.get("limit", 20, type=int))

    # ── Últimos pagos de membresías ───────────────────────────────────────────
    pagos = list(
        mdb.pagos.find({"id_gimnasio": gym_id})
        .sort("fecha_pago", -1).limit(limit)
    )

    # Batch-lookup de nombres: pagos guardan id_miembro (ObjectId) sin nombre
    ids_sin_nombre = set()
    for p in pagos:
        if not (p.get("nombre_miembro") or "").strip():
            raw = p.get("id_miembro")
            if raw:
                try:
                    ids_sin_nombre.add(ObjectId(str(raw)))
                except Exception:
                    pass

    nombre_cache: dict = {}
    if ids_sin_nombre:
        for m in mdb.miembros.find({"_id": {"$in": list(ids_sin_nombre)}}):
            full = f"{m.get('nombre', '')} {m.get('apellido', '')}".strip()
            nombre_cache[str(m["_id"])] = full or "—"

    # ── Últimos miembros registrados ──────────────────────────────────────────
    miembros = list(
        mdb.miembros.find({"id_gimnasio_pg": gym_id})
        .sort("fecha_registro", -1).limit(limit)
    )

    # ── Últimas ventas POS ────────────────────────────────────────────────────
    ventas_pos = list(
        mdb.ventas.find({"id_gimnasio": gym_id})
        .sort("fecha", -1).limit(limit)
    )

    # ── Construir feed ────────────────────────────────────────────────────────
    actividad = []

    for p in pagos:
        nombre = (p.get("nombre_miembro") or "").strip()
        if not nombre:
            raw = p.get("id_miembro")
            nombre = nombre_cache.get(str(raw), "—") if raw else "—"
        actividad.append({
            "tipo":   "pago",
            "titulo": nombre,
            "sub":    p.get("metodo_pago", ""),
            "monto":  float(p.get("monto", 0)),
            "fecha":  str(p.get("fecha_pago", "")),
        })

    for m in miembros:
        nombre = f"{m.get('nombre', '—')} {m.get('apellido', '')}".strip()
        actividad.append({
            "tipo":   "registro",
            "titulo": nombre,
            "sub":    m.get("estado", ""),
            "fecha":  str(m.get("fecha_registro", "")),
        })

    for v in ventas_pos:
        nombre = (v.get("nombre_miembro") or "").strip() or "Cliente general"
        items  = v.get("items", [])
        resumen = items[0].get("nombre", "Venta") if items else "Venta"
        if len(items) > 1:
            resumen = f"{resumen} +{len(items) - 1} más"
        fecha_v = v.get("fecha")
        fecha_str = fecha_v.isoformat() if hasattr(fecha_v, "isoformat") else str(fecha_v or "")
        actividad.append({
            "tipo":   "venta",
            "titulo": nombre,
            "sub":    resumen,
            "monto":  float(v.get("total", 0)),
            "fecha":  fecha_str,
        })

    actividad.sort(key=lambda x: x.get("fecha", ""), reverse=True)
    return jsonify(actividad[:limit]), 200
