"""
utils/estilo_pdf.py — Identidad visual compartida de los reportes en PDF.

Los dos reportes del sistema —el del gimnasio y el del entrenador— repetían su
propia paleta, sus propios estilos de párrafo y su propia tabla. El resultado
eran dos documentos parecidos pero no iguales, y cualquier retoque había que
hacerlo dos veces. Aquí vive lo común: colores, tipografías, portada, encabezado
y pie de página, tarjetas de indicadores y tablas.

Todo se apoya en ReportLab, que ya se usaba. No hay dependencias nuevas.

Piezas principales:
    Paleta            colores con significado fijo
    estilos()         hoja de estilos de párrafo
    portada()         primera página con el logotipo del gimnasio
    marco_pagina()    encabezado y pie que se repiten en cada hoja
    tarjetas_kpi()    fila de indicadores destacados
    tabla()           tabla con cebra y cabecera de color
    seccion()         título de sección con línea de acento
"""
from datetime import datetime

from reportlab.lib import colors as rl_colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    Table, TableStyle, Paragraph, Spacer, Image, Flowable,
)

# ── Paleta ───────────────────────────────────────────────────────────────────
# Los mismos tonos que la interfaz web (components/compartido/InfoGrafico.jsx),
# para que la pantalla y el documento impreso no parezcan de sistemas distintos.
VERDE     = rl_colors.HexColor("#00875A")   # acento de marca
VERDE_OSC = rl_colors.HexColor("#00694A")
TINTA     = rl_colors.HexColor("#0F1720")   # texto principal
GRIS      = rl_colors.HexColor("#5A6673")   # texto secundario
SUAVE     = rl_colors.HexColor("#EAEFF3")   # fondos y separadores
CEBRA     = rl_colors.HexColor("#F7F9FA")   # filas alternas
BLANCO    = rl_colors.white

ACENTO_INGRESOS = rl_colors.HexColor("#6366F1")
ACENTO_POS      = rl_colors.HexColor("#F59E0B")
ACENTO_ALERTA   = rl_colors.HexColor("#EF4444")

ANCHO_UTIL = 17 * cm    # A4 menos los márgenes de 2 cm


def estilos() -> dict:
    """Hoja de estilos de párrafo del reporte."""
    base = getSampleStyleSheet()
    return {
        "portada_titulo": ParagraphStyle(
            "PortadaTitulo", parent=base["Heading1"], fontSize=30, leading=36,
            textColor=TINTA, alignment=TA_CENTER, spaceAfter=4),
        "portada_sub": ParagraphStyle(
            "PortadaSub", parent=base["Normal"], fontSize=13, leading=19,
            textColor=GRIS, alignment=TA_CENTER, spaceAfter=3),
        "portada_periodo": ParagraphStyle(
            "PortadaPeriodo", parent=base["Normal"], fontSize=16, leading=22,
            textColor=VERDE, alignment=TA_CENTER, spaceBefore=10),
        "h2": ParagraphStyle(
            "H2", parent=base["Heading2"], fontSize=14, leading=18,
            textColor=TINTA, spaceBefore=4, spaceAfter=2),
        "texto": ParagraphStyle(
            "Texto", parent=base["Normal"], fontSize=10, leading=15,
            textColor=TINTA),
        "nota": ParagraphStyle(
            "Nota", parent=base["Normal"], fontSize=8.5, leading=12.5,
            textColor=GRIS),
        "celda": ParagraphStyle(
            "Celda", parent=base["Normal"], fontSize=9.5, leading=12.5,
            textColor=TINTA),
        "celda_der": ParagraphStyle(
            "CeldaDer", parent=base["Normal"], fontSize=9.5, leading=12.5,
            textColor=TINTA, alignment=TA_RIGHT),
    }


def imagen_desde_data_url(data_url: str, ancho: float, alto: float):
    """
    Convierte una data URL base64 en un `Image` de ReportLab.

    Los logotipos y las fotos se guardan como data URL en la propia base, así
    que hay que decodificarlos antes de dibujarlos. Devuelve None ante cualquier
    problema —dato ausente, base64 corrupto, formato no soportado— porque un
    reporte sin logotipo sigue siendo útil y uno que revienta al generarse no.
    """
    if not data_url or not isinstance(data_url, str):
        return None
    if not data_url.startswith("data:image"):
        return None

    try:
        import base64, io as _io
        cabecera, _, datos = data_url.partition(",")
        if not datos:
            return None
        crudo = base64.b64decode(datos)

        from reportlab.lib.utils import ImageReader
        lector = ImageReader(_io.BytesIO(crudo))

        # Se respeta la proporción original: un logotipo apaisado estirado a un
        # cuadrado queda deformado y da peor impresión que no ponerlo.
        w, h = lector.getSize()
        if w <= 0 or h <= 0:
            return None
        escala = min(ancho / w, alto / h)
        return Image(_io.BytesIO(crudo), width=w * escala, height=h * escala)
    except Exception:
        return None


class LineaAcento(Flowable):
    """Regla horizontal de color, para separar el título de su contenido."""

    def __init__(self, ancho=ANCHO_UTIL, grosor=2.2, color=VERDE):
        super().__init__()
        self.ancho, self.grosor, self.color = ancho, grosor, color
        self.width, self.height = ancho, grosor

    def wrap(self, ancho_disp, alto_disp):
        # Se declara explícitamente en lugar de confiar en la implementación por
        # defecto de Flowable, que según la versión de ReportLab puede no
        # devolver el tamaño y romper la maquetación.
        return self.ancho, self.grosor

    def draw(self):
        self.canv.setFillColor(self.color)
        self.canv.rect(0, 0, self.ancho, self.grosor, stroke=0, fill=1)


def seccion(titulo: str, st: dict, descripcion: str = "") -> list:
    """
    Encabezado de sección: título, línea de acento y, si procede, una frase que
    explique qué se está mirando.
    """
    piezas = [
        Spacer(1, 0.55 * cm),
        Paragraph(titulo, st["h2"]),
        LineaAcento(ancho=3.2 * cm),
        Spacer(1, 0.28 * cm),
    ]
    if descripcion:
        piezas += [Paragraph(descripcion, st["nota"]), Spacer(1, 0.22 * cm)]
    return piezas


def tarjetas_kpi(items: list, st: dict, por_fila: int = 4) -> Table:
    """
    Fila de indicadores destacados.

    `items` es [(etiqueta, valor, color_opcional), ...]. Son lo primero que se
    mira en un reporte, así que van en tarjetas con el número grande en lugar de
    perdidos dentro de una tabla de dos columnas.
    """
    if not items:
        return None

    st_etq = ParagraphStyle("KpiEtq", parent=st["nota"], fontSize=7.8,
                            textColor=GRIS, alignment=TA_CENTER, leading=10)

    filas, fila_actual = [], []
    for i, item in enumerate(items):
        etiqueta, valor = item[0], item[1]
        color = item[2] if len(item) > 2 else VERDE
        st_val = ParagraphStyle(f"KpiVal{i}", parent=st["texto"], fontSize=17,
                                textColor=color, alignment=TA_CENTER,
                                leading=21, fontName="Helvetica-Bold")
        celda = [Paragraph(str(valor), st_val), Paragraph(etiqueta.upper(), st_etq)]
        fila_actual.append(Table([[c] for c in celda], colWidths=[ANCHO_UTIL / por_fila - 0.3 * cm],
                                 style=TableStyle([
                                     ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
                                     ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
                                     ("TOPPADDING",    (0, 0), (-1, 0), 10),
                                     ("BOTTOMPADDING", (0, -1), (-1, -1), 9),
                                     ("LEFTPADDING",   (0, 0), (-1, -1), 4),
                                     ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
                                 ])))
        if len(fila_actual) == por_fila:
            filas.append(fila_actual)
            fila_actual = []

    if fila_actual:
        # La última fila se completa con huecos para que las tarjetas conserven
        # su ancho en lugar de estirarse a repartirse la página.
        while len(fila_actual) < por_fila:
            fila_actual.append("")
        filas.append(fila_actual)

    t = Table(filas, colWidths=[ANCHO_UTIL / por_fila] * por_fila, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), SUAVE),
        ("BOX",          (0, 0), (-1, -1), 0.5, SUAVE),
        ("INNERGRID",    (0, 0), (-1, -1), 3, BLANCO),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def tabla(filas: list, anchos: list, st: dict, alinear_derecha=None) -> Table:
    """
    Tabla con cabecera de color y filas alternas.

    `alinear_derecha` son los índices de columna con cifras: alinearlas a la
    derecha permite comparar magnitudes de un vistazo, que es para lo que se
    mira una columna de importes.

    El contenido se envuelve en Paragraph para que los textos largos salten de
    línea dentro de la celda en vez de desbordarla o salir recortados.
    """
    if not filas:
        return None

    alinear_derecha = set(alinear_derecha or [])
    st_cab = ParagraphStyle("Cab", parent=st["celda"], textColor=BLANCO,
                            fontName="Helvetica-Bold", fontSize=9.5)
    st_cab_der = ParagraphStyle("CabDer", parent=st_cab, alignment=TA_RIGHT)

    cuerpo = []
    for i, fila in enumerate(filas):
        nueva = []
        for j, celda in enumerate(fila):
            if i == 0:
                estilo = st_cab_der if j in alinear_derecha else st_cab
            else:
                estilo = st["celda_der"] if j in alinear_derecha else st["celda"]
            nueva.append(Paragraph(str(celda), estilo))
        cuerpo.append(nueva)

    t = Table(cuerpo, colWidths=anchos, hAlign="LEFT", repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), VERDE),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING",   (0, 0), (-1, -1), 9),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 9),
        # Cebra: en tablas de muchas filas evita seguir la línea equivocada.
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [BLANCO, CEBRA]),
        ("LINEBELOW",     (0, 1), (-1, -2), 0.4, SUAVE),
        ("BOX",           (0, 0), (-1, -1), 0.6, SUAVE),
    ]))
    return t


def portada(*, titulo: str, subtitulo: str, periodo: str, st: dict,
            logo_data_url: str = None, pie: str = "") -> list:
    """
    Primera página del reporte, con el logotipo del gimnasio si lo tiene.

    Sin logotipo se cae a una banda con la inicial, en lugar de dejar un hueco:
    una portada con un vacío en el centro parece que falló algo.
    """
    piezas = [Spacer(1, 3.2 * cm)]

    logo = imagen_desde_data_url(logo_data_url, 4.2 * cm, 4.2 * cm)
    if logo:
        logo.hAlign = "CENTER"
        piezas += [logo, Spacer(1, 0.9 * cm)]
    else:
        inicial = (subtitulo or titulo or "G").strip()[:1].upper()
        st_ini = ParagraphStyle("Inicial", parent=st["portada_titulo"],
                                fontSize=42, textColor=BLANCO, leading=48)
        marca = Table([[Paragraph(inicial, st_ini)]], colWidths=[3.4 * cm], rowHeights=[3.4 * cm])
        marca.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), VERDE),
            ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN",      (0, 0), (-1, -1), "CENTER"),
        ]))
        marca.hAlign = "CENTER"
        piezas += [marca, Spacer(1, 0.9 * cm)]

    piezas += [
        Paragraph(titulo, st["portada_titulo"]),
        Paragraph(subtitulo, st["portada_sub"]),
        Spacer(1, 0.35 * cm),
        Paragraph(periodo, st["portada_periodo"]),
        Spacer(1, 1.4 * cm),
    ]

    linea = LineaAcento(ancho=5 * cm)
    linea.hAlign = "CENTER"
    piezas.append(linea)

    if pie:
        piezas += [Spacer(1, 0.8 * cm),
                   Paragraph(pie, ParagraphStyle("PiePortada", parent=st["nota"],
                                                 alignment=TA_CENTER))]
    return piezas


def marco_pagina(*, titulo: str, gimnasio: str, logo_data_url: str = None):
    """
    Devuelve la función que dibuja el encabezado y el pie en cada página.
    Se pasa a `doc.build(..., onLaterPages=...)`.

    La portada no lo lleva: un encabezado sobre la portada la ensucia. Por eso
    se usa `onFirstPage` distinto en quien lo consume.

    El logotipo se decodifica UNA vez y se reutiliza en todas las páginas; hacerlo
    en cada una multiplicaría el trabajo por el número de hojas.
    """
    imagen = None
    if logo_data_url and isinstance(logo_data_url, str) and logo_data_url.startswith("data:image"):
        try:
            import base64, io as _io
            from reportlab.lib.utils import ImageReader
            _, _, datos = logo_data_url.partition(",")
            imagen = ImageReader(_io.BytesIO(base64.b64decode(datos)))
        except Exception:
            imagen = None

    def dibujar(canvas, doc):
        canvas.saveState()
        ancho, alto = doc.pagesize

        # ── Encabezado ───────────────────────────────────────────────────────
        y_linea = alto - 1.45 * cm
        x_texto = 2 * cm

        if imagen:
            try:
                w, h = imagen.getSize()
                escala = min(1.0 * cm / w, 1.0 * cm / h)
                canvas.drawImage(imagen, 2 * cm, y_linea - 0.18 * cm,
                                 width=w * escala, height=h * escala,
                                 mask="auto", preserveAspectRatio=True)
                x_texto = 3.2 * cm
            except Exception:
                pass

        canvas.setFont("Helvetica-Bold", 8.5)
        canvas.setFillColor(TINTA)
        canvas.drawString(x_texto, y_linea + 0.12 * cm, gimnasio[:48])
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(GRIS)
        canvas.drawString(x_texto, y_linea - 0.28 * cm, titulo[:60])

        canvas.setStrokeColor(SUAVE)
        canvas.setLineWidth(0.7)
        canvas.line(2 * cm, y_linea - 0.55 * cm, ancho - 2 * cm, y_linea - 0.55 * cm)

        # ── Pie ──────────────────────────────────────────────────────────────
        canvas.setStrokeColor(SUAVE)
        canvas.line(2 * cm, 1.5 * cm, ancho - 2 * cm, 1.5 * cm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(GRIS)
        canvas.drawString(2 * cm, 1.1 * cm,
                          f"Generado por GymPro · {datetime.now().strftime('%d/%m/%Y %H:%M')}")
        # El numero de pagina a la derecha es donde lo busca la vista al hojear.
        canvas.drawRightString(ancho - 2 * cm, 1.1 * cm, f"Página {doc.page}")

        canvas.restoreState()

    return dibujar


def marco_portada(canvas, doc):
    """
    Adorno de la portada: una banda de color arriba y otra abajo.

    Sin encabezado ni número de página, que en una portada sobran.
    """
    canvas.saveState()
    ancho, alto = doc.pagesize
    canvas.setFillColor(VERDE)
    canvas.rect(0, alto - 0.55 * cm, ancho, 0.55 * cm, stroke=0, fill=1)
    canvas.setFillColor(VERDE_OSC)
    canvas.rect(0, 0, ancho, 0.35 * cm, stroke=0, fill=1)
    canvas.restoreState()
