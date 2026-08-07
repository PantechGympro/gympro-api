"""
utils/graficas_pdf.py — Gráficas para los reportes en PDF.

Usa el módulo `reportlab.graphics`, que ya viene con ReportLab. Se descartó
matplotlib a propósito: añadiría unos 60 MB a la imagen de Docker y arrastra
dependencias de sistema, para dibujar cuatro gráficos sencillos que ReportLab
resuelve sin salir de la librería que ya se usa para el documento.

Todas las funciones devuelven un `Drawing` listo para meter en el `story` del
PDF, o None cuando no hay datos que dibujar. Devolver None y no un gráfico
vacío es deliberado: quien arma el reporte decide si omite la sección o pone
una nota, en lugar de dejar un recuadro en blanco que parece un fallo.

La paleta reproduce la del sistema (web/src/components/compartido/InfoGrafico.jsx)
para que el reporte impreso y la pantalla usen el mismo color para lo mismo.
"""
from reportlab.lib import colors as rl_colors
from reportlab.lib.units import cm
from reportlab.graphics.shapes import Drawing, String, Rect, Line
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.charts.legends import Legend

# Mismos tonos que la interfaz web
COLOR_INGRESOS   = rl_colors.HexColor("#6366F1")
COLOR_POS        = rl_colors.HexColor("#F59E0B")
COLOR_MEMBRESIAS = rl_colors.HexColor("#8B5CF6")
COLOR_ASISTENCIA = rl_colors.HexColor("#0EA5E9")
COLOR_REAL       = rl_colors.HexColor("#22C55E")
COLOR_TINTA      = rl_colors.HexColor("#0F1720")
COLOR_GRIS       = rl_colors.HexColor("#5A6673")
COLOR_SUAVE      = rl_colors.HexColor("#EAEFF3")

PALETA_CATEGORIAS = [
    COLOR_INGRESOS, COLOR_POS, COLOR_ASISTENCIA, COLOR_MEMBRESIAS,
    COLOR_REAL, rl_colors.HexColor("#EC4899"), rl_colors.HexColor("#14B8A6"),
]

ANCHO = 16 * cm


def _titulo(dibujo: Drawing, texto: str, y: float) -> None:
    """Rótulo del gráfico dentro del propio Drawing."""
    dibujo.add(String(0, y, texto, fontName="Helvetica-Bold",
                      fontSize=10, fillColor=COLOR_TINTA))


def _sin_datos(texto: str = "Sin datos en el periodo") -> Drawing:
    """Recuadro discreto para cuando una serie viene vacía."""
    d = Drawing(ANCHO, 2 * cm)
    d.add(Rect(0, 0, ANCHO, 2 * cm, fillColor=COLOR_SUAVE,
               strokeColor=None, rx=4, ry=4))
    d.add(String(ANCHO / 2, 0.9 * cm, texto, textAnchor="middle",
                 fontName="Helvetica-Oblique", fontSize=9, fillColor=COLOR_GRIS))
    return d


def _abreviar(texto: str, limite: int = 14) -> str:
    """Recorta etiquetas largas: en el eje X no caben nombres completos."""
    t = str(texto or "")
    return t if len(t) <= limite else t[: limite - 1] + "…"


def _limite_eje(n_categorias: int) -> int:
    """
    Caracteres que caben en una etiqueta del eje X según cuántas haya.

    Con pocas categorías cada una dispone de mucho espacio horizontal y no hay
    razón para recortarla: "Punto de venta" salía como "Punto de …" aunque
    sobrase sitio de sobra, porque el límite era fijo.
    """
    if n_categorias <= 2:
        return 24
    if n_categorias <= 4:
        return 16
    if n_categorias <= 8:
        return 11
    return 8


def barras_comparadas(etiquetas, series, titulo="", alto=7 * cm):
    """
    Barras agrupadas. `series` es [(nombre, [valores], color), ...].

    Se usa para comparar dos magnitudes sobre las mismas categorías, por
    ejemplo membresías contra punto de venta mes a mes.
    """
    if not etiquetas or not series:
        return _sin_datos()
    if not any(any(v for v in s[1]) for s in series):
        return _sin_datos("Sin importes registrados en el periodo")

    d = Drawing(ANCHO, alto)
    if titulo:
        _titulo(d, titulo, alto - 12)

    g = VerticalBarChart()
    g.x = 40
    g.y = 35
    g.width  = ANCHO - 60
    g.height = alto - 70
    g.data = [s[1] for s in series]
    g.categoryAxis.categoryNames = [_abreviar(e, _limite_eje(len(etiquetas))) for e in etiquetas]
    g.categoryAxis.labels.fontName = "Helvetica"
    g.categoryAxis.labels.fontSize = 7
    g.categoryAxis.labels.angle = 0 if len(etiquetas) <= 8 else 30
    g.categoryAxis.labels.dy = -6
    g.valueAxis.valueMin = 0
    g.valueAxis.labels.fontName = "Helvetica"
    g.valueAxis.labels.fontSize = 7
    g.valueAxis.gridStrokeColor = COLOR_SUAVE
    g.valueAxis.gridStrokeWidth = 0.5
    g.valueAxis.visibleGrid = 1
    g.barSpacing = 1
    g.groupSpacing = 8

    for i, (_, _, color) in enumerate(series):
        g.bars[i].fillColor = color
        g.bars[i].strokeColor = None

    d.add(g)

    # Leyenda: sin ella dos barras de colores no dicen qué es cada una.
    if len(series) > 1:
        leyenda = Legend()
        leyenda.x = 40
        leyenda.y = 8
        leyenda.alignment = "right"
        leyenda.columnMaximum = 1
        leyenda.fontName = "Helvetica"
        leyenda.fontSize = 8
        leyenda.dxTextSpace = 4
        leyenda.deltax = 90
        leyenda.colorNamePairs = [(s[2], s[0]) for s in series]
        d.add(leyenda)

    return d


def linea_temporal(etiquetas, valores, titulo="", color=COLOR_INGRESOS, alto=6.5 * cm):
    """Línea simple sobre el tiempo: evolución de una sola magnitud."""
    if not etiquetas or not valores or not any(valores):
        return _sin_datos()

    d = Drawing(ANCHO, alto)
    if titulo:
        _titulo(d, titulo, alto - 12)

    g = HorizontalLineChart()
    g.x = 40
    g.y = 25
    g.width  = ANCHO - 60
    g.height = alto - 55
    g.data = [list(valores)]
    g.categoryAxis.categoryNames = [_abreviar(e, _limite_eje(len(etiquetas))) for e in etiquetas]
    g.categoryAxis.labels.fontName = "Helvetica"
    g.categoryAxis.labels.fontSize = 7
    g.valueAxis.valueMin = 0
    g.valueAxis.labels.fontName = "Helvetica"
    g.valueAxis.labels.fontSize = 7
    g.valueAxis.gridStrokeColor = COLOR_SUAVE
    g.valueAxis.gridStrokeWidth = 0.5
    g.valueAxis.visibleGrid = 1
    g.lines[0].strokeColor = color
    g.lines[0].strokeWidth = 2
    g.lines[0].symbol = None
    d.add(g)
    return d


def pastel(etiquetas, valores, titulo="", alto=7 * cm):
    """
    Reparto porcentual. Se usa para métodos de pago y similares.

    Las porciones por debajo del 2 % se agrupan en "Otros": rebanadas
    minúsculas con su etiqueta encima vuelven el gráfico ilegible.
    """
    pares = [(e, float(v or 0)) for e, v in zip(etiquetas, valores) if (v or 0) > 0]
    if not pares:
        return _sin_datos()

    total = sum(v for _, v in pares) or 1
    grandes = [(e, v) for e, v in pares if v / total >= 0.02]
    resto   = sum(v for e, v in pares if v / total < 0.02)
    if resto > 0:
        grandes.append(("Otros", resto))
    grandes.sort(key=lambda p: p[1], reverse=True)

    d = Drawing(ANCHO, alto)
    if titulo:
        _titulo(d, titulo, alto - 12)

    p = Pie()
    p.x = 20
    p.y = 12
    p.width = p.height = alto - 45
    p.data = [v for _, v in grandes]
    p.labels = None          # las etiquetas van en la leyenda, no sobre el pastel
    p.slices.strokeColor = rl_colors.white
    p.slices.strokeWidth = 1
    for i in range(len(grandes)):
        p.slices[i].fillColor = PALETA_CATEGORIAS[i % len(PALETA_CATEGORIAS)]
    d.add(p)

    leyenda = Legend()
    leyenda.x = alto - 10
    leyenda.y = alto - 40
    leyenda.alignment = "right"
    leyenda.columnMaximum = 8
    leyenda.fontName = "Helvetica"
    leyenda.fontSize = 8
    leyenda.dxTextSpace = 5
    leyenda.colorNamePairs = [
        (PALETA_CATEGORIAS[i % len(PALETA_CATEGORIAS)],
         f"{_abreviar(e, 18)}  {v / total * 100:.1f}%")
        for i, (e, v) in enumerate(grandes)
    ]
    d.add(leyenda)
    return d


def barras_horizontales(etiquetas, valores, titulo="", color=COLOR_ASISTENCIA, alto=None):
    """
    Ranking: productos más vendidos, clientes con más sesiones.

    Se dibuja a mano y no con VerticalBarChart girado porque así las etiquetas
    quedan a la izquierda, legibles, sin rotar el texto.
    """
    pares = [(e, float(v or 0)) for e, v in zip(etiquetas, valores)]
    pares = [p for p in pares if p[1] > 0]
    if not pares:
        return _sin_datos()

    pares = pares[:8]
    maximo = max(v for _, v in pares) or 1
    fila   = 0.62 * cm
    alto   = alto or (len(pares) * fila + 1.4 * cm)

    d = Drawing(ANCHO, alto)
    if titulo:
        _titulo(d, titulo, alto - 12)

    x_etiqueta = 0
    x_barra    = 5.2 * cm
    ancho_max  = ANCHO - x_barra - 2.4 * cm

    for i, (etiqueta, valor) in enumerate(pares):
        y = alto - 1.2 * cm - (i + 1) * fila + 6
        d.add(String(x_etiqueta, y, _abreviar(etiqueta, 30),
                     fontName="Helvetica", fontSize=8, fillColor=COLOR_TINTA))
        ancho = max(1.0, ancho_max * (valor / maximo))
        d.add(Rect(x_barra, y - 3, ancho, fila * 0.6,
                   fillColor=color, strokeColor=None, rx=2, ry=2))
        d.add(String(x_barra + ancho + 5, y,
                     f"{valor:,.0f}".replace(",", " "),
                     fontName="Helvetica-Bold", fontSize=8, fillColor=COLOR_GRIS))

    return d


def linea_con_prediccion(etiquetas, reales, predichos, titulo="", alto=7 * cm):
    """
    Historial medido y proyección, en el mismo eje.

    `reales` y `predichos` deben tener la misma longitud que `etiquetas`, con
    None donde esa serie no tiene valor. El corte entre ambas se marca con una
    línea vertical: sin ella no se distingue dónde acaba lo medido y empieza lo
    estimado, que es justo lo que el lector necesita saber.
    """
    if not etiquetas:
        return _sin_datos()

    # Los huecos se dejan como None: HorizontalLineChart los interpreta como
    # tramos sin dato y no dibuja nada ahí.
    #
    # Es importante NO rellenarlos. Repetir el último valor conocido dibujaba
    # la línea de lo medido continuando plana más allá de la última medición,
    # que se lee como "el peso se mantuvo" cuando en realidad no hay dato; y
    # rellenar con cero hacía salir la proyección desde el suelo del eje.
    def _limpiar(serie):
        return [None if v is None else float(v) for v in serie]

    d = Drawing(ANCHO, alto)
    if titulo:
        _titulo(d, titulo, alto - 12)

    g = HorizontalLineChart()
    g.x = 40
    g.y = 32
    g.width  = ANCHO - 60
    g.height = alto - 62
    g.data = [_limpiar(reales), _limpiar(predichos)]
    g.categoryAxis.categoryNames = [_abreviar(e, _limite_eje(len(etiquetas))) for e in etiquetas]
    g.categoryAxis.labels.fontName = "Helvetica"
    g.categoryAxis.labels.fontSize = 6.5
    g.categoryAxis.labels.angle = 30
    g.categoryAxis.labels.dy = -6
    g.valueAxis.labels.fontName = "Helvetica"
    g.valueAxis.labels.fontSize = 7
    g.valueAxis.gridStrokeColor = COLOR_SUAVE
    g.valueAxis.gridStrokeWidth = 0.5
    g.valueAxis.visibleGrid = 1
    g.lines[0].strokeColor = COLOR_REAL
    g.lines[0].strokeWidth = 2
    g.lines[1].strokeColor = rl_colors.HexColor("#A855F7")
    g.lines[1].strokeWidth = 2
    g.lines[1].strokeDashArray = (3, 2)
    d.add(g)

    leyenda = Legend()
    leyenda.x = 40
    leyenda.y = 6
    leyenda.alignment = "right"
    leyenda.columnMaximum = 1
    leyenda.fontName = "Helvetica"
    leyenda.fontSize = 8
    leyenda.deltax = 100
    leyenda.colorNamePairs = [
        (COLOR_REAL, "Medido"),
        (rl_colors.HexColor("#A855F7"), "Proyectado"),
    ]
    d.add(leyenda)
    return d
