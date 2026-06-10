"""
utils/etl_ollama.py — Helpers compartidos para AI ETL vía Ollama.

Usado por:
  - routes/entrenador/diet_routes.py     (planes alimenticios)
  - routes/entrenador/trainer_routes.py  (rutinas y ejercicios)

Funciones públicas:
  extract_text(contenido, ext)              → str
  parse_routines_from_text(text)            → dict | None   (parser rápido sin LLM)
  check_ollama_ready()                      → (bool, str)
  call_ollama(system_prompt, document_text) → str           (JSON crudo vía Ollama)
  get_ollama_status()                       → dict
"""
from __future__ import annotations

import io
import os

import requests as _requests

OLLAMA_BASE  = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL",    "phi3:mini")


# ─── Extracción de texto ──────────────────────────────────────────────────────

def extract_text(contenido: bytes, ext: str) -> str:
    """Extrae texto plano de un PDF o Excel."""
    if ext == "pdf":
        import pdfplumber  # noqa: PLC0415
        with pdfplumber.open(io.BytesIO(contenido)) as pdf:
            return "\n\n".join(p.extract_text() or "" for p in pdf.pages)

    if ext in {"xlsx", "xls"}:
        import openpyxl  # noqa: PLC0415
        wb = openpyxl.load_workbook(io.BytesIO(contenido), data_only=True)
        lines: list[str] = []
        for ws in wb.worksheets:
            lines.append(f"[Hoja: {ws.title}]")
            for row in ws.iter_rows(values_only=True):
                celdas = [str(c) if c is not None else "" for c in row]
                if any(c.strip() for c in celdas):
                    lines.append(" | ".join(celdas))
        return "\n".join(lines)

    raise ValueError(f"Formato no soportado: .{ext}")


# ─── Parser determinístico para rutinas estructuradas ────────────────────────

def parse_routines_from_text(text: str) -> dict | None:
    """
    Parser rápido (sin LLM) para PDFs de planes de entrenamiento con formato tabular.

    Reconoce:
      - Encabezado de sesión:  "Sesión N: Título ⏱ X min"
      - Fila de ejercicio:     "Nombre del ejercicio  3  12"
      - Líneas partidas:       nombre en línea 1, 'sets reps' en línea 2 (tablas con wrap)

    Retorna {rutinas, ejercicios} si encuentra ≥1 sesión con ejercicios,
    o None para que el caller haga fallback a Ollama.
    """
    import re  # noqa: PLC0415

    _DAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

    def _muscle(title: str) -> str:
        t = title.lower()
        for keywords, muscle in [
            (["pierna", "glút", "femoral", "cuádric", "gemelo", "prensa", "rodilla", "talón"], "Piernas"),
            (["pecho", "empuje", "banca", "press"],                                             "Pecho"),
            (["espalda", "tracción", "remo", "jalón", "dorsal"],                               "Espalda"),
            (["hombro", "deltoid", "lateral"],                                                  "Hombros"),
            (["bícep"],                                                                          "Bíceps"),
            (["trícep"],                                                                         "Tríceps"),
            (["abdomen", "abdomin", "crunch", "core"],                                          "Abdomen"),
        ]:
            if any(k in t for k in keywords):
                return muscle
        return "Full Body"

    # "Sesión 1: Pierna & Abdomen (Enfoque Cuádriceps/Glúteo) ⏱ 72 min"
    # The \S{0,3} covers ⏱ (1 char) + optional whitespace before the number
    HDR = re.compile(
        r'^Sesi[oó]n\s+(\d+):\s+(.+?)(?:\s+\S{0,3}\s+(\d+)\s*min)?\s*$',
        re.IGNORECASE,
    )
    # Skip table-header rows — handles "EJERCICIO", "EJERCICIO SERIES REPETICIONES", etc.
    SKIP = re.compile(
        r'^\s*(EJERCICIO(\s+SERIES(\s+REPETICIONES)?)?|SERIES(\s+REPETICIONES)?|REPETICIONES)\s*$',
        re.IGNORECASE,
    )
    # "Exercise Name   3   12"  — one or more spaces between name and the two integers
    EX = re.compile(r'^(.+?)\s+(\d{1,2})\s+(\d{1,3})\s*$')

    days:              list[dict]         = []
    ejercicios_uniq:   dict[str, dict]    = {}
    cur_day:           str | None         = None
    cur_muscle                            = "Full Body"
    plan_duration                         = 60
    cur_exs:           list[dict]         = []
    partial                               = ""  # first line of a two-line wrapped cell

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            partial = ""
            continue

        # ── Session header ──────────────────────────────────────────────────
        hdr = HDR.match(line)
        if hdr:
            if cur_day is not None and cur_exs:
                days.append({"day": cur_day, "muscleGroup": cur_muscle, "exercises": cur_exs})
            idx        = int(hdr.group(1)) - 1
            cur_day    = _DAYS[idx] if idx < len(_DAYS) else f"Día {idx + 1}"
            cur_muscle = _muscle(hdr.group(2))
            if hdr.group(3):
                plan_duration = int(hdr.group(3))
            cur_exs = []
            partial = ""
            continue

        # Ignore table headers and any lines before the first session
        if SKIP.match(line) or cur_day is None:
            partial = ""
            continue

        # ── Exercise row ─────────────────────────────────────────────────────
        # Attempt match with partial prefix first (handles two-line cell wrap)
        candidate = (partial + " " + line).strip() if partial else line
        ex = EX.match(candidate)
        if ex:
            name = ex.group(1).strip()
            sets = ex.group(2)
            reps = ex.group(3)
            cur_exs.append({"name": name, "sets": sets, "reps": reps, "peso": "", "notes": ""})
            if name not in ejercicios_uniq:
                ejercicios_uniq[name] = {
                    "nombre":         name,
                    "grupo_muscular": cur_muscle,
                    "tipo":           "Fuerza",
                    "series":         int(sets),
                    "repeticiones":   reps,
                    "descripcion":    "",
                }
            partial = ""
        else:
            # Could be first half of a wrapped name; stash only if it doesn't already
            # look like a complete exercise (avoids building bogus partials forever)
            partial = line if not EX.match(line) else ""

    # Flush last session
    if cur_day is not None and cur_exs:
        days.append({"day": cur_day, "muscleGroup": cur_muscle, "exercises": cur_exs})

    if not days:
        return None

    return {
        "rutinas": [{
            "name":             "Plan de Entrenamiento Importado",
            "category":         "General",
            "difficulty":       "Intermedio",
            "duration_minutes": plan_duration,
            "description":      "Plan importado desde archivo PDF/Excel",
            "days":             days,
        }],
        "ejercicios": list(ejercicios_uniq.values()),
    }


# ─── Verificación de disponibilidad ──────────────────────────────────────────

def check_ollama_ready() -> tuple[bool, str]:
    """Verifica que Ollama esté activo y el modelo configurado esté disponible."""
    try:
        r = _requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        r.raise_for_status()
        modelos = [m["name"] for m in r.json().get("models", [])]
        modelo_ok = any(
            OLLAMA_MODEL in m or m.startswith(OLLAMA_MODEL.split(":")[0])
            for m in modelos
        )
        if not modelo_ok:
            return False, (
                f"El modelo '{OLLAMA_MODEL}' no está descargado. "
                f"Ejecuta: docker compose exec ollama ollama pull {OLLAMA_MODEL}"
            )
        return True, "ok"
    except _requests.exceptions.ConnectionError:
        return False, (
            "Ollama no está disponible. "
            "Asegúrate de que el servicio esté corriendo: docker compose up -d ollama"
        )
    except Exception as e:
        return False, f"Error verificando Ollama: {e}"


def get_ollama_status() -> dict:
    """Devuelve el estado de Ollama como dict JSON-serializable."""
    try:
        r = _requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        r.raise_for_status()
        modelos = [m["name"] for m in r.json().get("models", [])]
        modelo_ok = any(
            OLLAMA_MODEL in m or m.startswith(OLLAMA_MODEL.split(":")[0])
            for m in modelos
        )
        return {
            "disponible":    True,
            "modelo_activo": modelo_ok,
            "modelo":        OLLAMA_MODEL,
            "modelos":       modelos,
        }
    except Exception:
        return {
            "disponible":    False,
            "modelo_activo": False,
            "modelo":        OLLAMA_MODEL,
            "modelos":       [],
        }


# ─── Llamada al LLM ──────────────────────────────────────────────────────────

def call_ollama(
    system_prompt: str,
    document_text: str,
    max_tokens: int = 4096,
    timeout: int = 270,  # segundos — bajo el límite de gunicorn (300s)
) -> str:
    """
    Envía el documento al LLM local vía Ollama y devuelve el JSON crudo.
    `format='json'` fuerza salida JSON válido — característica nativa de Ollama.

    `timeout` debe ser menor que el gunicorn --timeout (300s) para que el worker
    no sea SIGKILLed antes de que requests pueda devolver el TimeoutError controlado.
    """
    payload = {
        "model":  OLLAMA_MODEL,
        "prompt": f"{system_prompt}\n\nDOCUMENTO A PROCESAR:\n{document_text}",
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,
            "num_predict": max_tokens,
            "top_p":       0.9,
        },
    }
    resp = _requests.post(
        f"{OLLAMA_BASE}/api/generate",
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json().get("response", "") or ""


# ─── Tabla de macros por ingrediente (por 100 g) ─────────────────────────────
# (kcal, proteina_g, carb_g, grasa_g)

_MACROS_DB: dict[str, tuple[float, float, float, float]] = {
    # Proteínas animales
    "pechuga de pollo":      (165, 31.0,  0.0,  3.6),
    "pollo deshebrado":      (165, 31.0,  0.0,  3.6),
    "pollo":                 (165, 31.0,  0.0,  3.6),
    "tilapia":               (96,  20.1,  0.0,  2.0),
    "salmon":                (208, 20.4,  0.0, 13.4),
    "atun en agua":          (116, 25.5,  0.0,  0.8),
    "atun":                  (116, 25.5,  0.0,  0.8),
    "pescado":               (96,  20.0,  0.0,  1.5),
    "clara de huevo":        (52,  11.0,  0.7,  0.2),
    "claras de huevo":       (52,  11.0,  0.7,  0.2),
    "huevo":                 (155,  13.0,  1.1, 11.0),
    "jamon bajo en grasa":   (120,  16.0,  2.0,  5.0),
    "jamon":                 (145,  16.0,  3.5,  7.0),
    "bistec":                (217,  26.0,  0.0, 12.0),
    "res":                   (217,  26.0,  0.0, 12.0),
    "queso panela":          (270,  17.0,  3.0, 21.0),
    "queso cottage":         (98,   11.0,  3.4,  4.3),
    "yogurt griego":         (59,   10.0,  3.6,  0.4),
    "yogur":                 (59,   10.0,  3.6,  0.4),
    # Carbohidratos
    "arroz cocido":          (130,   2.7, 28.0,  0.3),
    "arroz":                 (130,   2.7, 28.0,  0.3),
    "pasta cocida":          (158,   5.8, 31.0,  0.9),
    "pasta":                 (158,   5.8, 31.0,  0.9),
    "pan integral":          (247,   9.0, 41.0,  3.4),
    "pan":                   (265,   9.0, 49.0,  3.2),
    "tortilla de maiz":      (218,   5.7, 46.0,  2.5),
    "tortilla":              (218,   5.7, 46.0,  2.5),
    "tostadas horneadas":    (380,   9.0, 78.0,  2.5),
    "avena en hojuela cruda":(389,  17.0, 66.0,  7.0),
    "avena":                 (389,  17.0, 66.0,  7.0),
    "tortitas de arroz":     (392,   8.0, 82.0,  3.0),
    "elote":                 (86,    3.2, 19.0,  1.2),
    "camote":                (86,    1.6, 20.0,  0.1),
    "papa":                  (77,    2.0, 17.0,  0.1),
    # Grasas saludables
    "aguacate":              (160,   2.0,  9.0, 15.0),
    "guacamole":             (160,   2.0,  9.0, 15.0),
    "aceite de oliva":       (884,   0.0,  0.0,100.0),
    "aceite":                (884,   0.0,  0.0,100.0),
    "almendra":              (579,  21.0, 22.0, 50.0),
    "almendras":             (579,  21.0, 22.0, 50.0),
    "nuez":                  (654,  15.0, 14.0, 65.0),
    "cacahuate":             (567,  26.0, 16.0, 49.0),
    "cacahuates":            (567,  26.0, 16.0, 49.0),
    "crema de cacahuate":    (588,  25.0, 20.0, 50.0),
    "mayonesa":              (680,   1.0,  0.6, 75.0),
    # Verduras
    "espinaca":              (23,    2.9,  3.6,  0.4),
    "espinacas":             (23,    2.9,  3.6,  0.4),
    "nopal cocido":          (22,    1.5,  4.0,  0.3),
    "nopal":                 (22,    1.5,  4.0,  0.3),
    "pepino":                (15,    0.7,  3.6,  0.1),
    "tomate":                (18,    0.9,  3.9,  0.2),
    "jitomate":              (18,    0.9,  3.9,  0.2),
    "tomate cherry":         (18,    0.9,  3.9,  0.2),
    "champiñon":             (22,    3.1,  3.3,  0.3),
    "champiñones":           (22,    3.1,  3.3,  0.3),
    "calabaza":              (17,    1.2,  3.4,  0.1),
    "cebolla":               (40,    1.1,  9.3,  0.1),
    "betabel":               (43,    1.6, 10.0,  0.2),
    "jicama":                (38,    0.7,  8.8,  0.1),
    # Frutas
    "fresa":                 (32,    0.7,  7.7,  0.3),
    "manzana":               (52,    0.3, 14.0,  0.2),
    "mango":                 (60,    0.8, 15.0,  0.4),
    "piña":                  (50,    0.5, 13.0,  0.1),
    "platano":               (89,    1.1, 23.0,  0.3),
    "melon":                 (34,    0.8,  8.2,  0.2),
    "ejote":                 (31,    1.8,  7.1,  0.1),
    # Lácteos
    "leche":                 (61,    3.2,  4.8,  3.3),
    "crema":                 (195,   2.5,  3.4, 20.0),
    # Otros
    "semilla de chia":       (486,  17.0, 42.0, 31.0),
    "chia":                  (486,  17.0, 42.0, 31.0),
    "semilla de linaza":     (534,  18.0, 29.0, 42.0),
    "harina de avena":       (379,  13.0, 68.0,  7.0),
    "proteina en polvo":     (380,  75.0,  5.0,  5.0),
}

_PIECE_G: dict[str, float] = {
    "huevo":         50.0,
    "aguacate":     200.0,
    "manzana":      180.0,
    "platano":      120.0,
    "naranja":      130.0,
    "tortilla":      30.0,
    "tomate":       120.0,
    "jitomate":     120.0,
    "limón":         80.0,
    "pan integral":  35.0,   # slice of bread
    "pan":           35.0,
    "tostada":       12.0,
    "tostadas":      12.0,
    # Frutos secos — piezas pequeñas
    "almendra":       1.2,
    "almendras":      1.2,
    "nuez":           5.0,
    "cacahuate":      0.7,
    "cacahuates":     0.7,
    "semilla":        3.0,
    # Frutas pequeñas
    "fresa":          8.0,
    "uva":            5.0,
    "cereza":         8.0,
}

_CONDIMENTS = {
    "sal", "pimienta", "ajo", "comino", "oregano", "canela",
    "chile", "mostaza", "salsa", "vinagre", "cilantro",
    "limon", "limón", "jugo de limon",
}


def _parse_fraction(s: str) -> float:
    """'1 1/2' → 1.5,  '2/3' → 0.667,  '1.5' → 1.5"""
    import re as _re
    s = s.strip()
    m = _re.match(r"^(\d+)\s+(\d+)/(\d+)$", s)
    if m:
        return int(m.group(1)) + int(m.group(2)) / int(m.group(3))
    m = _re.match(r"^(\d+)/(\d+)$", s)
    if m:
        return int(m.group(1)) / int(m.group(2))
    return float(s.replace(",", "."))


def _qty_to_grams(qty: float, unit: str, ingredient: str) -> float:
    u = unit.lower()
    if u in ("gramos", "gramo", "g"):
        return qty
    if u in ("kg",):
        return qty * 1000
    if u in ("taza", "tazas"):
        return qty * 240
    if u in ("cucharada", "cucharadas"):
        return qty * 15
    if u in ("cucharadita", "cucharaditas"):
        return qty * 5
    if u in ("ml",):
        return qty
    if u in ("pieza", "piezas", "mitad", "mitades"):
        ing_low = ingredient.lower()
        for key, grams in _PIECE_G.items():
            if key in ing_low:
                return qty * grams
        return qty * 80   # default pieza
    if u in ("rebanada", "rebanadas"):
        ing_low = ingredient.lower()
        for key, grams in _PIECE_G.items():
            if key in ing_low:
                return qty * grams
        return qty * 20   # default rebanada ≈ slice
    if u in ("lata", "latas"):
        return qty * 170
    return qty


def _lookup_macros(nombre: str) -> tuple[float, float, float, float] | None:
    n = nombre.lower()
    if n in _MACROS_DB:
        return _MACROS_DB[n]
    for key in sorted(_MACROS_DB.keys(), key=len, reverse=True):
        if key in n:
            return _MACROS_DB[key]
    return None


def _parse_ingredient_line(line: str) -> dict | None:
    import re as _re
    text = line.lstrip("*").strip().rstrip(".")
    text_low = text.lower()

    if any(c in text_low for c in _CONDIMENTS):
        if not _re.match(r"^[\d\s,./]+\s+\w", text):
            return None

    m = _re.match(
        r"^([\d\s,./]+)\s+"
        r"(gramos?|g\b|tazas?|cucharadas?|cucharaditas?|piezas?|rebanadas?|"
        r"latas?|mitades?|ml|kg)\s*"
        r"(?:de\s+)?(.+)$",
        text, _re.IGNORECASE,
    )
    if m:
        qty_str = m.group(1).strip()
        unit    = m.group(2).strip()
        nombre  = m.group(3).strip()
    else:
        qty_str, unit, nombre = "al gusto", "", text

    calorias = prot = carb = fat = None
    if qty_str != "al gusto":
        try:
            qty    = _parse_fraction(qty_str)
            grams  = _qty_to_grams(qty, unit, nombre)
            macros = _lookup_macros(nombre)
            if macros:
                f        = grams / 100.0
                calorias = round(macros[0] * f)
                prot     = round(macros[1] * f, 1)
                carb     = round(macros[2] * f, 1)
                fat      = round(macros[3] * f, 1)
        except Exception:
            pass

    return {
        "nombre_alimento": nombre,
        "cantidad":        qty_str,
        "unidad":          unit,
        "calorias":        calorias,
        "proteinas_g":     prot,
        "carbohidratos_g": carb,
        "grasas_g":        fat,
    }


# ─── Parser determinístico para planes alimenticios en PDF tabular ────────────

def parse_diet_plan_from_pdf(contenido: bytes) -> dict | None:
    """
    Extrae un plan alimenticio y sus recetas de un PDF tabular (formato semanal).

    Detecta por página:
      - Tipo de comida (Desayuno / Colación / Comida / Colación 2 / Cena)
      - Nombre del platillo por día (líneas antes del primer '*')
      - Ingredientes ('* cantidad unidad ingrediente')
      - Imagen del platillo (base64 JPEG, extraída directamente del PDF)

    Returns {"plan": {...}, "recetas": [...]} o None si no reconoce el formato.
    """
    import io as _io
    import base64 as _b64
    import unicodedata as _ud
    import pdfplumber
    try:
        from PIL import Image as _PILImage
        _HAS_PIL = True
    except ImportError:
        _HAS_PIL = False

    def _nd(s: str) -> str:
        return "".join(
            c for c in _ud.normalize("NFD", s.lower())
            if _ud.category(c) != "Mn"
        )

    _DAY_KEYS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]

    _MEAL_MAP: dict[str, tuple[str, str]] = {
        "colacion 2": ("Colación 2", "17:00"),
        "colacion 1": ("Colación 1", "10:30"),
        "desayuno":   ("Desayuno",   "08:00"),
        "colacion":   ("Colación",   "10:30"),
        "comida":     ("Comida",     "14:00"),
        "merienda":   ("Merienda",   "17:00"),
        "cena":       ("Cena",       "20:00"),
    }

    def _img_b64(stream) -> str | None:
        if not _HAS_PIL or stream is None:
            return None
        try:
            data = stream.get_data()
            pil  = _PILImage.open(_io.BytesIO(data)).convert("RGB")
            buf  = _io.BytesIO()
            pil.save(buf, format="JPEG", quality=72)
            return "data:image/jpeg;base64," + _b64.b64encode(buf.getvalue()).decode()
        except Exception:
            return None

    days_data: dict[str, list[dict]]  = {d: [] for d in _DAY_KEYS}
    recipe_map: dict[str, dict]       = {}

    try:
        with pdfplumber.open(_io.BytesIO(contenido)) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                if not tables:
                    continue
                table = tables[0]

                # 1. Encabezado de días
                header_ri = None
                col_day: dict[int, str] = {}
                for ri, row in enumerate(table):
                    for ci, cell in enumerate(row):
                        if not cell:
                            continue
                        nd = _nd(str(cell).strip())
                        if nd in _DAY_KEYS:
                            header_ri = ri
                            col_day[ci] = nd
                    if header_ri is not None:
                        break

                if not col_day:
                    continue

                # 2. Imágenes de platillos (excluir logo: Image6)
                dish_imgs = sorted(
                    [img for img in page.images
                     if img["x1"] - img["x0"] > 50
                     and img.get("stream") is not None
                     and img.get("name", "") != "Image6"],
                    key=lambda i: i["x0"],
                )
                sorted_cols = sorted(col_day.keys())
                col_img: dict[int, str | None] = {
                    ci: (_img_b64(dish_imgs[idx]["stream"]) if idx < len(dish_imgs) else None)
                    for idx, ci in enumerate(sorted_cols)
                }

                # 3. Tipo de comida de la página
                meal_key = None
                for ri in range(header_ri + 1, len(table)):
                    row = table[ri]
                    label = _nd(str(row[0] or "").strip()) if row[0] else ""
                    for mk in _MEAL_MAP:          # ya están en orden longest-first
                        if mk in label:
                            meal_key = mk
                            break
                    if meal_key:
                        break
                if not meal_key:
                    pt = _nd(page.extract_text() or "")
                    for mk in _MEAL_MAP:
                        if mk in pt:
                            meal_key = mk
                            break
                if not meal_key:
                    continue

                meal_nombre, meal_hora = _MEAL_MAP[meal_key]

                # 4. Texto por columna (filas post-encabezado)
                col_texts: dict[int, list[str]] = {ci: [] for ci in col_day}
                for ri in range(header_ri + 1, len(table)):
                    row = table[ri]
                    for ci in col_day:
                        if ci < len(row) and row[ci] and str(row[ci]).strip():
                            col_texts[ci].append(str(row[ci]).strip())

                # 5. Parsear cada columna
                for ci, day_key in col_day.items():
                    raw = " ".join(col_texts.get(ci, []))
                    if not raw.strip():
                        continue

                    parts         = raw.split("*")
                    nombre_receta = " ".join(parts[0].split())
                    if not nombre_receta:
                        continue

                    ingredientes: list[dict] = []
                    for ing_raw in parts[1:]:
                        ing_raw = ing_raw.strip().rstrip(".")
                        if not ing_raw or len(ing_raw) < 2:
                            continue
                        parsed = _parse_ingredient_line("* " + ing_raw)
                        if parsed:
                            ingredientes.append(parsed)
                        else:
                            low = ing_raw.lower()
                            if not any(c in low for c in _CONDIMENTS):
                                ingredientes.append({
                                    "nombre_alimento":  ing_raw,
                                    "cantidad":         None,
                                    "unidad":           None,
                                    "calorias":         None,
                                    "proteinas_g":      None,
                                    "carbohidratos_g":  None,
                                    "grasas_g":         None,
                                })

                    def _tot(k: str) -> float | None:
                        vals = [i[k] for i in ingredientes if i.get(k) is not None]
                        return round(sum(vals), 1) if vals else None

                    kcal  = _tot("calorias")
                    prot  = _tot("proteinas_g")
                    carb  = _tot("carbohidratos_g")
                    fat   = _tot("grasas_g")
                    imagen = col_img.get(ci)

                    days_data[day_key].append({
                        "meal_key":      meal_key,
                        "nombre":        meal_nombre,
                        "hora":          meal_hora,
                        "nombre_receta": nombre_receta,
                        "ingredientes":  ingredientes,
                        "kcal":  kcal, "prot": prot,
                        "carb":  carb, "fat":  fat,
                    })

                    rk = nombre_receta.lower()
                    if rk not in recipe_map:
                        recipe_map[rk] = {
                            "nombre":          nombre_receta,
                            "calorias":        int(kcal) if kcal else None,
                            "proteinas_g":     prot,
                            "carbohidratos_g": carb,
                            "grasas_g":        fat,
                            "ingredientes":    ingredientes,
                            "imagen":          imagen,
                            "fuente":          "pdf_import",
                        }

    except Exception:
        return None

    if not recipe_map:
        return None

    # Construir plan v2
    semana_dias = []
    for day_key in _DAY_KEYS:
        meals = days_data[day_key]
        if not meals:
            continue
        comidas = [{
            "nombre": m["nombre"],
            "hora":   m["hora"],
            "items":  [{
                "nombre_alimento": m["nombre_receta"],
                "cantidad":        None,
                "unidad":          None,
                "calorias":        m["kcal"],
                "proteinas_g":     m["prot"],
                "carbohidratos_g": m["carb"],
                "grasas_g":        m["fat"],
            }],
        } for m in meals]
        semana_dias.append({"dia": day_key, "comidas": comidas})

    # Calorías meta = promedio de totales diarios
    day_totals = [
        sum(m["kcal"] for m in days_data[d] if m.get("kcal"))
        for d in _DAY_KEYS if days_data[d]
    ]
    daily_kcal = round(sum(t for t in day_totals if t) / len([t for t in day_totals if t])) \
                 if any(day_totals) else None

    plan = {
        "nombre":               "Plan Alimenticio Importado",
        "objetivo":             "mantenimiento",
        "duracion_semanas":     1,
        "calorias_meta":        daily_kcal,
        "proteinas_meta_g":     None,
        "carbohidratos_meta_g": None,
        "grasas_meta_g":        None,
        "notas":                None,
        "fuente":               "ia_import",
        "semanas": [{"numero": 1, "notas": None, "dias": semana_dias}],
    }

    return {"plan": plan, "recetas": list(recipe_map.values())}
