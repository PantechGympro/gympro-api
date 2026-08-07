"""
reportes_entrenador.py — Reporte de desempeño del entrenador en PDF.

El panel de Reportes ya mostraba las cifras en pantalla, pero no había forma de
llevárselas: el entrenador que quiere enseñar su trabajo en una evaluación, o
guardar el cierre del mes, tenía que capturar la pantalla. Este módulo genera el
mismo contenido como documento.

Endpoints:
    GET /api/trainer/reportes/opciones   años con sesiones y catálogo de secciones
    GET /api/trainer/reportes/pdf        documento, según los filtros

Parámetros de /pdf:
    anio       int   año del periodo. Sin él, se toma el año en curso.
    mes        int   1-12. 0 o ausente = año completo.
    secciones  csv   resumen,sesiones,clientes,tipos. Vacío = todas.

El periodo se resuelve del lado del servidor y no se acepta un rango libre
`desde/hasta`: acotarlo a año y mes evita consultas que barran toda la historia
de sesiones desde el móvil.
"""
import io
from datetime import datetime, timedelta

from flask import Blueprint, Response, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.mongo import get_db
from app.models.pg.usuario import Usuario
from app.models.pg.gimnasio import Gimnasio
from app.utils.tenant import require_tenant

reportes_entrenador_bp = Blueprint("reportes_entrenador", __name__, url_prefix="/api/trainer")

SECCIONES_VALIDAS = ["resumen", "sesiones", "clientes", "tipos"]

CATALOGO_SECCIONES = [
    {"id": "resumen",  "label": "Resumen del periodo",
     "descripcion": "Sesiones, clientes activos y calificación promedio."},
    {"id": "sesiones", "label": "Sesiones mes a mes",
     "descripcion": "Completadas y canceladas por mes."},
    {"id": "clientes", "label": "Clientes atendidos",
     "descripcion": "Quiénes entrenaron contigo y cuántas sesiones tuvo cada uno."},
    {"id": "tipos",    "label": "Tipos de sesión",
     "descripcion": "Reparto entre individual, grupal y demás modalidades."},
]

_MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
          "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]


def _resolver_periodo():
    """Devuelve (desde, hasta, etiqueta). `hasta` es exclusivo."""
    hoy  = datetime.now()
    anio = request.args.get("anio", type=int) or hoy.year
    mes  = request.args.get("mes",  type=int) or 0

    if 1 <= mes <= 12:
        desde = datetime(anio, mes, 1)
        hasta = datetime(anio + (mes == 12), (mes % 12) + 1, 1)
        return desde, hasta, f"{_MESES[mes - 1]} {anio}"

    return datetime(anio, 1, 1), datetime(anio + 1, 1, 1), f"Año {anio}"


def _nombre_miembro(mdb, oid) -> str:
    """Nombre del miembro, resolviendo primero contra PostgreSQL."""
    doc = mdb.miembros.find_one({"_id": oid}, {"nombre": 1, "apellido": 1, "id_usuario_pg": 1})
    if not doc:
        return "Sin nombre"

    uid = doc.get("id_usuario_pg")
    if uid:
        try:
            u = Usuario.query.get(int(uid))
            if u and u.nombre:
                return u.nombre
        except Exception:
            pass

    return f"{doc.get('nombre', '')} {doc.get('apellido', '')}".strip() or "Sin nombre"


def _recolectar(trainer_id: int, gym_id: int, desde, hasta) -> dict:
    """Cifras del entrenador en el periodo. Una sola pasada por colección."""
    mdb = get_db()
    rango = {"id_entrenador_pg": trainer_id, "fecha": {"$gte": desde, "$lt": hasta}}

    programadas = mdb.sesiones.count_documents(rango)
    completadas = mdb.sesiones.count_documents({**rango, "estado": "completed"})
    canceladas  = mdb.sesiones.count_documents({**rango, "estado": "cancelled"})

    clientes_activos = mdb.miembros.count_documents({
        "id_entrenador_pg": trainer_id,
        "id_gimnasio_pg":   gym_id,
        "estado":           "Activo",
    })

    # Sesiones completadas por mes dentro del periodo.
    por_mes = list(mdb.sesiones.aggregate([
        {"$match": rango},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m", "date": "$fecha"}},
            "completadas": {"$sum": {"$cond": [{"$eq": ["$estado", "completed"]}, 1, 0]}},
            "canceladas":  {"$sum": {"$cond": [{"$eq": ["$estado", "cancelled"]}, 1, 0]}},
            "total":       {"$sum": 1},
        }},
        {"$sort": {"_id": 1}},
    ]))

    # Reparto por tipo de sesión.
    tipos = list(mdb.sesiones.aggregate([
        {"$match": {**rango, "estado": "completed"}},
        {"$group": {"_id": "$tipo", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]))

    # Clientes atendidos, ordenados por volumen.
    por_cliente = list(mdb.sesiones.aggregate([
        {"$match": {**rango, "estado": "completed", "id_miembro": {"$ne": None}}},
        {"$group": {"_id": "$id_miembro", "sesiones": {"$sum": 1}}},
        {"$sort": {"sesiones": -1}},
        {"$limit": 30},
    ]))
    clientes = [
        {"nombre": _nombre_miembro(mdb, c["_id"]), "sesiones": c["sesiones"]}
        for c in por_cliente
    ]

    # Calificación promedio. No se acota al periodo: una calificación refleja al
    # entrenador, no al mes, y en un mes suelto suele no haber ninguna.
    evals = list(mdb.evaluaciones_entrenador.aggregate([
        {"$match": {"id_entrenador_pg": trainer_id}},
        {"$group": {"_id": None, "avg": {"$avg": "$calificacion"}, "n": {"$sum": 1}}},
    ]))
    calificacion = round(evals[0]["avg"], 1) if evals else 0
    n_evals      = evals[0]["n"] if evals else 0

    return {
        "programadas":  programadas,
        "completadas":  completadas,
        "canceladas":   canceladas,
        "clientes":     clientes_activos,
        "por_mes":      por_mes,
        "tipos":        tipos,
        "lista_clientes": clientes,
        "calificacion": calificacion,
        "n_evaluaciones": n_evals,
        "asistencia":   round(completadas / programadas * 100) if programadas else 0,
        "cancelacion":  round(canceladas / programadas * 100) if programadas else 0,
    }


@reportes_entrenador_bp.route("/reportes/opciones", methods=["GET"])
@jwt_required()
@require_tenant
def opciones():
    """Años con sesiones registradas y catálogo de secciones."""
    mdb        = get_db()
    trainer_id = int(get_jwt_identity())

    anios = sorted({
        f["_id"] for f in mdb.sesiones.aggregate([
            {"$match": {"id_entrenador_pg": trainer_id, "fecha": {"$ne": None}}},
            {"$group": {"_id": {"$year": "$fecha"}}},
        ]) if f.get("_id")
    }, reverse=True)

    if not anios:
        anios = [datetime.now().year]

    return jsonify({"anios": anios, "secciones": CATALOGO_SECCIONES}), 200


@reportes_entrenador_bp.route("/reportes/pdf", methods=["GET"])
@jwt_required()
@require_tenant
def reporte_pdf():
    """Reporte de desempeño del entrenador en PDF."""
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak,
    )
    from reportlab.lib import colors as rl_colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib.enums import TA_CENTER

    trainer_id = int(get_jwt_identity())
    gym_id     = g.tenant_id

    entrenador = Usuario.query.get(trainer_id)
    gym        = Gimnasio.query.get(gym_id)

    desde, hasta, etiqueta = _resolver_periodo()

    pedidas   = [s.strip() for s in (request.args.get("secciones") or "").split(",") if s.strip()]
    secciones = [s for s in pedidas if s in SECCIONES_VALIDAS] or SECCIONES_VALIDAS

    # Desactivadas por omision, igual que en el reporte del gimnasio.
    con_graficas = request.args.get("graficas") == "1"
    if con_graficas:
        from app.utils import graficas_pdf as gpdf

    datos = _recolectar(trainer_id, gym_id, desde, hasta)

    # ── Identidad visual: la misma que el reporte del gimnasio ───────────────
    # Compartida en app/utils/estilo_pdf.py. Antes cada reporte definía sus
    # propios estilos y acababan pareciéndose sin llegar a ser iguales.
    from app.utils import estilo_pdf as ep

    st      = ep.estilos()
    VERDE   = ep.VERDE
    GRIS    = ep.GRIS
    TINTA   = ep.TINTA
    SUAVE   = ep.SUAVE
    st_h2   = st["h2"]
    st_nota = st["nota"]

    def tabla(filas, anchos, derecha=None):
        return ep.tabla(filas, anchos, st, alinear_derecha=derecha)

    nombre_gym  = gym.nombre if gym else "Gimnasio"
    nombre_ent  = entrenador.nombre if entrenador else "Entrenador"

    # PORTADA con el logotipo del gimnasio donde trabaja el entrenador
    story = ep.portada(
        titulo="Reporte de desempeño",
        subtitulo=nombre_ent,
        periodo=etiqueta,
        st=st,
        logo_data_url=getattr(gym, "logo", None),
        pie=nombre_gym,
    )
    story.append(PageBreak())

    if "resumen" in secciones:
        story += ep.seccion("Resumen del periodo", st,
                            "Tu actividad como entrenador en el rango seleccionado.")

        tarjetas = ep.tarjetas_kpi([
            ("Sesiones completadas", str(datos["completadas"]), ep.VERDE),
            ("Tasa de asistencia",   f'{datos["asistencia"]}%', ep.ACENTO_INGRESOS),
            ("Clientes activos",     str(datos["clientes"]),    ep.TINTA),
            ("Cancelación",          f'{datos["cancelacion"]}%',
             ep.ACENTO_ALERTA if datos["cancelacion"] > 20 else ep.GRIS),
        ], st)
        if tarjetas:
            story += [tarjetas, Spacer(1, 0.55 * cm)]

        filas = [
            ["Indicador", "Valor"],
            ["Sesiones completadas", str(datos["completadas"])],
            ["Sesiones programadas", str(datos["programadas"])],
            ["Sesiones canceladas",  str(datos["canceladas"])],
            ["Tasa de asistencia",   f'{datos["asistencia"]}%'],
            ["Tasa de cancelación",  f'{datos["cancelacion"]}%'],
            ["Clientes activos",     str(datos["clientes"])],
        ]
        if datos["n_evaluaciones"]:
            filas.append([
                "Calificación promedio",
                f'{datos["calificacion"]} / 5  ({datos["n_evaluaciones"]} evaluación/es)',
            ])
        story.append(tabla(filas, [9 * cm, 6 * cm], derecha=[1]))

    if "sesiones" in secciones:
        story += ep.seccion("Sesiones mes a mes", st,
                            "Cuántas sesiones cerraste y cuántas se cayeron, mes a mes.")
        if datos["por_mes"]:
            filas = [["Mes", "Completadas", "Canceladas", "Total"]]
            for m in datos["por_mes"]:
                anio_txt, mes_txt = m["_id"].split("-")
                filas.append([
                    f"{_MESES[int(mes_txt) - 1]} {anio_txt}",
                    str(m["completadas"]), str(m["canceladas"]), str(m["total"]),
                ])
            story.append(tabla(filas, [6 * cm, 3.2 * cm, 3.2 * cm, 2.6 * cm], derecha=[1, 2, 3]))

            if con_graficas:
                etiquetas, completadas, canceladas = [], [], []
                for m in datos["por_mes"]:
                    anio_txt, mes_txt = m["_id"].split("-")
                    etiquetas.append(f"{_MESES[int(mes_txt) - 1][:3]} {anio_txt[-2:]}")
                    completadas.append(m["completadas"])
                    canceladas.append(m["canceladas"])
                story += [
                    Spacer(1, 0.5 * cm),
                    # moneda=False: aquí las cifras son sesiones, no pesos.
                    gpdf.barras_comparadas(
                        etiquetas,
                        [("Completadas", completadas, gpdf.COLOR_REAL),
                         ("Canceladas",  canceladas,  gpdf.COLOR_POS)],
                        "Sesiones mes a mes", moneda=False),
                ]
        else:
            story.append(Paragraph("Sin sesiones registradas en el periodo.", st_nota))

    if "clientes" in secciones:
        story += ep.seccion("Clientes atendidos", st,
                            "Quién entrenó contigo, ordenado por número de sesiones.")
        if datos["lista_clientes"]:
            filas = [["Cliente", "Sesiones completadas"]]
            filas += [[c["nombre"], str(c["sesiones"])] for c in datos["lista_clientes"]]
            story.append(tabla(filas, [10 * cm, 5 * cm], derecha=[1]))

            if con_graficas:
                story += [
                    Spacer(1, 0.5 * cm),
                    gpdf.barras_horizontales(
                        [c["nombre"] for c in datos["lista_clientes"]],
                        [c["sesiones"] for c in datos["lista_clientes"]],
                        "Clientes con mas sesiones",
                        color=gpdf.COLOR_ASISTENCIA,
                        moneda=False),
                ]
        else:
            story.append(Paragraph("Ningún cliente completó sesiones en el periodo.", st_nota))

    if "tipos" in secciones:
        story += ep.seccion("Tipos de sesión", st,
                            "Cómo se reparte tu trabajo entre las distintas modalidades.")
        if datos["tipos"]:
            total = sum(t["count"] for t in datos["tipos"]) or 1
            filas = [["Tipo", "Sesiones", "Participación"]]
            for t in datos["tipos"]:
                filas.append([
                    t.get("_id") or "Sin tipo",
                    str(t["count"]),
                    f'{t["count"] / total * 100:.1f}%',
                ])
            story.append(tabla(filas, [7 * cm, 4 * cm, 4 * cm], derecha=[1, 2]))

            if con_graficas:
                story += [
                    Spacer(1, 0.5 * cm),
                    gpdf.pastel([t.get("_id") or "Sin tipo" for t in datos["tipos"]],
                                [t["count"] for t in datos["tipos"]],
                                "Reparto por tipo de sesion",
                                moneda=False, unidad=" ses."),
                ]
        else:
            story.append(Paragraph("Sin sesiones completadas en el periodo.", st_nota))

    story += [
        Spacer(1, 1 * cm),
        Paragraph(
            "Las cifras corresponden a las sesiones registradas en GymPro entre el "
            f"{desde.strftime('%d/%m/%Y')} y el "
            f"{(hasta - timedelta(days=1)).strftime('%d/%m/%Y')}.",
            st_nota,
        ),
    ]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        # Margen superior mayor en las interiores para el encabezado, que se
        # dibuja fuera del flujo del texto.
        topMargin=2.6 * cm, bottomMargin=2.2 * cm,
        title=f"Reporte {nombre_ent} — {etiqueta}",
        author="GymPro",
    )
    doc.build(
        story,
        onFirstPage=ep.marco_portada,
        onLaterPages=ep.marco_pagina(
            titulo=f"{nombre_ent} · {etiqueta}",
            gimnasio=nombre_gym,
            logo_data_url=getattr(gym, "logo", None),
        ),
    )
    buf.seek(0)

    crudo = (entrenador.nombre if entrenador else "entrenador")
    slug  = "".join(c for c in crudo if c.isalnum() or c in " -_").strip()
    slug  = slug.replace(" ", "_")[:40] or "entrenador"
    nombre_archivo = f"Reporte_{slug}_{desde.strftime('%Y%m')}.pdf"

    return Response(
        buf.read(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre_archivo}"'},
    )
