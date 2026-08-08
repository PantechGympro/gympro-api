"""
owner_gym/reportes_negocio.py — Reporte ejecutivo del gimnasio en PDF.

A diferencia de /api/reports/* (que exporta resultados de los modelos de IA),
este reporte es del NEGOCIO: ingresos, membresías, punto de venta, asistencias
y altas de miembros, en el periodo que elija el dueño.

Endpoints:
    GET /api/owner_gym/reportes/opciones   metadatos para armar el formulario
    GET /api/owner_gym/reportes/pdf        genera el documento

Parámetros de /pdf:
    anio, mes         periodo rápido (mes=0 → año completo)
    desde, hasta      rango libre 'YYYY-MM-DD' (tiene prioridad sobre anio/mes)
    secciones         lista separada por comas; por defecto todas
                      resumen,ingresos,membresias,pos,asistencias,miembros
    comparar          '1' para incluir la variación contra el periodo anterior

Nota sobre fechas: las colecciones conviven con fechas guardadas como datetime
y como cadena, herencia de versiones anteriores. Por eso todas las agregaciones
normalizan con $toDate antes de comparar.
"""
import io
from datetime import datetime, timedelta

from flask import Blueprint, Response, request, jsonify, g
from flask_jwt_extended import jwt_required

from app.mongo import get_db
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.tipo_membresia import TipoMembresia
from app.utils.tenant import require_tenant
from app.utils.security import require_role

reportes_negocio_bp = Blueprint("reportes_negocio", __name__)

MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
            "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

SECCIONES_VALIDAS = ["resumen", "ingresos", "membresias", "pos", "asistencias", "miembros"]


# ═══════════════════════════════════════════════════════════════════════════
# Periodo
# ═══════════════════════════════════════════════════════════════════════════

def _resolver_periodo():
    """
    Devuelve (desde, hasta, etiqueta). El rango libre gana sobre anio/mes; si no
    llega nada se usa el mes en curso, que es lo que casi siempre se quiere.
    """
    desde_txt = (request.args.get("desde") or "").strip()
    hasta_txt = (request.args.get("hasta") or "").strip()

    if desde_txt and hasta_txt:
        try:
            desde = datetime.strptime(desde_txt[:10], "%Y-%m-%d")
            hasta = datetime.strptime(hasta_txt[:10], "%Y-%m-%d") + timedelta(days=1)
            etiqueta = f"{desde.strftime('%d/%m/%Y')} al {(hasta - timedelta(days=1)).strftime('%d/%m/%Y')}"
            return desde, hasta, etiqueta
        except ValueError:
            pass  # formato inválido: se cae al periodo por defecto

    anio = request.args.get("anio", type=int)
    mes  = request.args.get("mes",  type=int)
    hoy  = datetime.now()

    if anio and mes and 1 <= mes <= 12:
        desde = datetime(anio, mes, 1)
        hasta = datetime(anio + (mes == 12), (mes % 12) + 1, 1)
        return desde, hasta, f"{MESES_ES[mes - 1].capitalize()} {anio}"

    if anio:
        return datetime(anio, 1, 1), datetime(anio + 1, 1, 1), f"Año {anio}"

    desde = datetime(hoy.year, hoy.month, 1)
    hasta = datetime(hoy.year + (hoy.month == 12), (hoy.month % 12) + 1, 1)
    return desde, hasta, f"{MESES_ES[hoy.month - 1].capitalize()} {hoy.year}"


def _periodo_anterior(desde: datetime, hasta: datetime):
    """Rango inmediatamente anterior, de la misma duración."""
    dias = (hasta - desde).days or 30
    return desde - timedelta(days=dias), desde


# ═══════════════════════════════════════════════════════════════════════════
# Consultas
# ═══════════════════════════════════════════════════════════════════════════

def _suma(coleccion, gym_id, campo_fecha, campo_monto, desde, hasta):
    """Total e importe de una colección en un rango."""
    pipeline = [
        {"$match": {"id_gimnasio": gym_id}},
        {"$addFields": {"_f": {"$toDate": f"${campo_fecha}"}}},
        {"$match": {"_f": {"$gte": desde, "$lt": hasta}}},
        {"$group": {"_id": None,
                    "total": {"$sum": f"${campo_monto}"},
                    "n":     {"$sum": 1}}},
    ]
    r = list(coleccion.aggregate(pipeline))
    if not r:
        return 0.0, 0
    return float(r[0].get("total") or 0), int(r[0].get("n") or 0)


def _recolectar(gym_id: int, desde: datetime, hasta: datetime) -> dict:
    """Todas las cifras del periodo en una sola pasada por colección."""
    mdb = get_db()

    ingresos_mem, n_mem = _suma(mdb.pagos,  gym_id, "fecha_pago", "monto", desde, hasta)
    ingresos_pos, n_pos = _suma(mdb.ventas, gym_id, "fecha",      "total", desde, hasta)

    # ── Ingresos por método de pago ──────────────────────────────────────────
    metodos: dict = {}
    for coleccion, campo_f, campo_m in ((mdb.pagos, "fecha_pago", "monto"),
                                        (mdb.ventas, "fecha", "total")):
        for d in coleccion.aggregate([
            {"$match": {"id_gimnasio": gym_id}},
            {"$addFields": {"_f": {"$toDate": f"${campo_f}"}}},
            {"$match": {"_f": {"$gte": desde, "$lt": hasta}}},
            {"$group": {"_id": {"$ifNull": ["$metodo_pago", "Sin especificar"]},
                        "total": {"$sum": f"${campo_m}"},
                        "n": {"$sum": 1}}},
        ]):
            clave = d["_id"] or "Sin especificar"
            acum = metodos.setdefault(clave, {"total": 0.0, "n": 0})
            acum["total"] += float(d.get("total") or 0)
            acum["n"]     += int(d.get("n") or 0)

    # ── Membresías vendidas, por tipo ────────────────────────────────────────
    por_membresia: dict = {}
    for d in mdb.pagos.aggregate([
        {"$match": {"id_gimnasio": gym_id}},
        {"$addFields": {"_f": {"$toDate": "$fecha_pago"}}},
        {"$match": {"_f": {"$gte": desde, "$lt": hasta}}},
        {"$group": {"_id": {"$ifNull": ["$concepto", "Sin concepto"]},
                    "total": {"$sum": "$monto"}, "n": {"$sum": 1}}},
        {"$sort": {"total": -1}},
        {"$limit": 15},
    ]):
        por_membresia[d["_id"]] = {"total": float(d.get("total") or 0),
                                   "n": int(d.get("n") or 0)}

    # ── Productos más vendidos ───────────────────────────────────────────────
    productos: list = []
    for d in mdb.ventas.aggregate([
        {"$match": {"id_gimnasio": gym_id}},
        {"$addFields": {"_f": {"$toDate": "$fecha"}}},
        {"$match": {"_f": {"$gte": desde, "$lt": hasta}}},
        {"$unwind": "$items"},
        {"$group": {
            "_id": "$items.nombre",
            "unidades": {"$sum": {"$ifNull": ["$items.qty", {"$ifNull": ["$items.cantidad", 1]}]}},
            "importe":  {"$sum": {"$multiply": [
                {"$ifNull": ["$items.precio", 0]},
                {"$ifNull": ["$items.qty", {"$ifNull": ["$items.cantidad", 1]}]},
            ]}},
        }},
        {"$sort": {"importe": -1}},
        {"$limit": 15},
    ]):
        productos.append({"nombre": d["_id"] or "—",
                          "unidades": int(d.get("unidades") or 0),
                          "importe": float(d.get("importe") or 0)})

    # ── Asistencias ──────────────────────────────────────────────────────────
    asistencias = mdb.asistencias.count_documents({
        "id_gimnasio": gym_id,
        "$expr": {"$and": [
            {"$gte": [{"$toDate": "$fecha"}, desde]},
            {"$lt":  [{"$toDate": "$fecha"}, hasta]},
        ]},
    })

    # ── Miembros ─────────────────────────────────────────────────────────────
    altas = mdb.miembros.count_documents({
        "id_gimnasio_pg": gym_id,
        "$expr": {"$and": [
            {"$gte": [{"$toDate": "$fecha_registro"}, desde]},
            {"$lt":  [{"$toDate": "$fecha_registro"}, hasta]},
        ]},
    })
    activos   = mdb.miembros.count_documents({"id_gimnasio_pg": gym_id, "estado": "Activo"})
    inactivos = mdb.miembros.count_documents({"id_gimnasio_pg": gym_id, "estado": "Inactivo"})

    return {
        "ingresos_membresias": ingresos_mem,
        "ingresos_pos":        ingresos_pos,
        "ingresos_total":      ingresos_mem + ingresos_pos,
        "n_pagos":             n_mem,
        "n_ventas":            n_pos,
        "metodos":             metodos,
        "por_membresia":       por_membresia,
        "productos":           productos,
        "asistencias":         asistencias,
        "altas":               altas,
        "activos":             activos,
        "inactivos":           inactivos,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════════════════

@reportes_negocio_bp.route("/reportes/opciones", methods=["GET"])
@jwt_required()
@require_role("owner_gym")
@require_tenant
def opciones_reporte():
    """Años con datos y catálogo de secciones, para armar el formulario."""
    mdb = get_db()
    gym_id = g.tenant_id

    anios = set()
    for coleccion, campo in ((mdb.pagos, "fecha_pago"), (mdb.ventas, "fecha")):
        for d in coleccion.aggregate([
            {"$match": {"id_gimnasio": gym_id}},
            {"$group": {"_id": {"$year": {"$toDate": f"${campo}"}}}},
        ]):
            if d.get("_id"):
                anios.add(d["_id"])

    if not anios:
        anios.add(datetime.now().year)

    return jsonify({
        "anios": sorted(anios, reverse=True),
        "secciones": [
            {"id": "resumen",     "label": "Resumen ejecutivo",   "descripcion": "Totales del periodo"},
            {"id": "ingresos",    "label": "Ingresos",            "descripcion": "Desglose por origen y método de pago"},
            {"id": "membresias",  "label": "Membresías",          "descripcion": "Qué se vendió y cuánto dejó"},
            {"id": "pos",         "label": "Punto de venta",      "descripcion": "Productos más vendidos"},
            {"id": "asistencias", "label": "Asistencias",         "descripcion": "Visitas registradas"},
            {"id": "miembros",    "label": "Miembros",            "descripcion": "Altas y estado del padrón"},
        ],
    }), 200


@reportes_negocio_bp.route("/reportes/pdf", methods=["GET"])
@jwt_required()
@require_role("owner_gym")
@require_tenant
def reporte_pdf():
    """Reporte ejecutivo del gimnasio en PDF."""
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak,
    )
    from reportlab.lib import colors as rl_colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib.enums import TA_CENTER

    gym_id = g.tenant_id
    gym    = Gimnasio.query.get(gym_id)
    if not gym:
        return jsonify({"msg": "Gimnasio no encontrado"}), 404

    desde, hasta, etiqueta = _resolver_periodo()
    comparar = request.args.get("comparar") == "1"

    pedidas = [s.strip() for s in (request.args.get("secciones") or "").split(",") if s.strip()]
    secciones = [s for s in pedidas if s in SECCIONES_VALIDAS] or SECCIONES_VALIDAS

    # Las graficas van desactivadas por omision: un reporte que se imprime para
    # archivar suele querer solo las cifras, y dibujarlas cuesta tiempo de CPU.
    con_graficas = request.args.get("graficas") == "1"
    if con_graficas:
        from app.utils import graficas_pdf as gpdf

    datos = _recolectar(gym_id, desde, hasta)
    previo = None
    if comparar:
        p_desde, p_hasta = _periodo_anterior(desde, hasta)
        previo = _recolectar(gym_id, p_desde, p_hasta)

    # ── Identidad visual compartida ──────────────────────────────────────────
    # Vive en app/utils/estilo_pdf.py para que este reporte y el del entrenador
    # sean el mismo documento con distinto contenido, y no dos maquetas que hay
    # que retocar por separado.
    from app.utils import estilo_pdf as ep

    st     = ep.estilos()
    VERDE  = ep.VERDE
    GRIS   = ep.GRIS
    TINTA  = ep.TINTA
    SUAVE  = ep.SUAVE

    st_h2    = st["h2"]
    st_texto = st["texto"]
    st_nota  = st["nota"]

    dinero = lambda v: f"${v:,.2f}"

    def tabla(filas, anchos, encabezado=True, derecha=None):
        """Tabla del reporte. `derecha` son las columnas de cifras."""
        return ep.tabla(filas, anchos, st, alinear_derecha=derecha)

    def variacion(actual: float, anterior: float) -> str:
        """Texto de comparación; sin base previa no se inventa un porcentaje."""
        if not previo:
            return ""
        if anterior <= 0:
            return "sin base de comparación"
        pct = ((actual - anterior) / anterior) * 100
        signo = "+" if pct >= 0 else ""
        return f"{signo}{pct:.1f}% vs periodo anterior"

    # ── Documento ────────────────────────────────────────────────────────────
    story = []

    # PORTADA con el logotipo del gimnasio
    contacto = " · ".join(filter(None, [gym.email_contacto, gym.telefono])) or ""
    try:
        from app.utils.gym_types import GYM_TYPES
        tipo_txt = GYM_TYPES.get(gym.tipo_gimnasio or "", {}).get("label", "")
    except Exception:
        tipo_txt = ""
    plan_txt = gym.plan.value if hasattr(gym.plan, "value") else str(gym.plan or "")

    story += ep.portada(
        titulo=gym.nombre or "Gimnasio",
        subtitulo="Reporte ejecutivo",
        periodo=etiqueta,
        st=st,
        logo_data_url=gym.logo,
        pie=" · ".join(filter(None, [contacto, tipo_txt,
                                     f"Plan {plan_txt}" if plan_txt else ""])),
    )
    story.append(PageBreak())

    # RESUMEN
    if "resumen" in secciones:
        story += ep.seccion("Resumen del periodo", st,
                            "Las cifras clave del gimnasio en el rango seleccionado.")

        # Los indicadores principales van en tarjetas: son lo primero que se
        # busca y en una tabla de dos columnas se perdían entre el resto.
        tarjetas = ep.tarjetas_kpi([
            ("Ingresos totales", dinero(datos["ingresos_total"]), ep.VERDE),
            ("Asistencias",      str(datos["asistencias"]),       ep.ACENTO_INGRESOS),
            ("Altas",            str(datos["altas"]),             ep.ACENTO_POS),
            ("Miembros activos", str(datos["activos"]),           ep.TINTA),
        ], st)
        if tarjetas:
            story += [tarjetas, Spacer(1, 0.55 * cm)]

        filas = [["Concepto", "Valor", "Comparativa"]]
        filas.append(["Ingresos totales", dinero(datos["ingresos_total"]),
                      variacion(datos["ingresos_total"], previo["ingresos_total"]) if previo else "—"])
        filas.append(["Membresías", dinero(datos["ingresos_membresias"]), f'{datos["n_pagos"]} pagos'])
        filas.append(["Punto de venta", dinero(datos["ingresos_pos"]), f'{datos["n_ventas"]} ventas'])
        filas.append(["Asistencias", str(datos["asistencias"]),
                      variacion(datos["asistencias"], previo["asistencias"]) if previo else "—"])
        filas.append(["Altas de miembros", str(datos["altas"]),
                      variacion(datos["altas"], previo["altas"]) if previo else "—"])
        filas.append(["Miembros activos", str(datos["activos"]), f'{datos["inactivos"]} inactivos'])
        story += [tabla(filas, [7 * cm, 4.5 * cm, 5.5 * cm], derecha=[1]), Spacer(1, 0.4 * cm)]
        if not previo:
            story.append(Paragraph(
                "Para ver la comparativa contra el periodo anterior, genera el reporte "
                "con la opción de comparación activada.", st_nota))

    # INGRESOS
    if "ingresos" in secciones:
        story += ep.seccion("Ingresos por método de pago", st,
                            "Con qué pagan tus miembros y cuánto aporta cada vía.")
        if datos["metodos"]:
            filas = [["Método", "Movimientos", "Importe", "% del total"]]
            total = datos["ingresos_total"] or 1
            for metodo, v in sorted(datos["metodos"].items(), key=lambda x: -x[1]["total"]):
                filas.append([metodo, str(v["n"]), dinero(v["total"]),
                              f'{(v["total"] / total) * 100:.1f}%'])
            story.append(tabla(filas, [6 * cm, 3.5 * cm, 4 * cm, 3.5 * cm], derecha=[1, 2, 3]))

            if con_graficas:
                ordenados = sorted(datos["metodos"].items(), key=lambda x: -x[1]["total"])
                story += [
                    Spacer(1, 0.5 * cm),
                    gpdf.pastel([m for m, _ in ordenados],
                                [v["total"] for _, v in ordenados],
                                f"Reparto por metodo de pago — {etiqueta}",
                                moneda=True),
                    Spacer(1, 0.4 * cm),
                    gpdf.barras_comparadas(
                        ["Membresias", "Punto de venta"],
                        [("Importe cobrado",
                          [datos["ingresos_membresias"], datos["ingresos_pos"]],
                          gpdf.COLOR_INGRESOS)],
                        f"Origen de los ingresos — {etiqueta}", moneda=True,
                        eje_x="Origen del ingreso", eje_y="Importe cobrado (MXN)"),
                ]
        else:
            story.append(Paragraph("Sin ingresos registrados en el periodo.", st_texto))

    # MEMBRESÍAS
    if "membresias" in secciones:
        story += ep.seccion("Membresías cobradas", st,
                            "Qué planes se vendieron y cuánto dejó cada uno.")
        if datos["por_membresia"]:
            filas = [["Concepto", "Cobros", "Importe"]]
            for concepto, v in sorted(datos["por_membresia"].items(), key=lambda x: -x[1]["total"]):
                filas.append([concepto, str(v["n"]), dinero(v["total"])])
            story.append(tabla(filas, [9 * cm, 3.5 * cm, 4.5 * cm], derecha=[1, 2]))

            if con_graficas:
                ordenadas = sorted(datos["por_membresia"].items(), key=lambda x: -x[1]["total"])[:8]
                story += [
                    Spacer(1, 0.5 * cm),
                    gpdf.barras_horizontales([c for c, _ in ordenadas],
                                             [v["total"] for _, v in ordenadas],
                                             f"Importe por tipo de membresia — {etiqueta}",
                                             color=gpdf.COLOR_MEMBRESIAS,
                                             moneda=True,
                                             eje_y="Tipo de membresia",
                                             eje_x="Importe cobrado (MXN)"),
                ]
        else:
            story.append(Paragraph("No se cobraron membresías en el periodo.", st_texto))

        activas = TipoMembresia.query.filter_by(id_gimnasio=gym_id, activo=True).count()
        story += [Spacer(1, 0.3 * cm),
                  Paragraph(f"El catálogo tiene {activas} tipos de membresía activos.", st_nota)]

    # PUNTO DE VENTA
    if "pos" in secciones:
        story += ep.seccion("Productos más vendidos", st,
                            "Qué se mueve en el mostrador, ordenado por importe.")
        if datos["productos"]:
            filas = [["Producto", "Unidades", "Importe"]]
            for p in datos["productos"]:
                filas.append([p["nombre"], str(p["unidades"]), dinero(p["importe"])])
            story.append(tabla(filas, [9 * cm, 3.5 * cm, 4.5 * cm], derecha=[1, 2]))

            if con_graficas:
                story += [
                    Spacer(1, 0.5 * cm),
                    gpdf.barras_horizontales([p["nombre"] for p in datos["productos"]],
                                             [p["importe"] for p in datos["productos"]],
                                             f"Productos por importe vendido — {etiqueta}",
                                             color=gpdf.COLOR_POS,
                                             moneda=True,
                                             eje_y="Producto",
                                             eje_x="Importe vendido (MXN)"),
                ]
        else:
            story.append(Paragraph("No hubo ventas en el periodo.", st_texto))

    # ASISTENCIAS
    if "asistencias" in secciones:
        story += ep.seccion("Asistencias", st,
                            "Cuánta gente pisa el gimnasio y con qué frecuencia.")
        dias = max(1, (hasta - desde).days)
        promedio = datos["asistencias"] / dias
        filas = [["Indicador", "Valor"],
                 ["Visitas registradas", str(datos["asistencias"])],
                 ["Promedio diario", f"{promedio:.1f}"],
                 ["Días del periodo", str(dias)]]
        if datos["activos"]:
            filas.append(["Visitas por miembro activo",
                          f'{datos["asistencias"] / datos["activos"]:.1f}'])
        story.append(tabla(filas, [9 * cm, 5 * cm], derecha=[1]))

    # MIEMBROS
    if "miembros" in secciones:
        story += ep.seccion("Padrón de miembros", st,
                            "Cómo está compuesta tu base de miembros.")
        total_padron = datos["activos"] + datos["inactivos"]
        filas = [["Indicador", "Valor"],
                 ["Altas en el periodo", str(datos["altas"])],
                 ["Activos", str(datos["activos"])],
                 ["Inactivos", str(datos["inactivos"])],
                 ["Total en el padrón", str(total_padron)]]
        if total_padron:
            filas.append(["Proporción de activos",
                          f'{(datos["activos"] / total_padron) * 100:.1f}%'])
        story.append(tabla(filas, [9 * cm, 5 * cm], derecha=[1]))

    story += [Spacer(1, 1 * cm),
              Paragraph(
                  "Las cifras corresponden a los movimientos registrados en GymPro "
                  f"entre el {desde.strftime('%d/%m/%Y')} y el "
                  f"{(hasta - timedelta(days=1)).strftime('%d/%m/%Y')}.", st_nota)]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        # Margen superior mayor en las páginas interiores para dejar sitio al
        # encabezado con el logotipo, que se dibuja fuera del flujo del texto.
        topMargin=2.6 * cm, bottomMargin=2.2 * cm,
        title=f"Reporte {gym.nombre} — {etiqueta}",
        author="GymPro",
    )
    doc.build(
        story,
        onFirstPage=ep.marco_portada,
        onLaterPages=ep.marco_pagina(
            titulo=f"Reporte ejecutivo · {etiqueta}",
            gimnasio=gym.nombre or "Gimnasio",
            logo_data_url=gym.logo,
        ),
    )
    buf.seek(0)

    slug = "".join(c for c in (gym.nombre or "gimnasio") if c.isalnum() or c in " -_").strip()
    slug = slug.replace(" ", "_")[:40] or "gimnasio"
    nombre_archivo = f"Reporte_{slug}_{desde.strftime('%Y%m')}.pdf"

    return Response(
        buf.read(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre_archivo}"'},
    )
