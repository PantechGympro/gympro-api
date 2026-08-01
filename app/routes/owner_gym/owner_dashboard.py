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

    # ── Membresías próximas a vencer (próximos 7 días) ────────────────────────
    # Tres detalles que antes hacían que este contador diera siempre 0:
    #   1. La colección es 'miembro_membresia' (singular), no 'miembro_membresias'.
    #   2. El estado se guarda capitalizado: 'Activa', no 'activa'.
    #   3. La colección NO tiene id_gimnasio; se acota por los miembros del gym.
    # Además fecha_fin convive en dos formatos (datetime y 'YYYY-MM-DD'), así que
    # se consulta con ambos, igual que en el panel de recepción.
    en_7 = now + relativedelta(days=7)
    oids_gym = [m["_id"] for m in mdb.miembros.find({"id_gimnasio_pg": gym_id}, {"_id": 1})]
    por_vencer = mdb.miembro_membresia.count_documents({
        "id_miembro": {"$in": oids_gym},
        "estado":     "Activa",
        "$or": [
            {"fecha_fin": {"$gte": now, "$lte": en_7}},
            {"fecha_fin": {"$gte": now.strftime("%Y-%m-%d"),
                           "$lte": en_7.strftime("%Y-%m-%d")}},
        ],
    })

    # ── Ingresos ──────────────────────────────────────────────────────────────
    # 'Ingresos del mes' es TODO lo que entró: membresías (colección pagos) más
    # el punto de venta (colección ventas). Antes solo se sumaban las membresías,
    # así que el panel mostraba $0 mientras Reportes mostraba el importe real de
    # las ventas: dos pantallas dando cifras distintas del mismo mes.
    prev_start, prev_end = _month_range(
        (now - relativedelta(months=1)).year,
        (now - relativedelta(months=1)).month,
    )

    def _suma(coleccion, campo_fecha: str, campo_monto: str, desde, hasta) -> float:
        """Total de una colección en un rango, tolerando fechas string o datetime."""
        pipeline = [
            {"$match": {"id_gimnasio": gym_id}},
            {"$addFields": {"fecha_dt": {"$toDate": f"${campo_fecha}"}}},
            {"$match": {"fecha_dt": {"$gte": desde, "$lt": hasta}}},
            {"$group": {"_id": None, "total": {"$sum": f"${campo_monto}"}}},
        ]
        r = list(coleccion.aggregate(pipeline))
        return float(r[0]["total"]) if r else 0.0

    ingresos_membresias = _suma(mdb.pagos,  "fecha_pago", "monto", start_mes, end_mes)
    ingresos_pos        = _suma(mdb.ventas, "fecha",      "total", start_mes, end_mes)
    ingresos_mes        = ingresos_membresias + ingresos_pos

    prev_membresias = _suma(mdb.pagos,  "fecha_pago", "monto", prev_start, prev_end)
    prev_pos        = _suma(mdb.ventas, "fecha",      "total", prev_start, prev_end)
    ingresos_prev   = prev_membresias + prev_pos

    variacion_ingresos = 0.0
    if ingresos_prev > 0:
        variacion_ingresos = round(((ingresos_mes - ingresos_prev) / ingresos_prev) * 100, 1)
    elif ingresos_mes > 0:
        # Sin base de comparación no existe un porcentaje: se informa aparte con
        # 'sin_comparativa' para que la app no pinte un engañoso 0 % o -100 %.
        variacion_ingresos = 0.0

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

    # ── Transacciones del POS en el mes ───────────────────────────────────────
    # El importe ya se calculó arriba (ingresos_pos); aquí solo falta el conteo.
    pipeline_conteo = [
        {"$match": {"id_gimnasio": gym_id}},
        {"$addFields": {"fecha_dt": {"$toDate": "$fecha"}}},
        {"$match": {"fecha_dt": {"$gte": start_mes, "$lt": end_mes}}},
        {"$group": {"_id": None, "count": {"$sum": 1}}},
    ]
    res_v = list(mdb.ventas.aggregate(pipeline_conteo))
    ventas_mes_count = int(res_v[0]["count"]) if res_v else 0

    return jsonify({
        "miembros": {
            "activos":      activos,
            "inactivos":    inactivos,
            "total":        total_miembros,
            "nuevos_mes":   nuevos_mes,
            "por_vencer":   por_vencer,
        },
        "ingresos": {
            # mes_actual es el TOTAL (membresías + POS). El desglose permite a la
            # app mostrar de dónde viene cada peso sin volver a pedir datos.
            "mes_actual":     ingresos_mes,
            "membresias":     ingresos_membresias,
            "punto_de_venta": ingresos_pos,
            "mes_anterior":   ingresos_prev,
            "variacion_pct":  variacion_ingresos,
            # True cuando el mes anterior no tuvo ingresos: no hay porcentaje que
            # calcular y la app debe mostrar un guion en vez de 0 % o -100 %.
            "sin_comparativa": ingresos_prev <= 0,
        },
        "ventas_pos": {
            "total_mes":    ingresos_pos,
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
    # Las tres colecciones guardan la fecha en formatos distintos (datetime en
    # unas, cadena ISO en otras). Ordenar por el texto mezclaba el orden porque
    # str(datetime) usa un espacio y isoformat() una 'T', y ' ' < 'T' en ASCII.
    # Se normaliza a ISO para ordenar y para lo que recibe la app.
    def _iso(valor) -> str:
        if valor is None:
            return ""
        if hasattr(valor, "isoformat"):
            return valor.isoformat()
        return str(valor).replace(" ", "T")

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
            "fecha":  _iso(p.get("fecha_pago")),
        })

    for m in miembros:
        nombre = f"{m.get('nombre', '—')} {m.get('apellido', '')}".strip()
        actividad.append({
            "tipo":   "registro",
            "titulo": nombre,
            "sub":    m.get("estado", ""),
            "fecha":  _iso(m.get("fecha_registro")),
        })

    for v in ventas_pos:
        nombre = (v.get("nombre_miembro") or "").strip() or "Cliente general"
        items  = v.get("items", [])
        resumen = items[0].get("nombre", "Venta") if items else "Venta"
        if len(items) > 1:
            resumen = f"{resumen} +{len(items) - 1} más"
        actividad.append({
            "tipo":   "venta",
            "titulo": nombre,
            "sub":    resumen,
            "monto":  float(v.get("total", 0)),
            "fecha":  _iso(v.get("fecha")),
        })

    actividad.sort(key=lambda x: x.get("fecha", ""), reverse=True)
    return jsonify(actividad[:limit]), 200
