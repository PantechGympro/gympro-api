"""
routes/reports.py -- Exportacion de reportes en CSV y PDF.

Sprint 4 / US18: genera archivos descargables a partir de resultados de Spark.
Los datos se obtienen del cache de analytics (mismo TTL) o se re-ejecutan.

Endpoints:
  GET /api/reports/<tipo>/csv   -- tipos: mapreduce, kmeans, miembros, cancelaciones
  GET /api/reports/<tipo>/pdf

Requiere: reportlab (pip install reportlab)
"""
import io
import csv
import os
from datetime import datetime
from flask import Blueprint, Response, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt
from app.utils.tenant import require_tenant

reports_bp = Blueprint("reports", __name__, url_prefix="/api/reports")


# ─── Helpers ReportLab ────────────────────────────────────────────────────────

def _pdf_response(filename: str, build_fn) -> Response:
    """Genera un PDF en memoria usando ReportLab y lo devuelve como descarga."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "GymProTitle",
        parent=styles["Heading1"],
        fontSize=16, spaceAfter=8,
    )
    sub_style = ParagraphStyle(
        "GymProSub",
        parent=styles["Normal"],
        fontSize=9, textColor=colors.grey, spaceAfter=16,
    )

    story = build_fn(styles, title_style, sub_style, colors, Table, TableStyle,
                     Paragraph, Spacer, cm)
    doc.build(story)
    buf.seek(0)
    return Response(
        buf.read(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _csv_response(filename: str, headers_row: list, rows: list) -> Response:
    buf = io.StringIO()
    w   = csv.writer(buf)
    w.writerow(headers_row)
    w.writerows(rows)
    return Response(
        buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _hoy() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M")


# ─── Obtener datos de analytics (cache o re-ejecucion) ───────────────────────

def _get_mapreduce_data(gym_id):
    from app.routes.ia.spark_config import cache_get, cache_set, get_spark
    from app.routes.spark_mapreduce import _cache_key, _ejecutar_y_construir_payload

    key    = _cache_key(gym_id)
    cached = cache_get(key)
    if cached:
        return cached
    spark   = get_spark()
    payload = _ejecutar_y_construir_payload(spark, gym_id)
    cache_set(key, payload)
    return payload


def _get_kmeans_data(gym_id, k=3):
    from app.routes.ia.spark_config import cache_get, cache_set, get_spark
    from app.routes.spark_kmeans import _cache_key, _ejecutar_kmeans, _build_payload

    key    = _cache_key(gym_id, k)
    cached = cache_get(key)
    if cached:
        return cached
    spark  = get_spark()
    resumen, asig, centroides, sil = _ejecutar_kmeans(spark, k=k, gym_id=gym_id)
    payload = _build_payload(k, 20, resumen, asig, centroides, sil)
    cache_set(key, payload)
    return payload


def _get_miembros_data(gym_id):
    from app.mongo import get_db
    db = get_db()
    filtro = {} if gym_id is None else {"id_gimnasio_pg": int(gym_id)}
    return list(db.miembros.find(filtro, {
        "nombre":1,"email":1,"sexo":1,"estado":1,"objetivo":1,
        "peso_inicial":1,"estatura":1,"fecha_registro":1,
    }).limit(5000))


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS CSV
# ─────────────────────────────────────────────────────────────────────────────

@reports_bp.route("/mapreduce/csv", methods=["GET"])
@jwt_required()
@require_tenant
def report_mapreduce_csv():
    gym_id = get_jwt().get("id_gimnasio")
    data   = _get_mapreduce_data(gym_id)

    rows = []
    for row in data.get("ingresos_por_periodo", []):
        rows.append([row.get("periodo",""), row.get("metodo_pago",""),
                     row.get("total_ingresos",0), row.get("num_pagos",0)])

    return _csv_response(
        f"mapreduce_ingresos_{_hoy()}.csv",
        ["Periodo", "Metodo de Pago", "Total Ingresos (MXN)", "Num Pagos"],
        rows,
    )


@reports_bp.route("/kmeans/csv", methods=["GET"])
@jwt_required()
@require_tenant
def report_kmeans_csv():
    gym_id = get_jwt().get("id_gimnasio")
    k      = request.args.get("k", 3, type=int)
    data   = _get_kmeans_data(gym_id, k)

    rows = [[
        a.get("id_miembro",""), a.get("nombre",""),
        a.get("cluster_id",""), a.get("etiqueta",""),
        a.get("peso",""), a.get("imc",""), a.get("grasa_corporal",""),
    ] for a in data.get("asignaciones", [])]

    return _csv_response(
        f"kmeans_k{k}_{_hoy()}.csv",
        ["ID Miembro","Nombre","Cluster","Etiqueta","Peso","IMC","Grasa Corporal %"],
        rows,
    )


@reports_bp.route("/miembros/csv", methods=["GET"])
@jwt_required()
@require_tenant
def report_miembros_csv():
    gym_id   = get_jwt().get("id_gimnasio")
    miembros = _get_miembros_data(gym_id)

    rows = [[
        str(m.get("_id","")), m.get("nombre",""), m.get("email",""),
        m.get("sexo",""), m.get("estado",""), m.get("objetivo",""),
        m.get("peso_inicial",""), m.get("estatura",""),
        str(m.get("fecha_registro",""))[:10],
    ] for m in miembros]

    return _csv_response(
        f"miembros_{_hoy()}.csv",
        ["ID","Nombre","Email","Sexo","Estado","Objetivo","Peso Inicial","Estatura","Fecha Registro"],
        rows,
    )


@reports_bp.route("/cancelaciones/csv", methods=["GET"])
@jwt_required()
@require_tenant
def report_cancelaciones_csv():
    from app.routes.ia.spark_config import cache_get
    gym_id = get_jwt().get("id_gimnasio")
    key    = f"cancelaciones_gym{gym_id}"
    data   = cache_get(key)
    if not data:
        return jsonify({"error": "Sin datos de cancelaciones. Ejecute el modelo primero."}), 404

    rows = [[
        p.get("nombre",""), p.get("dias_sin_asistir",""),
        f"{(p.get('probabilidad',0)*100):.1f}%", p.get("riesgo",""),
    ] for p in data.get("predicciones", [])]

    return _csv_response(
        f"riesgo_cancelacion_{_hoy()}.csv",
        ["Miembro","Dias Sin Asistir","Probabilidad Cancelacion","Nivel Riesgo"],
        rows,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS PDF
# ─────────────────────────────────────────────────────────────────────────────

def _table_style(colors):
    from reportlab.platypus import TableStyle
    return TableStyle([
        ("BACKGROUND",  (0,0), (-1,0),  colors.HexColor("#1a1a2e")),
        ("TEXTCOLOR",   (0,0), (-1,0),  colors.HexColor("#fbe379")),
        ("FONTNAME",    (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,0),  9),
        ("FONTNAME",    (0,1), (-1,-1), "Helvetica"),
        ("FONTSIZE",    (0,1), (-1,-1), 8),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.HexColor("#16213e"), colors.HexColor("#0f3460")]),
        ("TEXTCOLOR",   (0,1), (-1,-1), colors.white),
        ("GRID",        (0,0), (-1,-1), 0.3, colors.HexColor("#333355")),
        ("ALIGN",       (0,0), (-1,-1), "LEFT"),
        ("PADDING",     (0,0), (-1,-1), 5),
        ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
    ])


@reports_bp.route("/mapreduce/pdf", methods=["GET"])
@jwt_required()
@require_tenant
def report_mapreduce_pdf():
    gym_id = get_jwt().get("id_gimnasio")
    data   = _get_mapreduce_data(gym_id)

    def build(styles, title_s, sub_s, colors, Table, TableStyle, Paragraph, Spacer, cm):
        story = [
            Paragraph("GymPro — Reporte MapReduce: Ingresos y Asistencia", title_s),
            Paragraph(f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')} | Gimnasio ID: {gym_id}", sub_s),
        ]

        ingresos = data.get("ingresos_por_periodo", [])
        if ingresos:
            story.append(Paragraph("Ingresos por Periodo y Metodo de Pago", styles["Heading2"]))
            story.append(Spacer(1, 0.2*cm))
            tdata = [["Periodo","Metodo","Total MXN","Num Pagos"]] + [
                [r.get("periodo",""), r.get("metodo_pago",""),
                 f"${r.get('total_ingresos',0):,.2f}", r.get("num_pagos",0)]
                for r in sorted(ingresos, key=lambda x: x.get("periodo",""), reverse=True)[:40]
            ]
            t = Table(tdata, colWidths=[4*cm, 5*cm, 5*cm, 3.5*cm])
            t.setStyle(_table_style(colors))
            story.extend([t, Spacer(1, 0.5*cm)])

        asistencia = data.get("asistencia_por_dia_semana", [])
        if asistencia:
            story.append(Paragraph("Asistencia por Dia de la Semana", styles["Heading2"]))
            story.append(Spacer(1, 0.2*cm))
            tdata = [["Dia","Total Visitas"]] + [
                [r.get("dia_semana",""), r.get("total_visitas","")]
                for r in asistencia
            ]
            t = Table(tdata, colWidths=[8*cm, 8*cm])
            t.setStyle(_table_style(colors))
            story.append(t)

        return story

    return _pdf_response(f"mapreduce_{_hoy()}.pdf", build)


@reports_bp.route("/kmeans/pdf", methods=["GET"])
@jwt_required()
@require_tenant
def report_kmeans_pdf():
    gym_id = get_jwt().get("id_gimnasio")
    k      = request.args.get("k", 3, type=int)
    data   = _get_kmeans_data(gym_id, k)

    def build(styles, title_s, sub_s, colors, Table, TableStyle, Paragraph, Spacer, cm):
        resumen = data.get("resumen_clusters", [])
        story   = [
            Paragraph(f"GymPro — Reporte K-Means Clustering (k={k})", title_s),
            Paragraph(
                f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')} | "
                f"Silhouette: {data.get('silhouette',0):.3f}", sub_s,
            ),
            Paragraph("Resumen de Clusters", styles["Heading2"]),
            Spacer(1, 0.2*cm),
        ]

        tdata = [["Cluster","Etiqueta","Miembros","IMC Prom","Peso Prom","Grasa Prom"]] + [
            [c.get("cluster_id",""), c.get("etiqueta",""), c.get("num_miembros",""),
             f"{c.get('imc_promedio',0):.1f}", f"{c.get('peso_promedio',0):.1f} kg",
             f"{c.get('grasa_promedio',0):.1f}%"]
            for c in resumen
        ]
        t = Table(tdata, colWidths=[2*cm, 7*cm, 3*cm, 3*cm, 3.5*cm, 3.5*cm])
        t.setStyle(_table_style(colors))
        story.extend([t, Spacer(1, 0.5*cm)])

        asig = data.get("asignaciones", [])[:30]
        if asig:
            story.append(Paragraph("Muestra de Asignaciones (primeros 30)", styles["Heading2"]))
            story.append(Spacer(1, 0.2*cm))
            tdata2 = [["Nombre","Cluster","Etiqueta","Peso","IMC"]] + [
                [a.get("nombre",""), a.get("cluster_id",""), a.get("etiqueta",""),
                 f"{a.get('peso',0):.1f} kg", f"{a.get('imc',0):.1f}"]
                for a in asig
            ]
            t2 = Table(tdata2, colWidths=[7*cm, 2.5*cm, 7*cm, 3*cm, 2.5*cm])
            t2.setStyle(_table_style(colors))
            story.append(t2)

        return story

    return _pdf_response(f"kmeans_k{k}_{_hoy()}.pdf", build)


@reports_bp.route("/miembros/pdf", methods=["GET"])
@jwt_required()
@require_tenant
def report_miembros_pdf():
    gym_id   = get_jwt().get("id_gimnasio")
    miembros = _get_miembros_data(gym_id)

    def build(styles, title_s, sub_s, colors, Table, TableStyle, Paragraph, Spacer, cm):
        story = [
            Paragraph("GymPro — Reporte de Miembros", title_s),
            Paragraph(
                f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')} | "
                f"Total: {len(miembros)} miembros | Gimnasio ID: {gym_id}", sub_s,
            ),
            Spacer(1, 0.2*cm),
        ]

        tdata = [["Nombre","Email","Sexo","Estado","Objetivo","Peso Ini.","Fecha Reg."]] + [
            [m.get("nombre","")[:28], m.get("email","")[:28], m.get("sexo",""),
             m.get("estado",""), m.get("objetivo","")[:20],
             f"{m.get('peso_inicial',0):.1f} kg",
             str(m.get("fecha_registro",""))[:10]]
            for m in miembros[:60]
        ]
        t = Table(tdata, colWidths=[5.5*cm, 5.5*cm, 2*cm, 2.5*cm, 4*cm, 2.5*cm, 2.5*cm])
        t.setStyle(_table_style(colors))
        story.append(t)
        if len(miembros) > 60:
            story.append(Spacer(1, 0.3*cm))
            story.append(Paragraph(f"* Mostrando 60 de {len(miembros)} miembros. Use CSV para exportacion completa.", sub_s))
        return story

    return _pdf_response(f"miembros_{_hoy()}.pdf", build)
