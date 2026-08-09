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
from reportlab.graphics.shapes import Drawing, String, Rect, Line, Group
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.widgets.markers import makeMarker

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


def _subtitulo(dibujo: Drawing, texto: str, y: float) -> None:
    """
    Línea de contexto bajo el título: el total, el promedio o el máximo.

    Una gráfica dice cómo se reparte algo, pero no cuánto es ese algo. El
    subtítulo pone la cifra global para no tener que sumar las barras a ojo.
    """
    if not texto:
        return
    dibujo.add(String(0, y, texto, fontName="Helvetica",
                      fontSize=7.5, fillColor=COLOR_GRIS))


def _formato_corto(valor: float) -> str:
    """
    Cifra compacta, para rótulos donde no cabe el importe completo.

    Ya no se usa en las etiquetas de las gráficas: estas rotulan solo el máximo
    y el mínimo, y con el importe exacto (véase `_formato_extremos`). Se
    conserva para rótulos futuros con restricción de espacio.
    """
    v = float(valor or 0)
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:.1f}M".replace(".0M", "M")
    if abs(v) >= 1_000:
        return f"{v / 1_000:.1f}k".replace(".0k", "k")
    if v != int(v):
        return f"{v:.1f}"
    return str(int(v))


def _dinero(valor: float) -> str:
    """Importe con separador de miles, para los subtítulos."""
    return f"${float(valor or 0):,.2f}"


def _dinero_entero(valor: float) -> str:
    """Importe sin centavos, para las etiquetas dentro del gráfico."""
    return f"${float(valor or 0):,.0f}"


def _formato_extremos(valores, moneda=False, unidad=""):
    """
    Formateador que rotula únicamente el valor más alto y el más bajo.

    Antes se etiquetaba cada punto con la cifra abreviada (7.3k) y se omitían
    todas cuando había muchas categorías, con lo que el pico —que es el dato
    que se busca— quedaba justo sin rotular en las series largas. Ahora se
    escriben solo dos etiquetas, y con el importe completo, de modo que caben
    siempre y no hace falta deducir la cifra del eje.

    ReportLab pasa a estos formateadores el valor y no su posición, así que el
    extremo se reconoce por su propio valor: si se repite, se rotulan ambas
    apariciones, que muestran la misma cifra.

    Los ceros quedan fuera: un mes sin cobros es ausencia de dato y no el
    mínimo del periodo.
    """
    nums = [
        float(v) for v in _aplanar(valores)
        if isinstance(v, (int, float)) and float(v) != 0
    ]
    if not nums:
        return lambda v: ""

    alto, bajo = max(nums), min(nums)
    fmt = _dinero_entero if moneda else (
        lambda v: f"{v:,.0f}{unidad}".replace(",", " ")
    )

    def etiqueta(valor):
        if not valor:
            return ""
        v = float(valor)
        return fmt(v) if v in (alto, bajo) else ""

    return etiqueta


def _aplanar(valores):
    """Aplana una lista de series en una sola secuencia de valores."""
    for v in valores or []:
        if isinstance(v, (list, tuple)):
            for x in v:
                yield x
        else:
            yield v


def _rotular_ejes(dibujo: Drawing, grafico, eje_x: str = "", eje_y: str = "") -> None:
    """
    Escribe qué mide cada eje.

    Sin rótulo, un "8k" en el eje vertical puede ser pesos, visitas o miembros;
    hay que deducirlo del título y no siempre alcanza. El eje Y va girado 90°
    junto al margen izquierdo, como es costumbre en cualquier gráfica.

    Se dibujan como texto suelto sobre el Drawing y no como propiedad del eje
    porque ReportLab no ofrece un rótulo de eje propiamente dicho.
    """
    if eje_x:
        # Centrado bajo el área de trazado, por debajo de las etiquetas de
        # categoría para no pisarlas.
        dibujo.add(String(grafico.x + grafico.width / 2, max(2, grafico.y - 26),
                          eje_x, textAnchor="middle",
                          fontName="Helvetica-Bold", fontSize=7.5,
                          fillColor=COLOR_GRIS))
    if eje_y:
        etiqueta = String(0, 0, eje_y, textAnchor="middle",
                          fontName="Helvetica-Bold", fontSize=7.5,
                          fillColor=COLOR_GRIS)
        etiqueta.textAnchor = "middle"
        grupo = Group(etiqueta)
        grupo.translate(10, grafico.y + grafico.height / 2)
        grupo.rotate(90)
        dibujo.add(grupo)


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


def barras_comparadas(etiquetas, series, titulo="", alto=8.2 * cm, moneda=False,
                      eje_x="", eje_y=""):
    """
    Barras agrupadas. `series` es [(nombre, [valores], color), ...].

    Se usa para comparar dos magnitudes sobre las mismas categorías, por
    ejemplo membresías contra punto de venta mes a mes.

    Cada barra lleva su valor encima y el subtítulo da el total, para no tener
    que leer la altura contra el eje y estimar a ojo. `moneda` cambia el formato
    del resumen a importes.
    """
    if not etiquetas or not series:
        return _sin_datos()
    if not any(any(v for v in s[1]) for s in series):
        return _sin_datos("Sin importes registrados en el periodo")

    d = Drawing(ANCHO, alto)
    # Con rótulo de eje X hace falta una franja extra abajo para que no se
    # solape con las etiquetas de categoría.
    y_grafico = 56 if eje_x else 40
    if titulo:
        _titulo(d, titulo, alto - 12)

    # Resumen: total de cada serie y, con varias, el total conjunto.
    totales = [(s[0], sum(v or 0 for v in s[1])) for s in series]
    fmt = _dinero if moneda else (lambda v: f"{v:,.0f}".replace(",", " "))
    if len(totales) == 1:
        resumen = f"Total: {fmt(totales[0][1])}  ·  {len(etiquetas)} categorías"
    else:
        partes = [f"{n}: {fmt(t)}" for n, t in totales]
        resumen = "  ·  ".join(partes) + f"  ·  Total: {fmt(sum(t for _, t in totales))}"
    _subtitulo(d, resumen, alto - 25)

    g = VerticalBarChart()
    g.x = 52 if eje_y else 40
    g.y = y_grafico
    g.width  = ANCHO - g.x - 20
    g.height = alto - y_grafico - 48
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

    # Importe exacto sobre la barra más alta y la más baja del gráfico. Solo
    # esas dos: rotular todas las barras las solapaba en cuanto había varias
    # categorías, y obligaba a omitirlas justo cuando más falta hacían.
    g.barLabels.fontName = "Helvetica-Bold"
    g.barLabels.fontSize = 7
    g.barLabels.fillColor = COLOR_TINTA
    g.barLabels.dy = 5
    g.barLabelFormat = _formato_extremos([s[1] for s in series], moneda=moneda)

    for i, (_, _, color) in enumerate(series):
        g.bars[i].fillColor = color
        g.bars[i].strokeColor = None

    d.add(g)
    _rotular_ejes(d, g, eje_x, eje_y)

    # Leyenda: sin ella dos barras de colores no dicen qué es cada una.
    if len(series) > 1:
        leyenda = Legend()
        leyenda.x = g.x
        leyenda.y = 8
        leyenda.alignment = "right"
        leyenda.columnMaximum = 1
        leyenda.fontName = "Helvetica"
        leyenda.fontSize = 8
        leyenda.dxTextSpace = 4
        leyenda.deltax = 100
        # La leyenda lleva el total de cada serie: así se sabe cuál pesa más sin
        # sumar sus barras.
        leyenda.colorNamePairs = [
            (s[2], f"{s[0]} ({fmt(t)})") for s, (_, t) in zip(series, totales)
        ]
        d.add(leyenda)

    return d


def linea_temporal(etiquetas, valores, titulo="", color=COLOR_INGRESOS,
                   alto=7.8 * cm, moneda=True, eje_x="", eje_y=""):
    """
    Línea simple sobre el tiempo: evolución de una sola magnitud.

    El subtítulo resume la serie —total, promedio, mejor y peor punto— y la
    línea lleva el valor sobre cada vértice. Una curva sin cifras dice si sube o
    baja, pero no cuánto, y ese "cuánto" es justo lo que se busca al mirar los
    ingresos de un mes.
    """
    limpios = [float(v or 0) for v in valores]
    if not etiquetas or not limpios or not any(limpios):
        return _sin_datos()

    d = Drawing(ANCHO, alto)
    if titulo:
        _titulo(d, titulo, alto - 12)

    fmt = _dinero if moneda else (lambda v: f"{v:,.0f}".replace(",", " "))
    total   = sum(limpios)
    promedio = total / len(limpios)
    i_max = limpios.index(max(limpios))
    i_min = limpios.index(min(limpios))

    partes = [f"Total: {fmt(total)}", f"Promedio: {fmt(promedio)}",
              f"Máximo: {etiquetas[i_max]} ({fmt(limpios[i_max])})"]
    # El mínimo solo aporta si es distinto del máximo; con un único punto
    # repetirlo sería ruido.
    if i_min != i_max:
        partes.append(f"Mínimo: {etiquetas[i_min]} ({fmt(limpios[i_min])})")
    _subtitulo(d, "  ·  ".join(partes), alto - 25)

    # Variación entre el primer y el último punto: la lectura que casi siempre
    # se busca en una serie temporal.
    if len(limpios) > 1 and limpios[0] > 0:
        cambio = (limpios[-1] - limpios[0]) / limpios[0] * 100
        signo = "+" if cambio >= 0 else ""
        d.add(String(0, alto - 37,
                     f"Del primero al último punto: {signo}{cambio:.1f}%",
                     fontName="Helvetica-Oblique", fontSize=7.5,
                     fillColor=COLOR_REAL if cambio >= 0 else COLOR_POS))

    g = HorizontalLineChart()
    g.x = 52 if eje_y else 40
    g.y = 42 if eje_x else 25
    g.width  = ANCHO - g.x - 20
    g.height = alto - g.y - 50
    g.data = [limpios]
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
    g.lines[0].symbol = makeMarker("FilledCircle", size=4, fillColor=color)

    # Importe exacto en el pico y en el valle de la serie, con independencia de
    # cuántos puntos tenga: al ser solo dos etiquetas no llegan a pisarse.
    g.lineLabels.fontName = "Helvetica-Bold"
    g.lineLabels.fontSize = 7
    g.lineLabels.fillColor = COLOR_TINTA
    g.lineLabels.dy = 7
    g.lineLabelFormat = _formato_extremos(limpios, moneda=moneda)

    d.add(g)
    _rotular_ejes(d, g, eje_x, eje_y)
    return d


def pastel(etiquetas, valores, titulo="", alto=7.6 * cm, moneda=True, unidad=""):
    """
    Reparto porcentual. Se usa para métodos de pago y similares.

    La leyenda muestra el porcentaje Y la cifra. Solo con el porcentaje se sabe
    cómo se reparte el pastel pero no de cuánto es: "PayPal 79.5 %" no dice si
    son ochocientos pesos o cien mil.

    Las porciones por debajo del 2 % se agrupan en "Otros": rebanadas
    minúsculas con su etiqueta encima vuelven el gráfico ilegible.
    """
    pares = [(e, float(v or 0)) for e, v in zip(etiquetas, valores) if (v or 0) > 0]
    if not pares:
        return _sin_datos()

    total = sum(v for _, v in pares) or 1
    grandes = [(e, v) for e, v in pares if v / total >= 0.02]
    resto   = sum(v for e, v in pares if v / total < 0.02)
    n_agrupados = sum(1 for e, v in pares if v / total < 0.02)
    if resto > 0:
        grandes.append((f"Otros ({n_agrupados})", resto))
    grandes.sort(key=lambda p: p[1], reverse=True)

    fmt = _dinero if moneda else (lambda v: f"{v:,.0f}{unidad}".replace(",", " "))

    d = Drawing(ANCHO, alto)
    if titulo:
        _titulo(d, titulo, alto - 12)

    lider, valor_lider = grandes[0]
    _subtitulo(
        d,
        f"Total: {fmt(total)}  ·  {len(pares)} "
        f"{'categoría' if len(pares) == 1 else 'categorías'}  ·  "
        f"Predomina {_abreviar(lider, 22)} con {valor_lider / total * 100:.1f}%",
        alto - 25,
    )

    p = Pie()
    p.x = 20
    p.y = 10
    p.width = p.height = alto - 52
    p.data = [v for _, v in grandes]
    p.labels = None          # las etiquetas van en la leyenda, no sobre el pastel
    p.slices.strokeColor = rl_colors.white
    p.slices.strokeWidth = 1
    for i in range(len(grandes)):
        p.slices[i].fillColor = PALETA_CATEGORIAS[i % len(PALETA_CATEGORIAS)]
    d.add(p)

    leyenda = Legend()
    leyenda.x = alto - 14
    leyenda.y = alto - 46
    leyenda.alignment = "right"
    leyenda.columnMaximum = 8
    leyenda.fontName = "Helvetica"
    leyenda.fontSize = 8
    leyenda.dxTextSpace = 5
    leyenda.colorNamePairs = [
        (PALETA_CATEGORIAS[i % len(PALETA_CATEGORIAS)],
         f"{_abreviar(e, 16)}  {v / total * 100:.1f}%  ·  {fmt(v)}")
        for i, (e, v) in enumerate(grandes)
    ]
    d.add(leyenda)
    return d


def barras_horizontales(etiquetas, valores, titulo="", color=COLOR_ASISTENCIA,
                        alto=None, moneda=True, max_filas=8,
                        eje_x="", eje_y=""):
    """
    Ranking: productos más vendidos, clientes con más sesiones.

    Se dibuja a mano y no con VerticalBarChart girado porque así las etiquetas
    quedan a la izquierda, legibles, sin rotar el texto.

    Cada fila muestra su posición, el valor y qué porcentaje del total
    representa. El porcentaje importa: saber que un producto vendió 4 800 no
    dice nada hasta saber si eso es la mitad del negocio o una migaja.
    """
    pares = [(e, float(v or 0)) for e, v in zip(etiquetas, valores)]
    pares = [p for p in pares if p[1] > 0]
    if not pares:
        return _sin_datos()

    total_general = sum(v for _, v in pares)
    n_total = len(pares)
    pares = sorted(pares, key=lambda p: p[1], reverse=True)[:max_filas]

    maximo = max(v for _, v in pares) or 1
    fila   = 0.66 * cm
    # Franja extra abajo si hay que rotular el eje horizontal.
    margen_inferior = 0.75 * cm if eje_x else 0
    alto   = alto or (len(pares) * fila + 2.1 * cm + margen_inferior)

    fmt = _dinero if moneda else (lambda v: f"{v:,.0f}".replace(",", " "))

    d = Drawing(ANCHO, alto)
    if titulo:
        _titulo(d, titulo, alto - 12)

    mostrados = sum(v for _, v in pares)
    resumen = f"Total: {fmt(total_general)}"
    if n_total > len(pares):
        # Si el ranking se recorta hay que decirlo, o el lector suma las barras
        # y no le cuadra con el total.
        resumen += (f"  ·  Se muestran los {len(pares)} primeros de {n_total} "
                    f"({mostrados / total_general * 100:.0f}% del total)")
    else:
        resumen += f"  ·  {n_total} {'elemento' if n_total == 1 else 'elementos'}"
    _subtitulo(d, resumen, alto - 25)

    x_etiqueta = 0
    x_barra    = 5.6 * cm
    # Se reserva más hueco a la derecha: ahí van el valor y el porcentaje.
    ancho_max  = ANCHO - x_barra - 3.6 * cm

    # Rótulo del eje vertical: aquí las categorías van a la izquierda, así que
    # se escribe encima de esa columna en lugar de girarlo.
    if eje_y:
        d.add(String(x_etiqueta, alto - 1.55 * cm, eje_y.upper(),
                     fontName="Helvetica-Bold", fontSize=6.5, fillColor=COLOR_GRIS))
    if eje_x:
        d.add(String(x_barra, alto - 1.55 * cm, eje_x.upper(),
                     fontName="Helvetica-Bold", fontSize=6.5, fillColor=COLOR_GRIS))

    for i, (etiqueta, valor) in enumerate(pares):
        y = alto - 1.9 * cm - (i + 1) * fila + 6

        # Posición en el ranking: ordena la lectura de un vistazo.
        d.add(String(x_etiqueta, y, f"{i + 1}.",
                     fontName="Helvetica-Bold", fontSize=7.5, fillColor=COLOR_GRIS))
        d.add(String(x_etiqueta + 0.45 * cm, y, _abreviar(etiqueta, 28),
                     fontName="Helvetica", fontSize=8, fillColor=COLOR_TINTA))

        ancho = max(1.0, ancho_max * (valor / maximo))
        # Riel de fondo: hace visible cuánto le falta a cada barra respecto al
        # primero, que sin él hay que estimar a ojo.
        d.add(Rect(x_barra, y - 3, ancho_max, fila * 0.58,
                   fillColor=COLOR_SUAVE, strokeColor=None, rx=2, ry=2))
        d.add(Rect(x_barra, y - 3, ancho, fila * 0.58,
                   fillColor=color, strokeColor=None, rx=2, ry=2))

        d.add(String(x_barra + ancho_max + 5, y, fmt(valor),
                     fontName="Helvetica-Bold", fontSize=7.5, fillColor=COLOR_TINTA))
        if total_general > 0:
            d.add(String(ANCHO, y, f"{valor / total_general * 100:.0f}%",
                         textAnchor="end",
                         fontName="Helvetica", fontSize=7, fillColor=COLOR_GRIS))

    return d


def linea_con_prediccion(etiquetas, reales, predichos, titulo="", alto=7.8 * cm,
                         eje_x="Fecha", eje_y="Peso (kg)"):
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

    # Resumen de la proyección: dónde está hoy y hacia dónde va. Es la lectura
    # que se busca al mirar una predicción, y obligaba a comparar dos puntos de
    # la curva a ojo.
    med = [float(v) for v in reales if v is not None]
    pre = [float(v) for v in predichos if v is not None]
    if med and pre:
        actual, futuro = med[-1], pre[-1]
        delta = futuro - actual
        signo = "+" if delta >= 0 else ""
        _subtitulo(
            d,
            f"Última medición: {actual:.1f}  ·  Proyección: {futuro:.1f}  ·  "
            f"Cambio estimado: {signo}{delta:.1f}  ·  "
            f"{len(med)} {'medición' if len(med) == 1 else 'mediciones'}",
            alto - 25,
        )
    elif med:
        _subtitulo(d, f"Última medición: {med[-1]:.1f}  ·  "
                      f"{len(med)} {'medición' if len(med) == 1 else 'mediciones'}",
                   alto - 25)

    g = HorizontalLineChart()
    g.x = 52 if eje_y else 40
    g.y = 48 if eje_x else 32
    g.width  = ANCHO - g.x - 20
    g.height = alto - g.y - 50
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
    # Círculo relleno en lo medido y hueco en lo proyectado: aunque el PDF se
    # imprima en blanco y negro, la forma del punto distingue el dato real del
    # estimado, cosa que el color por sí solo no logra.
    g.lines[0].symbol = makeMarker("FilledCircle", size=4, fillColor=COLOR_REAL)
    g.lines[1].strokeColor = rl_colors.HexColor("#A855F7")
    g.lines[1].strokeWidth = 2
    g.lines[1].strokeDashArray = (3, 2)
    g.lines[1].symbol = makeMarker("Circle", size=4,
                                   strokeColor=rl_colors.HexColor("#A855F7"),
                                   fillColor=rl_colors.white)

    if len(etiquetas) <= 9:
        g.lineLabels.fontName = "Helvetica-Bold"
        g.lineLabels.fontSize = 6.5
        g.lineLabels.fillColor = COLOR_TINTA
        g.lineLabels.dy = 7
        g.lineLabelFormat = lambda v: f"{v:.1f}" if v else ""

    d.add(g)
    _rotular_ejes(d, g, eje_x, eje_y)

    leyenda = Legend()
    leyenda.x = g.x
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
